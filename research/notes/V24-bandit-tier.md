# V24: Multi-armed bandit across compression tiers using "did agent then ask for full" as feedback signal

## Hypothesis

`select_level` in `redcon/cmd/budget.py` is a deterministic threshold function
over (`raw_tokens`, `remaining_tokens`, `max_output_tokens`, `quality_floor`).
It treats the choice between VERBOSE / COMPACT / ULTRA as a fixed staircase
calibrated by hand on the M9 benchmark corpus (`_VERBOSE_RATIO = 1.0`,
`_COMPACT_RATIO = 0.15`, `_BUDGET_SHARE = 0.30`). The claim of V24 is that
these three constants are jointly suboptimal per-(repo, command-schema,
task-type) cell, and that the **next agent action** observed after a
`redcon_run` provides a free reward signal that lets us close the loop.
Specifically: if the agent's NEXT call (within the same session) is a
re-fetch of the same logical content at higher detail, the previous tier
choice was too aggressive (negative reward); if the agent moves on to a
different task surface (different command, different paths, different
keyword set), the tier was at least sufficient (positive reward, modulated
by the token cost the tier paid). A Thompson-sampling allocator over
(compressor x tier) cells, fed by this signal, learns the per-cell
rate at which COMPACT is "good enough" and shifts borderline cases up or
down accordingly.

The subtle part: an online bandit is non-deterministic by construction,
which violates BASELINE constraint #1. The proposal must therefore split
the bandit into a **learning phase** (offline, telemetry-driven, opt-in)
and a **policy phase** (deterministic table lookup, shipped statically).
This yields the bandit's wins without sacrificing same-input-same-output.

## Theoretical basis

Treat each compression as a contextual bandit pull. Context
`x = (schema, raw_tok_bucket, remaining_tok_bucket, repo_id, task_kind)`.
Arms `a in {VERBOSE, COMPACT, ULTRA}`. Reward defined post-hoc, two
components:

```
r(x, a) = -tokens_emitted(x, a) / max_output_tokens
         - LAMBDA * 1[refetch_within_K_calls(same_content)]
```

The first term is the deterministic cost we already know; the second is
the new signal. `LAMBDA` is the implicit penalty for an avoidable round
trip plus the tokens the re-fetch will spend. From V09's break-even
derivation, a wasted re-fetch costs 200-1500 tokens on diff/grep, so
`LAMBDA` calibrated against `max_output_tokens=2000` lands in `[0.1, 0.75]`.

Thompson sampling: maintain a Beta(alpha_a, beta_a) per cell over the
"agent did NOT re-fetch" event, plus a Gaussian over emitted tokens.
At decision time draw `theta_a ~ Beta(alpha_a, beta_a)` and pick
`argmax_a [theta_a * B - cost_a / max_output]` where B is the
re-fetch penalty in token units. Beta-Bernoulli regret bound
(Lattimore-Szepesvari Thm 36.2):

```
E[Regret_T] = O( sum_{a != a*} (Delta_a / KL(p_a, p_a*)) * log T )
            <= sum_{a != a*} 32 / Delta_a * log T   (sub-Gaussian bound)
```

For three arms with realistic gaps `Delta = 0.05-0.20` per cell and
`T = 1000` calls per cell-week, expected regret is bounded by
~2-15 mistakes per cell over the learning window. With ~(11 schemas) x
(4 raw-tok buckets) x (3 remaining-tok buckets) = 132 cells, a full
sweep of regret-tolerant exploration is tractable inside one
deployment-week's traces, but only at instrumented sites (the
unconditional online learning case violates determinism).

Why a contextual bandit and not full RL: the "next call" is observed
within seconds, the action space is 3, and there is no long-horizon
credit assignment (each tier choice is independent of the previous,
modulo session-scoped state which Redcon currently does not retain).
Bandit is the right tool; using DQN here would be cargo-culting.

Lower bound on attainable improvement: define `eps` = fraction of calls
where the bandit's optimal arm differs from the threshold function's
arm (the *disagreement rate*). On those calls expected savings are
`E[r* - r_heuristic | disagreement]`. We have no direct empirical
distribution yet, but two analytic anchors:

- `_COMPACT_RATIO = 0.15` is the *average* across compressors; actual
  ratio ranges from 0.03 (git_diff) to 0.66 (ls_-R). A threshold tuned
  to 0.15 over-spends budget on git_diff (could go ULTRA more often)
  and under-spends on ls_-R (must go ULTRA but threshold says
  COMPACT). Two of eleven schemas are >= 25 pp from the average.
- `_BUDGET_SHARE = 0.30` ignores task-kind: a `git status` call inside
  a multi-step debug task should get more share than the 27th `find .`
  call inside the same session.

Plug rough numbers: 2/11 schemas mis-tiered for ~30% of calls, expected
loss ~ (1500-200)/2 = 650 tokens per re-fetch they trigger, base
re-fetch rate 0.2-0.4 (per V09). Per-call expected gain on the
mis-tiered subset: 0.3 * 0.3 * 650 ~= 60 tokens. At session level with
20 mis-tiered calls in 100, ~1200 tokens. That matches the BASELINE
"breakthrough" bar (>= 5pp on >=1 compressor) for at least the worst
two schemas.

## Concrete proposal for Redcon

Three pieces, separated by determinism risk.

### 1. Reward-signal capture in run history (deterministic, mandatory)

Today `redcon/cmd/history.py::record_run` writes one `run_history_cmd`
row per call but has **no link between consecutive calls**. To detect
"agent re-fetched the same content", we need at minimum:

- A `session_id` column populated by the MCP/CLI surface (process pid +
  start time, or a caller-provided id). Already partially present in
  the file-side `run_history` table via `workspace`, but absent on the
  cmd side.
- A `content_fingerprint` column: a hash of the *logical target* of
  the call, NOT the canonical argv. Example for git_diff:
  `sha1(sorted(file_paths_in_diff))`; for grep:
  `sha1(pattern + sorted(matched_paths))`. This must be derivable
  from the canonical typed result the compressor already builds,
  reusing the same dataclasses V09 leans on.
- A `parent_call_id` column: nullable foreign key into the same table.
  Populated when the current call's `content_fingerprint` overlaps
  >=80% with the previous call's *and* the time delta < 60s *and*
  the current level is more verbose than the previous.

Schema delta:

```sql
ALTER TABLE run_history_cmd ADD COLUMN session_id TEXT NOT NULL DEFAULT '';
ALTER TABLE run_history_cmd ADD COLUMN content_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE run_history_cmd ADD COLUMN parent_call_id INTEGER;
ALTER TABLE run_history_cmd ADD COLUMN refetched_by INTEGER;  -- inverse link, set on next-call write
CREATE INDEX idx_cmd_session ON run_history_cmd(session_id, generated_at);
CREATE INDEX idx_cmd_fp ON run_history_cmd(content_fingerprint);
```

Bumps `SQLITE_HISTORY_FORMAT_VERSION` to 3 (the file-side schema
already lives at 2 in `redcon/cache/run_history_sqlite.py` line 19;
this is independent of the cmd-side table but the version bump
discipline is the same). All new columns nullable / defaulted, so
existing dashboards and `recent_runs` keep working.

This piece is **fully deterministic**: the fingerprint is a pure
function of the canonical typed result, the session id is provided by
the caller (or derived from pid+start which is only used for grouping,
never for compression decisions), and `parent_call_id` is computed
on insert via a query against the just-completed prior row in the
same session. No randomness anywhere.

### 2. Offline bandit trainer (opt-in CLI, not on hot path)

A new module `redcon/cmd/bandit_trainer.py` (out of compression hot
path; never imported from `pipeline.py`). API:

```python
def train_static_policy(
    history_db: Path,
    *,
    min_pulls_per_cell: int = 30,
    out_path: Path = Path("redcon/cmd/policy_table.json"),
) -> PolicyTable:
    """
    Read run_history_cmd, replay calls offline, fit Beta posteriors per
    (schema, raw_tok_bucket, remaining_tok_bucket) cell, write a frozen
    policy table. Pure offline batch; deterministic given the same DB
    snapshot (we sort by id and use a fixed RNG seed = 0 for tie-breaks).
    """

def derive_reward(row, next_row) -> float:
    refetched = (
        next_row is not None
        and next_row["parent_call_id"] == row["id"]
        and _level_rank[next_row["level"]] > _level_rank[row["level"]]
    )
    return -row["compressed_tokens"] / MAX_OUT - LAMBDA * float(refetched)
```

Pseudo-code for the Thompson update (offline, seeded):

```python
posteriors: dict[Cell, dict[Tier, BetaParams]] = defaultdict(_init_beta)
for row, next_row in pairwise(rows_by_session):
    cell = bucket(row.schema, row.raw_tokens, row.remaining_tokens)
    refetched = is_refetch(row, next_row)
    posteriors[cell][row.level].update(success=not refetched)

# Collapse posteriors into a static argmax-of-mean table.
policy = {
    cell: max(tiers, key=lambda t: posteriors[cell][t].mean - cost_of(t))
    for cell, tiers in posteriors.items()
}
write_json(out_path, policy)
```

The trained policy is a frozen JSON table shipped with the package. The
production hot path reads it once at import; lookup is O(1) on
`(schema, raw_bucket, rem_bucket)`. **No RNG at decision time.**

### 3. Policy lookup integrated with `select_level`

`redcon/cmd/budget.py` gets one optional override:

```python
def select_level(raw_tokens, hint, *, schema: str | None = None) -> CompressionLevel:
    if schema is not None and _POLICY_TABLE is not None:
        cell = _bucket(schema, raw_tokens, hint.remaining_tokens)
        recommended = _POLICY_TABLE.get(cell)
        if recommended is not None:
            return _at_least(recommended, hint.quality_floor)
    # ... existing threshold logic as fallback
```

`pipeline.py::compress_command` already knows `output.schema` after
`detect_compressor`, so it threads it through. If the policy table is
absent or the cell is missing, behaviour is byte-identical to today.

### Files touched (sketch)

- `redcon/cmd/history.py`: schema migration helper, fingerprint computation
  hook, parent-call linking on insert. ~60 LOC.
- `redcon/cmd/bandit_trainer.py` (new): offline training module. ~120 LOC.
- `redcon/cmd/budget.py`: optional policy-table override. ~25 LOC.
- `redcon/cmd/policy_table.json` (new, generated): the static policy
  shipped with the package. ~5-50 KB depending on coverage.
- `redcon/cmd/compressors/*`: each compressor exposes a
  `content_fingerprint(parsed) -> str` returning a stable hash of the
  logical targets. ~6 LOC each, 11 compressors.

## Estimated impact

- **Token reduction**: per-call delta is small. The Pareto-mistake
  derivation above gives ~60 tokens expected gain on the ~30% of calls
  in the worst 2/11 schemas; weighted across the full corpus that is
  ~3-6% session-level reduction on traces matching the trained
  distribution. On schemas where the threshold is already optimal
  (`git_diff` at 97% ULTRA, `pytest` at 73.8% COMPACT), the bandit
  will agree with the heuristic almost always and the delta is near
  zero - not a regression, but not a win either. Net BASELINE-bar
  pass: borderline; this is a **distribution-shaping** technique, not
  a fundamental compressor improvement.
- **Latency**: cold path adds one JSON load (~5-50 KB) at import,
  negligible vs lazy-import savings already in place. Hot path adds
  one dict lookup and one bucket computation - microseconds. Cold
  start regression < 1%. Safe per BASELINE #5.
- **Affects**: `select_level`, `record_run`, every compressor (one new
  method). No change to compressor output bytes when policy is absent.
  Cache key is unchanged - the policy is a function of (schema,
  raw_tokens_bucket, remaining_tokens_bucket), all already part of the
  cache key inputs. Cache determinism preserved per BASELINE #6.

### Steady-state shift estimate

Given the corpus split and `_COMPACT_RATIO = 0.15`'s known mis-fits:

- `git_diff`, `git_status`, `git_log`, `pytest`, `cargo_test`,
  `npm_test`, `go_test`, `grep`, `find`: heuristic mostly optimal.
  Expected bandit shift: < 5% of calls cross a tier boundary.
- `ls_-R` (33.5% reduction floor, well above 0.15 threshold
  assumption): bandit will push more calls from VERBOSE -> COMPACT
  earlier, plus more calls from COMPACT -> ULTRA when they fail to
  meet the budget cap. Expected shift: ~25% of calls.
- `lint`, `docker`, `pkg_install`, `kubectl`: insufficient empirical
  data; expected shift conditional on traces.

Aggregated: in a steady state I expect the bandit to disagree with
the threshold function on **8-15% of all calls**, mostly on the two
weakest schemas. Token impact across a 100-call session: roughly
**400-1200 tokens** saved (3-6%), with the upper bound only realised
on traces dominated by `ls_-R` and the new long-tail compressors.

## Implementation cost

- **Lines of code (rough)**: ~250 LOC new (bandit trainer + fingerprint
  hooks across 11 compressors), ~30 LOC modified (`history.py`,
  `budget.py`), schema migration test fixtures ~80 LOC. Total ~360.
- **New runtime deps**: none. SciPy/NumPy could speed the training
  step but trainer is offline and one-shot, so a pure-Python Beta
  sampler (5 lines) suffices. Honours BASELINE #2-#3.
- **Risks to determinism**:
  - The hot path is fully deterministic by construction (frozen
    table). The risk is human: if someone "improves" V24 by adding
    online updates, BASELINE #1 dies. Mitigation: trainer module
    explicitly forbidden from being imported by `pipeline.py` (lint
    rule + module-level guard).
  - Fingerprinting must be byte-stable across Python versions; use
    `hashlib.sha1` over a `json.dumps(..., sort_keys=True)` of a
    canonical target list, never `hash()`.
- **Risks to robustness**: if a compressor's `content_fingerprint`
  raises on an adversarial input, fall through to empty fingerprint
  (= no parent linking, just like today). No new must-preserve risk.
- **Risks to must-preserve**: zero. The policy only changes which tier
  is selected for tier-eligible inputs; the must-preserve harness
  still enforces COMPACT/VERBOSE preservation, and ULTRA is exempt as
  before.

## Disqualifiers / why this might be wrong

1. **Reward signal is correlated with task hardness, not tier
   suboptimality.** An agent that re-fetches `git diff` at VERBOSE
   may be doing so because the *task* is hard, not because the
   compression was too aggressive. The bandit then learns "this
   schema needs VERBOSE" when really it just needs better selection.
   This is a classic confounding-variable bug in bandit deployments
   (see contextual-bandit literature on observed-confounders).
   Mitigation: condition on task-kind (kind extracted from the
   user's plan call, if any). Without that, V24 may overfit to
   trace-set composition.
2. **The "next call" detection is brittle.** Defining "same content"
   is hard for grep (different patterns over the same files? same
   pattern over different files?). A bad fingerprint either misses
   real re-fetches (under-counts negative reward, bandit converges
   to too-aggressive tiers) or false-positives on incidental overlap
   (over-counts negative reward, bandit becomes pathologically
   verbose). The 80%-overlap threshold above is a guess; tuning it
   IS itself a hyperparameter the bandit cannot self-tune without
   meta-learning.
3. **Static policy implies stale policy.** A repo that adopts a new
   build tool sees its `pkg_install` call distribution shift;
   shipped policy stays at the trained values until the next
   release. Tier choice for that schema may regress. Mitigation: the
   threshold function remains the safety net, and a per-cell
   confidence check (`min_pulls_per_cell` not met -> fall through)
   keeps cold cells on the heuristic. But that means the bandit
   only shifts the head of the distribution, which is also where
   the heuristic is already good.
4. **Already adjacent to V09.** V09 (selective re-fetch protocol)
   reduces re-fetches by giving the agent a marker; V24 reduces
   re-fetches by picking a more conservative tier. They compete for
   the same delta. If V09 ships first and lands the 200-1500 token
   per-avoided-fetch saving, V24's headroom collapses. They
   **should not both be deployed unless their wins are shown to be
   independent**, which requires a 2x2 ablation.
5. **The threshold function is not just a heuristic, it is a
   contract.** Several MCP clients (per `_meta.redcon` schema) may
   have written assumptions like "VERBOSE iff remaining > 10k". A
   bandit that violates this contract for some cells without
   advertising it is a quiet behavioural change. Mitigation: emit
   `_meta.redcon.tier_source = "policy" | "heuristic"` so consumers
   can distinguish.
6. **Determinism-vs-bandit reframing risk.** If the static policy is
   regenerated on every release from internal telemetry, the project
   ships **non-reproducible-from-source** behaviour: a user who
   builds from `git clone` does not get the bandit-trained policy
   unless they have access to telemetry. Mitigation: ship the
   training corpus alongside the policy, pinned by commit hash.
   That doubles the repo size for a 3-6% gain.

## Verdict

- **Novelty: medium**. Bandits over compression tiers are not novel in
  the abstract (compression-quality bandits exist in video codec
  literature, e.g. ABR / Pensieve). Novel-for-Redcon is the
  reward-signal definition (re-fetch detection from cmd-side history)
  and the determinism split (offline trainer + frozen table). The
  V09-style channel-coding angle is more interesting per token-saved.
- **Feasibility: medium**. The schema+fingerprint work is
  straightforward (~1 week). The trainer is straightforward
  (~3-5 days). The hard part is acquiring a representative corpus:
  Redcon does not yet ship telemetry, so the first `policy_table.json`
  has to be trained on synthetic or self-recorded traces, which
  collapses the headline win until real-agent traces accrue.
- **Estimated speed of prototype**: ~2 weeks for end-to-end pipeline
  (schema + fingerprint + trainer + flag-gated lookup). ~1-3 months
  before there is enough deployment trace volume for the policy to
  beat the heuristic on a held-out trace.
- **Recommend prototype: conditional-on** (a) shipping V09's
  `_meta.redcon.refetch_candidates` first so re-fetches are *labelled*
  by the agent (sharper reward signal than overlap-fingerprint
  guessing), and (b) measured baseline showing >=10% disagreement
  rate with the heuristic on a recorded trace. Without (a) the
  reward signal is too noisy; without (b) the bandit has nothing to
  learn. If both gates clear, V24 is worth two engineer-weeks for a
  3-6% session-level reduction. If they don't clear, this proposal
  reduces to "tune `_BUDGET_SHARE` and `_COMPACT_RATIO` per schema by
  hand against the bench corpus", which is a 2-day fix in
  `redcon/cmd/budget.py` and captures most of the win without the
  bandit machinery.
