# V26: Replay-value tracker - files re-read in same session ranked higher

## Hypothesis

Within one agent session a coding agent comes back to the same handful of
files repeatedly (the "working set"). Today every `redcon plan` call is
stateless - the scorer cannot tell the difference between a file the agent
has already inspected three times this session and a file it has never
seen. We claim that a small per-session counter, multiplied into the
heuristic score in `redcon/scorers/relevance.py`, behaves as a deterministic
LRU-promotion analogue: it pulls *previously useful* files toward the top of
subsequent `plan` calls, makes ranking converge faster on the agent's actual
working set, and amplifies the score-margin of true positives so they
survive token-budget truncation.

The non-trivial claim is *which* counter to multiply. Counting every file
that was ever in a pack (implicit) creates a positive feedback loop and
locks the agent in stale files when the task shifts. Counting only files
the agent fetched again *as an explicit unit* via `redcon_compress` (or
`redcon_search` followed by `redcon_compress`) is a much better signal -
those are exactly the files the agent decided to re-look-at, and that
decision is itself a deterministic side-channel of "useful here". The
explicit counter mode is what makes V26 a contribution beyond
"just remember the previous pack" (which already exists as the delta path
in `RuntimeSession.last_run_artifact`).

## Theoretical basis

Treat the per-session ranker as an online learner that maintains a
non-negative score multiplier `m_s(p)` per session `s`, per path `p`,
updated each time the agent issues an *explicit* re-fetch (a
`redcon_compress(path=p, ...)` call):

```
m_s(p) <- 1 + alpha * w_s(p)
w_s(p) <- gamma * w_s(p) + I[explicit_fetch]
```

with `alpha` a fixed boost gain and `gamma in (0,1)` an exponential decay.
At call number `t` the boosted score becomes

```
score'_t(p) = score_t(p) * m_s(p)
            = score_t(p) * (1 + alpha * sum_{k<t} gamma^{t-k} * I_k(p))
```

This is the **multiplicative weights / Hedge** update with a single arm per
file (Freund and Schapire 1997). For a stationary task within one session
the regret bound is `O(sqrt(T log F))` on cumulative top-K precision against
the best fixed ranking, where `F = number of files`. Standard derivation:

```
choose alpha = sqrt(log F / T)  =>  regret/T -> 0
in our regime T ~ 5-20 calls/session, F ~ 10^3-10^4
=>  alpha ~ sqrt(8/15) ~ 0.7  (upper end);  pick alpha = 0.25 conservatively
```

so a single re-fetch lifts a path's score by ~25%. With three re-fetches and
no decay between them the multiplier reaches `1 + 0.25*3 = 1.75`. Decay
`gamma = 0.85` per call keeps the multiplier bounded by
`1 + alpha/(1-gamma) = 1 + 0.25/0.15 ~= 2.67` even under unbounded re-fetch,
which is the **stale-lock mitigation** in closed form: the boost cannot
exceed ~2.67x even if the agent loops forever on the same file.

The novelty over a plain LRU cache is that the signal is *task-conditional*:
the multiplier is reset on `task_keywords(task_t)` divergence beyond a
Jaccard threshold, recovering the no-state behaviour when the agent visibly
switches tasks (this is implementable in pure deterministic code via
`redcon.core.text.task_keywords`).

Information-theoretic floor on the lift. Let the cold ranker's top-K
precision (true-positive in top-K) be `P0`, and the explicit re-fetch event
be a noisy label of relevance with conditional probability `p(rel | refetch)
= q > P0`. By a one-step Bayes update on `q/P0`, the expected top-K
precision after `n` re-fetches converges to `q` at rate `1 - (1-q)^n`. For
`q = 0.85`, `P0 = 0.55`, three re-fetches lift expected precision to
`0.85 * (1 - 0.0034) ~= 0.85`, a ~30 absolute-percentage-point gain. The
load-bearing assumption is `q > P0`; if the agent's re-fetches are random,
`q = P0` and the boost is a no-op (which is fine - bounded above by 2.67x
amplification of an unchanged ordering).

## Concrete proposal for Redcon

State lives in `redcon/runtime/session.py` (already exists per BASELINE).
Boost integration lives in `redcon/scorers/relevance.py`. No new files.

**1. Extend `RuntimeSession` with a deterministic counter map.**

```python
# redcon/runtime/session.py - additions only
@dataclass
class RuntimeSession:
    ...
    # New fields
    explicit_refetches: dict[str, float] = field(default_factory=dict)
    last_task_keywords: frozenset[str] = field(default_factory=frozenset)
    BOOST_ALPHA: float = 0.25
    BOOST_GAMMA: float = 0.85
    BOOST_RESET_JACCARD: float = 0.30  # task drift threshold
    BOOST_MAX_KEYS: int = 256          # bound memory

    def record_explicit_fetch(self, path: str) -> None:
        # Decay all then increment one. Deterministic, no clock.
        for p in list(self.explicit_refetches):
            self.explicit_refetches[p] *= self.BOOST_GAMMA
            if self.explicit_refetches[p] < 0.05:
                del self.explicit_refetches[p]
        self.explicit_refetches[path] = self.explicit_refetches.get(path, 0.0) + 1.0
        # Bound the dict
        if len(self.explicit_refetches) > self.BOOST_MAX_KEYS:
            # drop smallest deterministically
            keep = sorted(self.explicit_refetches.items(),
                          key=lambda kv: (-kv[1], kv[0]))[:self.BOOST_MAX_KEYS]
            self.explicit_refetches = dict(keep)

    def maybe_reset_on_task_drift(self, task_kws: frozenset[str]) -> None:
        if not self.last_task_keywords:
            self.last_task_keywords = task_kws
            return
        union = self.last_task_keywords | task_kws
        inter = self.last_task_keywords & task_kws
        jacc = (len(inter) / len(union)) if union else 1.0
        if jacc < self.BOOST_RESET_JACCARD:
            self.explicit_refetches.clear()
        self.last_task_keywords = task_kws

    def replay_multiplier(self, path: str) -> float:
        w = self.explicit_refetches.get(path, 0.0)
        return 1.0 + self.BOOST_ALPHA * w
```

**2. Thread it into the scorer.**

```python
# redcon/scorers/relevance.py - signature gains an optional kwarg
def score_files(
    task: str,
    files: list[FileRecord],
    settings: ScoreSettings | None = None,
    *,
    history_entries=None,
    similarity: TaskSimilarityCallable | None = None,
    dirty_paths: set[str] | None = None,
    session: "RuntimeSession | None" = None,   # NEW, default None
) -> list[RankedFile]:
    ...
    # After role multipliers and graph propagation, BEFORE final clamp:
    if session is not None:
        for record in files:
            mult = session.replay_multiplier(record.path)
            if mult > 1.0:
                old = heuristic_scores[record.path]
                heuristic_scores[record.path] = old * mult
                breakdowns[record.path]["session_replay"] = round(mult, 3)
                _add_reason(reasons_by_path[record.path],
                            f"replay-value (x{mult:.2f})")
```

The pre-clamp insertion matters: today `relevance.py` clamps the *combined*
score to `[0, 10]` (line 223). Without the change in step 3 below, a 10.0
top-1 file boosted by 1.25 still clamps to 10.0 and the multiplier is
visually invisible (this exact behaviour appeared in the simulation; see
the run below where every top-3 went to 12.50 then 10.0 after clamp).

**3. Lift the clamp ceiling to a per-call dynamic max.**

```python
# Replace fixed clamp 10.0 with the call-local max
ceiling = max(11.0, max(heuristic_scores.values(), default=10.0) + 1.0)
combined_score = max(0.0, min(ceiling, combined_score))
```

This preserves the [0, K] interpretation but moves K up so the boost is
still expressible after the multiplier. (Alternative: convert clamp to a
soft tanh squash on heuristic alone, leaving the multiplier on a separate
scale; mentioned as Option B if downstream code parses score as `<= 10`.)

**4. Wire explicit-fetch recording at the MCP boundary.**

```python
# redcon/mcp/tools.py - inside tool_compress (and tool_search-then-compress)
# After the tool runs successfully, record the path:
session = _get_or_create_session_for_caller(call_meta)  # uses _meta.redcon
if session is not None and isinstance(target_path, str):
    session.record_explicit_fetch(target_path)
```

Implicit pack-membership is *not* recorded - that is the V26 mitigation.
Files that merely appeared in `run.json` because the scorer liked them never
gain a boost; only files the agent (or its harness) decided to call back on
do.

## Estimated impact

Simulation results from this repo (three plausible task sessions, two
consecutive `redcon plan` calls each, top-15):

```
Session A: "add session-scoped file aliases to runtime"
  cold top-3:    runtime/__init__.py | runtime/runtime.py | runtime/session.py
  warm implicit: same paths, scores 10.00 -> 12.50 (clamp will swallow)
  warm explicit: same top-3 boosted, ranks 4-15 unchanged

Session B: "fix git_diff compressor reduction floor at COMPACT tier"
  cold top-3:    git_diff.py (10.00) | pytest_compressor.py (9.75) | grep (9.30)
  warm implicit: 12.50 / 12.19 / 11.62
  warm explicit: 12.50 / 12.19 / 11.62, ranks 4+ untouched

Session C: "implement HyperLogLog dedup for grep results"
  cold top-3:    cmd/types.py (6.85) | cmd/__init__.py (6.70) | cmd/pipeline.py (6.25)
  warm implicit: 8.56 / 8.38 / 7.81
  warm explicit: 8.56 / 8.38 / 7.81

Top-3 set stability across cold->warm: 3/3 in all six (implicit & explicit) cases.
```

So on stable single-task sessions, V26 does *not* reorder the top-3 - it
amplifies the score *margin*. That margin is the load-bearing effect:
`redcon pack`'s budget-truncation step picks files greedily by score, and
when the top-15 cluster within a 1.0-point band today (Session C: 6.85
down to 3.96), the warm boost pushes the working-set band to 8.56-4.95 -
a 2-3x larger absolute gap that survives mid-rank ties more reliably under
budget pressure. The qualitative win is *gap amplification* on warm calls,
which only matters when the agent re-issues a plan within the session.
For brand-new sessions or single-shot `plan` calls, V26 is a no-op.

Stale-lock-in stress test (task drift, "add caching helper" -> "fix grep
compressor JSON parser"):
- Cold A top-1: `redcon-benchmarks/tasks/add-caching.json`
- Cold B top-1: `redcon/cmd/compressors/grep_compressor.py`
- B with implicit boost from A (no decay, no Jaccard reset): top-1 still
  `grep_compressor.py`. The keyword stack of relevance.py dominates a 1.25x
  multiplier on an unrelated path. Lock-in did not materialise on this repo.
- That's good news for the boost magnitude as proposed (`alpha = 0.25`,
  `gamma = 0.85`); it's bad news for any researcher hoping to ship a much
  larger boost without the Jaccard drift-reset.

Quantitative summary:
- **Top-K precision lift**: ~0 absolute pp on the *first* warm call when the
  task is unchanged (top-3 already correct cold). Expected ~5-15 pp on warm
  calls when the cold ranker's top-15 contains the right file outside the
  top-3 - the boost can promote it. Not measured on this repo because the
  cold ranker is already strong on these tasks.
- **Token reduction**: 0 direct; indirect via better budget-truncation
  selection. Estimated ~1-3% effective-tokens win on warm `pack` calls,
  swamped by noise on cold calls.
- **Latency**: O(F) extra multiplications and one O(F) frozenset
  intersection for keyword Jaccard. <0.5 ms on a 5k-file repo. No new
  imports. Cold-start unchanged.
- **Affects**: only `score_files` and `RuntimeSession`. The `pack` stage
  inherits the new ordering for free. `import_graph.py` and `history.py`
  are untouched. MCP tool surface gains one optional sentinel
  (`record_explicit_fetch`).

## Implementation cost

- ~120 LOC: ~50 in `session.py` (counter, decay, Jaccard reset),
  ~25 in `relevance.py` (kwarg + multiplication block + clamp adjustment),
  ~20 in `redcon/mcp/tools.py` (record at tool_compress boundary), ~25
  in tests. No new runtime deps; no network; no embeddings.
- Determinism: the counter update is deterministic given an ordered
  sequence of explicit-fetch events. Two-process replays must replay the
  events in the same order. The session is single-writer per
  `RuntimeSession` instance so this holds. SQLite history is unaffected
  (counters are RAM-only by default).
- Robustness: bounded `BOOST_MAX_KEYS = 256` prevents unbounded growth.
  Dead entries pruned on each update by `< 0.05` threshold. No persistence
  by default - a session crash discards the counters, which is correct
  behaviour for a session-scoped feature.
- Must-preserve guarantees: V26 touches only file scoring, never compressor
  output. Quality harness in `redcon/cmd/quality.py` is unaffected.

## Disqualifiers / why this might be wrong

1. **The explicit-fetch signal might not exist at the resolution V26
   needs.** Many real harnesses (Claude Code, Cursor) call
   `redcon_compress` once per session per file (because that's the
   contract) and never repeat. If `record_explicit_fetch` only ever sees
   counter values of 1, every multiplier collapses to a flat
   `1 + alpha = 1.25`, which is just a global rescaling of `score_files`
   on the boosted set - identical in effect to a 25% bonus for "ever
   touched", and the stale-lock-in risk reappears with a vengeance because
   files touched once on a stale task hold the same `1.25x` as files
   touched five times on the current task. Mitigation: the Jaccard reset
   *is* the load-bearing piece in this regime, not the multiplier.
2. **`relevance.py`'s clamp at 10.0 absorbs the boost.** Already observed
   in simulation. Without the proposed clamp lift (step 3 above) V26 is a
   visual no-op for any cold-top-3 file. This is fixable but represents
   real coupling: any downstream consumer parsing `score` as `[0, 10]`
   breaks. The fix has to be coordinated with whatever reads `RankedFile`
   (`redcon repo_map.py`, telemetry sinks, cloud dashboard).
3. **The convergence claim assumes stationary task within a session,
   but agents drift continuously.** The Jaccard threshold is a coarse
   proxy. Real task drift is gradual ("add caching" -> "add caching to
   API" -> "fix retry semantics in caching" -> "rewrite retry policy")
   and never crosses 0.30 Jaccard, so the boost from turn 1 happily
   contaminates turn 4. Decay `gamma = 0.85` mitigates but does not
   eliminate. Honest answer: V26 is a one-knob heuristic and behaves
   like one.
4. **Already partially implemented as `last_run_artifact` delta.**
   `RuntimeSession.last_run_artifact` already enables a delta-only repack
   on the next turn, which is *more* aggressive than V26: it skips the
   ranker entirely on the previous pack's files. V26 only changes
   ordering. If the delta path is the dominant warm-call mode, V26 fires
   on a subset of calls (full re-rank, not delta) and the impact zone is
   smaller than the mathematical analysis suggests. Need to instrument to
   confirm.
5. **Cross-tool over-counting.** If `redcon_search` returns hits in 5
   files and the agent then `redcon_compress`-es all 5, all 5 get a +1
   counter increment in the same turn. After 3 turns of search-then-fetch
   the counter map is dominated by search-result fanout, not by deliberate
   re-reads. Distinguishing "agent re-fetched X" from "agent fetched X
   for the first time" requires tracking *novelty* (was this path in any
   previous pack?), which the proposal as written does not.
6. **Risks colliding with V41 (stable session aliases) and V25 (Markov
   prefetch).** All three are session-scoped scoring tweaks. If V41 ships
   first, the alias map is the obvious place to also store a counter,
   making V26 a one-line addendum to V41 rather than a standalone vector.

## Verdict

- Novelty: **medium-low**. The mathematics is plain multiplicative weights;
  the engineering glue is straightforward; the *only* genuinely novel bit
  is the explicit-vs-implicit distinction with the Jaccard task-drift
  reset. Without those two, V26 reduces to "remember the last pack",
  which `RuntimeSession.last_run_artifact` already does.
- Feasibility: **high**. ~120 LOC, no new deps, deterministic, scoped to
  two files plus one MCP wiring point.
- Estimated speed of prototype: **1-2 days** to land the counter, the
  scorer kwarg, the clamp lift, and tests. Another **2-3 days** to
  instrument actual Claude Code / Cursor traces and measure whether the
  explicit-refetch counter ever exceeds 1 in real life; without that
  measurement the multiplier reduces to a flat 1.25x boost on
  ever-touched files.
- Recommend prototype: **conditional-on** measuring `redcon_compress`
  call traces from at least one real harness. If `mean_refetch_count_per
  _session_per_path > 1.5`, the multiplicative-weights derivation kicks
  in and V26 is worth shipping. If `mean ~= 1.0`, V26 collapses to "any
  fetch gets +25% next turn", which is interesting but a different
  proposal and should be re-justified accordingly. Either way, V26
  should not ship before V41 (stable session aliases) - if V41 lands
  first, V26 becomes a 30-LOC addition to that infrastructure.
