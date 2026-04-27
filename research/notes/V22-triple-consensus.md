# V22: Triple-scorer consensus filter - keep only files where all three deterministic scorers agree

## Hypothesis

Today `redcon.scorers.relevance.score_files` produces a *single weighted* score
that sums keyword hits, import-graph propagation bonuses, role multipliers, and
historical adjustments. A file just past the cutoff usually got there because
*one* signal spiked. Claim: when the three orthogonal axes already in
`redcon/scorers/` (keyword/path-content match, import-graph topology,
file-role centrality) are evaluated *independently* and we keep only files
that rank in the top-K under all three, the resulting "consensus" set is
much smaller and dominated by genuinely central files. Concretely on this
repo for the task `command output compression`, at the natural top-K the
consensus rule shrinks the selected token cost by 50-70% relative to the
weighted top-25, while never including a file that was a one-signal
artefact (the failure mode that consensus is engineered to suppress). The
softer pair-union ("any 2 of 3 agree") provides a graceful fallback for
small repos and rare-keyword tasks where strict triple consensus is empty.
Prediction: triple consensus is empty at K=25 on this repo (a real
finding, not a bug), becomes non-empty at K~=75, and pair-union at K=25
already gives a usable, smaller-and-cleaner set than the weighted top-25.

## Theoretical basis

### Setup

Let `S_A, S_B, S_C: F -> R` be three deterministic scorers over a file set
`F`. Define `T_A(K)` as the top-K of `F` ordered by `S_A`, similarly
`T_B(K), T_C(K)`. The *weighted* top-K used today is

  `T_W(K) = topK(F, alpha*S_A + beta*S_B + gamma*S_C)`,

with `alpha,beta,gamma` baked into `score_files` (keyword has the largest
amplitude, graph adds a one-shot bonus capped at `graph_bonus_cap`, role
multiplies). The *consensus* set is

  `T_AND(K) = T_A(K) intersect T_B(K) intersect T_C(K)`,

the *pair-union* (any 2 of 3) set is

  `T_OR2(K) = (T_A & T_B) U (T_A & T_C) U (T_B & T_C)`.

### Why consensus is principled (Bayesian sketch)

Treat "file `f` is task-relevant" as a latent binary label `Y_f`. Each
scorer is a noisy classifier; let `r_f^X = 1[f in T_X(K)]` for X in
{A,B,C}. Under the assumption that the three classifiers are *conditionally
independent given* `Y_f` (the orthogonality assumption baked into V22), the
posterior odds that `f` is relevant given all three voted "in" is

  `P(Y=1 | r^A=r^B=r^C=1) / P(Y=0 | ...) =`
    `(P(in|1)/P(in|0))_A * (P(in|1)/P(in|0))_B * (P(in|1)/P(in|0))_C * prior_odds`.

Each factor `LR_X = P(in|1)/P(in|0)` is the per-scorer likelihood ratio at
"top-K vote = yes". For a scorer with precision `p_X` at top-K and base
rate `pi`, `LR_X = p_X / pi * (1-pi) / (1-p_X)`. Three votes multiply, so
even moderately precise scorers (LR ~ 3) compound to LR_total ~ 27.
Equivalently, the consensus set is roughly the top of the *product* score
`S_A * S_B * S_C` thresholded at the geometric-mean-of-tops, which is
algebraically distinct from the *sum* `alpha S_A + beta S_B + gamma S_C`
the current code uses: sums admit a single-large-axis spike; products do
not. This is the *information-fusion* argument for consensus filtering:
log-product of likelihood ratios is the optimal classifier under
conditional independence (Pearl 1988, ch. 2.1; the same identity that
underwrites naive Bayes).

### Why triple consensus may *over-prune* (and pair-union restores recall)

The independence assumption is a lie when scorers are *correlated*. In our
case A and B are weakly correlated (an import-imported pair often shares
path tokens), C and A are weakly correlated (test files contain the word
"test"), and C is *coarse* (only 6 distinct role classes). Hence:

  - Strict consensus has high precision but possibly bad recall, especially
    when one scorer is degenerate-tied near its top.
  - Pair-union ("any 2 of 3") corresponds to majority-vote and is the
    optimal symmetric rule when the per-scorer error rate is < 1/2
    (Condorcet's jury theorem). For three classifiers with error rate `e`,
    pair-union error is `3e^2 - 2e^3`, which beats individual `e` whenever
    `e < 1/2`. So pair-union is the principled softer alternative.

### Back-of-envelope numbers (>=3 lines required)

Measured on this repo with the experiment script `/tmp/v22_consensus_experiment.py`
(reads files directly, mirrors `redcon.core.text.task_keywords`,
`file_roles.classify_file_role`, the python module-resolution rules from
`scorers/import_graph.py`, and the `relevance.py` weighting constants;
excludes `.venv-agent`, `.venv-codex`, and `redcon/symbols`). Task:
`command output compression`. Keywords: `["command","output","compression"]`.

  - 504 candidate files (everything redcon would actually scan).
  - Token cost for the **weighted top-25** (current rule): **93,899**
    tokens (chars/4 estimator over full file size).
  - At K=25:
      - `|T_A| = 25` tokens=53,630  (keyword scorer)
      - `|T_B| = 25` tokens=133,986 (import-graph; only 25 files have
        non-zero graph-propagation score because seeds came from A)
      - `|T_C| = 25` tokens=86,793  (file-role scorer; saturates at the
        depth/role tied maximum; selects benchmarks/dashboard scaffolding)
      - **Consensus: 0 files, 0 tokens.**
      - **Pair-union: 8 files, 64,889 tokens** (a 31% drop vs weighted).
        Members: `redcon/__init__.py, redcon/cli.py, redcon/cmd/__init__.py,
        redcon/cmd/budget.py, redcon/cmd/pipeline.py, redcon/config.py,
        redcon/engine.py, redcon/stages/workflow.py`.
  - At K=75 (3x oversampling):
      - **Consensus: 11 files, 32,319 tokens** (-65% vs weighted top-25).
        Members: `cmd/__init__.py, cmd/benchmark.py, cmd/budget.py,
        cmd/cache.py, cmd/history.py, cmd/pipeline.py, cmd/runner.py,
        cmd/types.py, compressors/context_compressor.py, config.py,
        core/agent_cost.py`.
      - Pair-union: 42 files, 140,398 tokens.
  - At K=100: consensus 16 files, 60,610 tokens; pair-union 73 files,
    253,583 tokens (here pair-union exceeds the weighted-25 cost - a
    crossover after which it stops being a budget win).

The K=25 "consensus is empty" result is **the central finding**, not a
bug. The file-role scorer (axis C) is too coarse: depth-based centrality
collapses the top into a tie of 15 files at score 1.35 (everything at
depth <= 1 in any subdirectory), so it pulls in `dashboard/next.config.js`
and `vscode-redcon/esbuild.js` instead of the cmd internals. Strict
consensus refuses to admit `cmd/pipeline.py` because role-axis C ranks it
at depth 2 (centrality 1.20) below the depth-0/1 ties. This is the rule
working as designed: it correctly *refuses* to include any file unless
all three independent signals agree, and on this repo no file passes the
bar at K=25 because of axis-C saturation.

The fix is *not* to weaken consensus; it is either (a) to fix axis C to
break ties, or (b) to use pair-union as the primary rule. Both are
deterministic, both are supported by the script, and the numbers above
say (b) is already a 31% token win at K=25 with no other change.

### Token-cost ledger (consolidated)

|         | size | tokens (chars/4) | vs weighted-25 |
|---------|------|------------------|----------------|
| weighted top-25 (today) | 25 | 93,899 | baseline |
| consensus K=25 | 0 | 0 | -100% (empty) |
| consensus K=75 | 11 | 32,319 | **-65%** |
| consensus K=100 | 16 | 60,610 | -35% |
| pair-union K=25 | 8 | 64,889 | **-31%** |
| pair-union K=50 | 28 | ~110k | +17% (worse) |

The non-monotone pair-union row tells you the rule is sensitive to K. The
only regimes where consensus or pair-union *strictly dominate* weighted
top-25 on token cost AND keep the central cmd files are
`{consensus K in [60,100], pair-union K in [20,30]}`.

## Concrete proposal for Redcon

### Files

- `redcon/scorers/relevance.py` (modify): add a `selection_mode` parameter
  to `score_files`, defaulting to today's behaviour (`"weighted"`).
  Two new modes: `"consensus"` and `"pair_union"`.
- `redcon/scorers/_consensus.py` (new, ~70 LOC): per-axis ranker +
  set-algebra. Pure stdlib.
- `redcon/config.py` (modify): one new field on `ScoreSettings`,
  `selection_mode: Literal["weighted","consensus","pair_union"] = "weighted"`,
  plus `consensus_top_k_factor: float = 3.0` (oversample factor).
- No production source change required for the experimental measurement
  shipped here - this proposal is the *opt-in* mode plumbing, not a
  default-behaviour change.

### API sketch (8-12 lines per function)

```python
# redcon/scorers/_consensus.py
@dataclass(frozen=True, slots=True)
class AxisRanking:
    name: str
    rank_by_path: dict[str, int]   # 1-indexed; missing => +inf

def topk_paths(score_by_path: dict[str, float], k: int) -> set[str]:
    ordered = sorted(score_by_path.items(), key=lambda kv: (-kv[1], kv[0]))
    return {p for p, s in ordered[:k] if s > 0}

def consensus_filter(rankings: list[set[str]], min_agree: int) -> set[str]:
    counts: dict[str, int] = {}
    for r in rankings:
        for p in r:
            counts[p] = counts.get(p, 0) + 1
    return {p for p, c in counts.items() if c >= min_agree}
```

```python
# redcon/scorers/relevance.py (sketch of the new branch in score_files)
if cfg.selection_mode in ("consensus", "pair_union"):
    keyword_scores = _compute_keyword_scores(...)
    graph_scores   = _compute_graph_scores(...)
    role_scores    = _compute_role_scores(...)
    k = int(top_n * cfg.consensus_top_k_factor)
    sets = [topk_paths(keyword_scores, k),
            topk_paths(graph_scores, k),
            topk_paths(role_scores, k)]
    min_agree = 3 if cfg.selection_mode == "consensus" else 2
    accepted = consensus_filter(sets, min_agree)
    # then re-rank accepted by the existing weighted score, return RankedFile
    # objects with a `score_breakdown["selection"] = "consensus|pair_union"`
    # reason for explainability.
    ranked = [rf for rf in weighted_ranked if rf.file.path in accepted]
    return ranked
```

The keyword / graph / role score-extraction helpers already exist inline
inside `score_files`; this proposal asks them to be extracted into three
private helpers (`_compute_keyword_scores`, `_compute_graph_scores`,
`_compute_role_scores`) so they can be invoked independently. That
refactor is mechanical and preserves behaviour bit-for-bit when
`selection_mode == "weighted"`.

### Determinism / cache

- Same input -> same set membership -> same `RankedFile` order. The
  `consensus_top_k_factor` is a frozen config field, so the oversample
  factor is part of the deterministic input.
- No new tokeniser path. No new IO. No subprocess.
- Cache: `relevance.score_files` is invoked once per pack; no cache layer
  changes.

## Estimated impact

- **Token reduction (file-side packing)**: on this repo for "command output
  compression", the *opt-in* consensus mode at K=75 delivers 32k vs 94k
  tokens for the same task -> 65pp absolute reduction in *what gets sent
  to the model* before the per-file compressor even runs. Pair-union at
  K=25 delivers 65k vs 94k -> 31pp. **This stacks multiplicatively with
  every other compressor: consensus picks fewer files; per-file compress
  reduces each file**.
- **Token reduction (compact-tier of cmd compressors)**: zero. This vector
  is on the file-side scorer, not the cmd-side compressors. BASELINE.md's
  "breakthrough" definition of >=5pp on compact-tier *does not directly
  apply*; this is on a different surface (file packing).
- **Latency**: cold +0 ms (no new imports on the default path).
  Warm: one extra pass over the score dicts (`O(N log K)` for top-K), so
  ~+0.3-0.6 ms on a 5000-file repo. Negligible.
- **Affects**: `redcon/scorers/relevance.py` only. Cache key for
  `redcon plan`/`redcon pack` becomes a strict superset (the
  `selection_mode` value joins it), keeping BASELINE.md constraint 6.

## Implementation cost

- LOC: ~70 for `_consensus.py`, ~40-60 for the refactor + branch in
  `relevance.py`, ~30 for `ScoreSettings` field, ~80 for tests
  (`tests/test_scan_and_score.py` already covers `score_files`; add
  three cases: consensus default-empty for under-K repos, pair-union
  matches today on weighted on a specific corpus, oversample factor
  honoured).
- New runtime deps: none.
- Risks:
  - **Recall regression** on small repos and rare-keyword tasks: at
    K=25 consensus is *empty* on this exact repo; that's the
    headline finding. Mitigation: `consensus_top_k_factor >= 3.0` so
    consensus is always computed at K' = 3 * top_n, plus a hard
    fallback "if consensus empty, fall through to pair-union, then
    weighted". That fallback is the only correct default.
  - **Coarse axis-C saturates**: `file_roles` only emits 6 classes,
    and the depth-centrality I used in the experiment is my own
    extension - the *real* `relevance.py` uses role *multipliers* on
    the keyword score, not as an independent axis. To make C
    genuinely orthogonal in production we have to *promote* role into
    a standalone scoring axis (not a multiplier on A). That is a
    behaviour change to the role layer; it does not just plug into
    today's pipeline as-is. **This is the load-bearing caveat.**
  - **Determinism**: preserved as long as `consensus_top_k_factor` is
    a config field, not a runtime arg.
  - **Must-preserve**: not applicable - this is file selection, not
    command-output compression.

## Disqualifiers / why this might be wrong

1. **The current weighted scorer already encodes consensus implicitly via
   the graph-seed gate.** `relevance.py:163` (`graph_seed_score_threshold`)
   only awards graph propagation bonuses to files whose *keyword* score
   already passed a threshold - i.e. it requires A and B to "agree"
   before B can boost. This is a *one-sided* consensus already in the
   code. The triple-consensus rule generalises it to all three axes, but
   the gain over the existing seed-gating is smaller than the headline
   number suggests on tasks where the keyword scorer alone is already
   discriminative (most tasks: the 16-keyword task vector is task-defined).

2. **C is not orthogonal in the current code.** In `relevance.py:140-154`,
   `role_multipliers` is applied as a *multiplicative* gate on the
   already-summed score, not as an independent axis. To do real triple
   consensus we would have to extract a standalone role-score (the
   experiment used a depth-centrality plus role-multiplier proxy for
   this; production code does not have one). So the proposal as written
   *adds a new axis* rather than *combining three existing axes* - the
   theoretical claim of "use what's already there" is half-true.

3. **The headline 65% reduction is at K=75, after we admitted K=25 was
   empty.** That looks like moving the goalposts. Honest framing: at
   the natural K matching `top_n=25`, strict consensus is empty on this
   repo for this task; the "win" requires either a 3x oversample (which
   weakens the agreement guarantee, since being in top-75 of any axis
   is much weaker evidence than being in top-25), or the pair-union
   relaxation (which is just majority-vote, not triple consensus). The
   *strict* triple-consensus claim, as the vector states it, fails
   empirically on this repo at the natural K.

4. **Already partially done elsewhere.** The historical-adjustment layer
   (`scorers/history.py`) already implements an "agreement with prior
   runs" filter. The graph-seed threshold already implements
   "keyword + graph must agree". So the architecture has consensus
   *components* already; this vector adds the symmetric three-way version.
   That is genuinely new but not a paradigm shift.

5. **Token cost is measured chars/4, not via the production tokenizer.**
   The 65% / 31% numbers above use a cl100k *approximation*. The real
   tokenizer can shift those by 5-15% in either direction depending on
   identifier density. The qualitative conclusion (consensus K=25 is
   empty, pair-union K=25 saves real tokens, consensus K=75 saves a lot)
   is robust to that error; the exact percentages are not.

## Verdict

- **Novelty: medium**. The naive-Bayes-style three-classifier vote is
  textbook (Pearl 1988); applying it deterministically with the three
  axes that are already in `redcon/scorers/` and discovering that the
  natural-K consensus is *empty* on this repo (because axis C is too
  coarse) is the genuinely new observation. The pair-union fallback is a
  cleaner formulation of "use majority-vote" than I have seen in the
  scoring stack today.
- **Feasibility: high**. ~150 LOC + tests; pure stdlib; opt-in via
  `ScoreSettings.selection_mode`. The only architectural change is
  promoting `file_roles` from a multiplier to a standalone axis, which
  is itself a small refactor with independent value (better
  explainability of role contributions in `RankedFile.score_breakdown`).
- **Estimated speed of prototype: 1-2 days**. Half a day to extract the
  three score-helper functions out of `score_files`. Half a day for
  `_consensus.py` and the new branch. Half a day to add the role
  standalone-axis. Half a day of tests, one of which is the literal
  experiment in this note locked into a fixture.
- **Recommend prototype: conditional on (a) shipping pair-union as the
  *primary* selection mode (strict triple consensus is too fragile at
  natural K), and (b) promoting `file_roles` to a standalone axis with a
  finer-grained centrality measure than today's coarse 6-class label.**
  Without (b), axis C will keep saturating and forcing recall through
  the floor. With both, this is a real opt-in budget win on file-side
  packing for tasks where the three signals genuinely disagree on the
  long tail; it is *not* a default-behaviour change because of the
  recall risk at K=25 on small repos and rare-keyword tasks.
