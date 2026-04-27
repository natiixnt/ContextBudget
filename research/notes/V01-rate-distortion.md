# V01: Rate-distortion theory for code outputs - derive R(D) curve, pick operating point per compressor

## Hypothesis

Redcon's `select_level` uses a single global compact-ratio constant
(`_COMPACT_RATIO = 0.15`, plus VERBOSE = 1.0 and an implicit ULTRA fallback)
to pick a tier. That is a uniform rate model applied to non-uniform sources.
Empirically the compact-tier rate varies from 0.03 (git_diff on a large diff)
to 0.66 (ls -R on a large tree) - a 22x spread. The current rule therefore
mis-fits VERBOSE for low-compressibility sources (ls, grep, find on big inputs)
and wastes context budget by being too cautious for high-compressibility
sources (git_diff, pytest large).

Claim: each compressor has a measurable, near-stationary rate-distortion
operating triple `(R_v, R_c, R_u)` where `R_t = compressed_tokens /
raw_tokens` at tier `t`. Given a per-compressor distortion proxy `D_t`
(must-preserve violation rate from the quality harness, plus tier-level
quality_floor), the optimal tier is the one that maximises a Lagrangian
`tokens_saved - lambda * D` for the caller's lambda. lambda is set
deterministically from the budget pressure (tokens-remaining vs
tokens-needed), not pinned to one constant. Prediction: switching from a
single global `_COMPACT_RATIO` to per-schema (R_t, D_t) tables and a
budget-driven lambda shifts the tier choice on >=4 of 11 compressors at
realistic budgets, and on the affected calls saves an extra 5-30 percentage
points of tokens vs the heuristic. On compressors where the global ratio
already matches the empirical R, the rule reproduces today's choice; that
boundary itself is a useful result.

## Theoretical basis

### Setup

Source `X` is a stream of raw command-output tokens with empirical length
`n = raw_tokens`. A compressor at tier `t in {V, C, U}` is a deterministic
encoder `f_t: X -> Y_t` with output length `m_t = compressed_tokens`. Let
the rate be the per-token rate

  `R_t = m_t / n`         (units: output tokens per input token).

Define a task-utility distortion `D_t in [0, 1]`. We use the ground-truth
labels Redcon already records:

  `D_t = 1 - 1[must_preserve_ok] * w_floor(t)`

with `w_floor(VERBOSE) = w_floor(COMPACT) = 1` and
`w_floor(ULTRA) = 0` (ULTRA is exempt from must-preserve, BASELINE.md
line 30, harness sets ULTRA's floor met by definition). So:

  - VERBOSE: `D_V = 0` whenever `must_preserve_ok` is True;
  - COMPACT: `D_C = 0` whenever True, `D_C = 1` if False;
  - ULTRA: `D_U = q_ultra in (0, 1]` is a *fixed* per-schema penalty
    representing "ULTRA may drop facts"; we set `q_ultra` from the
    must-preserve regression magnitude on the corpus.

This is the operational distortion the agent actually pays.

### Shannon's lower bound applied

Shannon's rate-distortion theorem (Cover & Thomas, ch. 10) says the
minimum achievable rate to encode `X` with average distortion `<= D` is

  `R(D) = inf_{p(y|x) : E[d(X,Y)] <= D} I(X; Y)`.

For our discrete operating points the curve is piecewise linear between
the three tiers (the standard time-sharing argument: any convex
combination of the three points is achievable by mixing). The lower
convex hull of `{(R_V, D_V), (R_C, D_C), (R_U, D_U)}` is the achievable
R(D). Tiers that are above this hull are *strictly dominated* and should
never be picked - this is the first thing a rate-distortion analysis
buys us.

### Operating-point rule (Lagrangian)

The Bellman tier-choice problem is

  `t*(lambda) = argmin_t  R_t + lambda * D_t`              (*)

equivalently `argmax_t  (1 - R_t) - lambda * D_t`, i.e. maximise
tokens-saved-per-input-token minus a budget-weighted distortion penalty.

The per-pair switching point between tiers `a` and `b` is

  `lambda_{a,b} = (R_a - R_b) / (D_b - D_a)`        when D_b > D_a.

So between VERBOSE (R_V, D_V=0) and COMPACT (R_C, D_C in {0,1}):

  - if `D_C = 0` on this schema (must-preserve always holds at COMPACT),
    COMPACT strictly dominates VERBOSE for any lambda > 0 - we should
    *never* return VERBOSE on a schema whose harness shows `D_C = 0`
    *unless the caller raises the quality floor*. The current heuristic
    returns VERBOSE whenever raw fits in 30% of remaining budget, which
    spends real tokens for zero distortion gain.

  - between COMPACT and ULTRA the switch is at
    `lambda_{C,U} = (R_C - R_U) / (q_ultra - D_C)`.

### Mapping lambda to budget pressure (closed form)

The agent's pain function is "running out of context". Let `B = remaining_tokens`,
`m_t = R_t * n` the output cost at tier `t`. Marginal value of a saved
token is approximately `1/B` (rate of context consumption). Marginal cost
of distortion is the expected re-fetch cost: if must-preserve fails the
agent typically re-issues the command at a higher tier, which costs
roughly `n + R_v * n` in round-trip and re-parse. Setting

  `lambda = re_fetch_cost / saving_value = (n * (1 + R_V)) / (1 / B) ... `

and normalising both to the per-output-token scale gives

  `lambda = c * (B / n)`           with c in [0.5, 2] empirical.

Plug into (*): when budget `B` is tight relative to raw size `n`,
lambda small -> ULTRA wins. When `B >> n`, lambda large -> COMPACT wins
(and VERBOSE wins iff `D_V = D_C` at this schema, which is the ordinary
case). This is mathematically what the current code is *trying* to
do via `budget_cap = 0.30 * remaining` and a constant 0.15 ratio. It
does it with wrong constants:

  - The 0.15 constant is the *VERBOSE -> COMPACT* fit-threshold, not a
    per-schema rate. Reality (from `docs/benchmarks/cmd/`):
       git_diff_huge: R_C = 0.030
       pytest_massive: R_C = 0.262
       grep_massive: R_C = 0.231
       find_massive:  R_C = 0.187
       ls_huge:       R_C = 0.665
    So on grep / find / ls the heuristic *under*-allocates: it thinks
    COMPACT will use 15% of n when it actually uses 19-66%. Where this
    matters is COMPACT-vs-ULTRA: on a 4k-token cap, a 7k-token grep
    gives `15% * 7k = 1050` (heuristic says "fits, pick COMPACT") but
    actually outputs 1623 (still fits at this cap, but on a 1200-token
    cap it would mispredict and emit a tier that overflows).

  - There is no R_C row for ULTRA. ULTRA is implicitly (rate ~0).
    Reality: ULTRA varies 0.0006 (find_massive: 8/3398) to 0.94 on
    tiny header-dominated inputs.

### Back-of-envelope (>= 3 lines as required)

Take grep_massive (`n = 7015`, `R_C = 0.231`, `R_U = 0.0011`,
`D_C = 0` if must-preserve ok, `D_U = q_ultra` say 0.5).

Switch threshold `lambda_{C,U} = (0.231 - 0.0011) / (0.5 - 0) = 0.460`.

Heuristic rule with `B = 4000, max_out = 1500`:
  - `budget_cap = 1200`, `hard_cap = 1500`.
  - Verbose estimate `7015 * 1.0 = 7015 > 1200` -> reject.
  - Compact estimate `7015 * 0.15 = 1052 < 1200` -> heuristic picks COMPACT.
  - Real compact output = 1623 -> *overflows the budget cap by 35%*.

R(D) rule, same budget:
  - lambda = c * (B/n) = 1 * (4000/7015) = 0.570. Above 0.460 ->
    rule prefers COMPACT *if* D_C = 0 on this fixture. Quality
    harness shows must_preserve_ok = True -> COMPACT chosen.
  - But the rule also predicts the *true* output size 1623 from
    the per-schema R_C, so it can hard-check `1623 <= max_out=1500`
    and *pre-emptively step down to ULTRA* (predicted 8 tokens),
    which the current rule cannot do.

Same input, agent allocates only `B = 1500`:
  - Heuristic: budget_cap = 450; compact estimate 1052 > 450 ->
    falls to ULTRA. Wastes detail unnecessarily because real compact
    1623 also overflows; here heuristic and R(D) agree.

Different schema, git_diff_huge (`n = 8078`, `R_C = 0.030`):
  - At `B = 4000`: heuristic compact estimate `8078 * 0.15 = 1212`,
    but real is 244. Heuristic is *5x too pessimistic*. With a tight
    cap like `max_out = 1000`, heuristic correctly picks COMPACT.
    With `max_out = 500`, heuristic predicts `1212 > 500` and falls
    to ULTRA (loses detail). R(D) rule with calibrated `R_C = 0.030`
    predicts 244 < 500 and returns COMPACT. *This is a real shift in
    behaviour and saves the agent a full re-fetch.*

So on git_diff (and pytest, by symmetry: `R_C = 0.262` on the massive
fixture but predicted 0.738 reduction means R_C = 1 - 0.738 = 0.262, so
on small caps the heuristic overshoots), the R(D) rule keeps COMPACT in
regimes where the heuristic prematurely degrades. That is the operating
point shift the vector asks for.

### Convex hull check

For each schema we should also drop any tier above the lower convex hull
of `{(R_t, D_t)}`. Inspecting the benchmark tables:

  - cargo_test, npm_test, go_test: `R_V = R_C` on the small fixtures
    (tied formatting). VERBOSE is on the hull only if `D_V < D_C`,
    which in practice is never: must_preserve passes at both. So
    VERBOSE is *strictly dominated* by COMPACT here and the hull
    rule says: never pick VERBOSE on these schemas. Today's heuristic
    picks VERBOSE for short outputs, wasting nothing because tokens
    are equal but also gaining nothing.

  - find on small input (raw=12, all tiers expand): the entire hull
    collapses into "raw is below the must-preserve floor exemption
    threshold (80 raw tokens)" - the existing rule already short-
    circuits this at the harness level. R(D) rule agrees.

  - git_status (raw=16): same regime, header dominates. R(D) is mute.

So the rule's behaviour change concentrates on large fixtures, exactly
the case the benchmarks currently measure as "compact".

### Worked numerical R(D) tables (non-trivial result)

From `docs/benchmarks/cmd/*.json`, large-fixture-only:

  schema       R_V    R_C    R_U    D_V  D_C  D_U   non-dominated tiers
  git_diff   0.7732 0.0302 0.0053  0    0   0.5    {C, U}      (V dominated; D_V=D_C)
  pytest     0.4466 0.2618 0.0078  0    0   0.5    {C, U}      (V dominated)
  grep       0.6612 0.2314 0.0011  0    0   0.5    {C, U}      (V dominated)
  find       0.5633 0.1872 0.0024  0    0   0     {C, U}      (V, U tied D=0)
  ls -R      1.0032 0.6650 0.0097  0    0   0     {C, U}      (V dominated; D_U from harness fail flag)
  git_log    0.6875 0.2188 0.2500  0    0   1     non-monotone (R_U > R_C and D_U=1!)
                                                    -> ULTRA strictly dominated by COMPACT
                                                    on this schema. Never pick ULTRA.

The `git_log` row is the interesting one: ULTRA produces *more* tokens
than COMPACT on the test fixture (16 vs 14 - the ULTRA format has more
header) AND has higher distortion (must_preserve_ok = False). It is
strictly dominated. The current heuristic doesn't know this and will
gleefully pick ULTRA when budget is tight, paying the cost twice. That
is a non-trivial result: rate-distortion analysis disqualifies a tier
on a real schema.

The same hull check will rule out VERBOSE on every multi-line schema
where compact passes must_preserve - the harness already validates
this for COMPACT. So for those schemas the only meaningful runtime
choice is COMPACT vs ULTRA, which simplifies (*) to a one-dimensional
threshold check.

## Concrete proposal for Redcon

### Files

- `redcon/cmd/budget.py` (modify): replace single `_COMPACT_RATIO` with
  a per-schema `RateDistortionTable`, compute lambda from
  `BudgetHint`, run the Lagrangian.
- `redcon/cmd/_rate_distortion.py` (new, ~80 LOC): static table
  generated from `docs/benchmarks/cmd/*.json` plus the convex-hull
  pruning function. Pure data, no runtime deps.
- `benchmarks/run_cmd_benchmarks.py` (modify slightly): emit
  `R(D)` table to `redcon/cmd/_rate_distortion_table.json` so the
  data is regenerated deterministically by the existing pipeline.
- `redcon/cmd/quality.py` (no change required; harness already
  produces the per-tier `must_preserve_ok` we use as `D_t`).

The table is keyed by `schema` (the compressor's `schema` attribute),
so dispatch is one dict lookup. Cold-start budget is unaffected -
this is a constant table read, not a model load.

### API

```python
# redcon/cmd/_rate_distortion.py
@dataclass(frozen=True, slots=True)
class RDPoint:
    rate: float          # compressed/raw expected on >=80-token inputs
    distortion: float    # 0..1, from must_preserve fail-rate * floor weight

@dataclass(frozen=True, slots=True)
class RDTable:
    points: dict[CompressionLevel, RDPoint]
    nondominated: tuple[CompressionLevel, ...]   # convex-hull pruned

# Static table loaded once at import time
RD_TABLES: dict[str, RDTable] = _load_rd_tables()
DEFAULT_RD = RDTable(points=..., nondominated=(COMPACT, ULTRA))
```

```python
# redcon/cmd/budget.py (sketch, ~25 lines change)
def select_level(raw_tokens: int, hint: BudgetHint, schema: str | None = None) -> CompressionLevel:
    if raw_tokens <= 0:
        return _at_least(VERBOSE, hint.quality_floor)
    if hint.remaining_tokens <= 0 or hint.max_output_tokens <= 0:
        return _at_least(ULTRA, hint.quality_floor)

    table = RD_TABLES.get(schema or "", DEFAULT_RD)
    lam = _lambda_from_budget(hint, raw_tokens)             # B/n based, deterministic

    # Lagrangian over non-dominated tiers, plus hard cap pre-check
    best = ULTRA
    best_score = math.inf
    for tier in table.nondominated:
        rate = table.points[tier].rate
        dist = table.points[tier].distortion
        predicted_out = max(1, int(raw_tokens * rate))
        if predicted_out > min(hint.max_output_tokens, int(0.30 * hint.remaining_tokens)):
            continue
        score = rate + lam * dist
        if score < best_score:
            best_score, best = score, tier

    return _at_least(best, hint.quality_floor)
```

```python
def _lambda_from_budget(hint: BudgetHint, raw: int) -> float:
    # Closed form: lam ~ B/n, capped to avoid pathological floats.
    return max(0.0, min(8.0, hint.remaining_tokens / max(1, raw)))
```

The compressors call `select_level(raw_tokens, ctx.hint, schema=self.schema)`.
`self.schema` is already a class attribute on every compressor (the
existing constant), so the change at the call site is one keyword arg.

### Behaviour delta vs current code (deterministic, testable)

  - On `git_log`: never returns ULTRA for non-empty input (dominated tier).
  - On schemas with `D_C = 0` measured: returns COMPACT instead of VERBOSE
    whenever the caller hasn't pinned a floor of VERBOSE. This is *not* a
    regression because the harness already proves COMPACT preserves the
    invariants on these inputs.
  - On `grep / ls / find` large outputs: predicts the true compact size
    instead of `0.15 * n`, so on tight `max_output_tokens` it correctly
    drops to ULTRA earlier (avoids overflow) - this is the genuine
    operating-point shift the vector asks for.
  - On schemas without a fitted entry: falls back to `DEFAULT_RD` whose
    rates equal the existing `(1.0, 0.15, 0.0)` constants - i.e. the new
    code reduces to the old code in the absence of data.

## Estimated impact

- **Token reduction**: net effect is *budget-correct* tier selection rather
  than reduction-on-a-fixed-input. For grep/find/ls under tight caps the
  gain is avoiding ~30-50% accidental overflow that today silently falls
  back to ULTRA at the parser level. For git_diff/pytest under medium
  caps the gain is keeping COMPACT (which has 70-97% reduction) where the
  heuristic prematurely went to ULTRA. Across the M9 benchmark mix the
  estimated *delivered-info per token* gain is +5 to +12 percentage
  points on 4 of 11 compressors. Doesn't move the headline compact-tier
  reduction numbers (those are tier-conditional), but moves the *expected*
  reduction at the agent level by changing which tier is picked.

- **Latency**: cold +0 ms (table load is a JSON read deferred to first call,
  same pattern as existing lazy-imports). Warm +O(3) dict lookups +
  one float div per call, sub-microsecond.

- **Affects**: every compressor that calls `select_level` (currently 11),
  plus `redcon/cmd/quality.py` consumes the same harness data so we
  validate D_t before shipping the table. No change to cache layer; the
  cache key is argv-derived, not tier-derived (tier is part of the value,
  not the key) - so existing cache stays valid.

## Implementation cost

- Lines of code: ~80 LOC for `_rate_distortion.py`, ~25 LOC change to
  `budget.py`, ~30 LOC table-emit append to `run_cmd_benchmarks.py`,
  ~50 LOC of tests (additions to `test_cmd_budget.py`).
- New runtime deps: none. Pure stdlib JSON + math.
- Risks:
  - **Determinism**: table is a static JSON shipped in repo, regenerated
    by the existing benchmarks pipeline. Same input -> same lambda ->
    same tier. Preserved.
  - **Calibration drift**: if a compressor's R changes (e.g. someone
    edits format) and the table isn't regenerated, the Lagrangian
    works with stale numbers. Mitigation: a CI check that recomputes
    R from the live harness and fails on a >5pp delta vs the shipped
    table.
  - **Floor of D**: setting `q_ultra = 0.5` is an editorial choice. It
    must be a config constant per schema, tunable via the same JSON.
    Defaults: 0.5 where harness fails must-preserve, 0.0 otherwise.
  - **must-preserve guarantee**: unchanged. We never *lower* the
    quality_floor; we only refuse dominated tiers and pick correctly
    among non-dominated ones. Floor-clamp `_at_least` stays as-is.
  - **Small-input regime**: the harness already exempts <80-token inputs
    from reduction floor checks; we adopt the same threshold and route
    those to the existing fallback rule unchanged.

## Disqualifiers / why this might be wrong

1. **The "heuristic in disguise" risk.** A static per-schema rate table
   is just three numbers per compressor. If you only used the lambda
   threshold (and skipped the convex-hull pruning), the resulting rule
   reduces algebraically to "if R_C * n <= cap pick COMPACT else ULTRA",
   which is exactly the existing code with a per-schema `R_C` instead
   of a global one. The information-theoretic framing is then a fancy
   wrapper around "calibrate the constant per command", not a
   breakthrough. Honest assessment: most of the gain comes from
   per-schema calibration, not from rate-distortion theory. The genuinely
   new contribution is the (a) hull pruning that disqualifies tiers
   (e.g. ULTRA on `git_log`, VERBOSE on `cargo_test`), and (b) the
   distortion-weighted Lagrangian which makes lambda budget-driven
   instead of ratio-driven.

2. **Distortion proxy is binary.** `must_preserve_ok` is 0/1, not a
   continuous semantic distance. R(D) curves on binary distortion
   collapse into two regimes (preserve / not), so the "curve" has at
   most two slopes. A richer distortion (e.g. fraction of must-preserve
   patterns matched, or Jaccard over claimed facts) would smooth the
   hull and might flip more decisions. Without that, the hull pruning
   is the *only* qualitative effect; everything else is calibration.

3. **Per-schema tables are corpus-specific.** The R values come from
   the M8/M9 fixture corpus (2 fixtures per schema in many cases). A
   Bayesian estimate would smooth toward the global prior (today's
   constant 0.15) when fixture count is small. Without that, the
   table can be tail-fit on `find_massive` and overconfident on a
   different real-world `find` invocation. Mitigation already exists
   (record_history SQLite -> we could *learn* R online from
   real invocations), but that introduces non-determinism unless we
   pin the table to a versioned snapshot.

4. **Already partially done.** The harness in `redcon/cmd/quality.py`
   already collects per-tier reduction and `must_preserve_ok`. The
   benchmarks in `docs/benchmarks/cmd/` already publish the operating
   points. What's missing is *wiring those numbers into select_level*,
   not measuring them. So the proposal is mostly a 50-line plumbing
   change - the theory part is justification rather than discovery.

## Verdict

- **Novelty: medium**. The Lagrangian over a per-schema R(D) table is
  textbook (Cover & Thomas ch. 10), but applying it deterministically
  with the harness data already in the repo and getting genuine new
  behaviour (the `git_log` ULTRA-domination case, the `grep` size
  pre-check, the `cargo_test` VERBOSE-domination case) is a real
  step beyond a single global constant. Not a paradigm shift; a
  principled calibration with two non-trivial qualitative effects
  (hull pruning + budget-driven lambda).
- **Feasibility: high**. Static JSON + 80 LOC + uses only data
  the harness already produces. No new deps, no embeddings,
  determinism preserved (the table ships as code), cache key
  unchanged.
- **Estimated speed of prototype: 1-2 days**. Half a day to write
  `_rate_distortion.py` and the JSON emitter; half a day to wire
  `select_level` and add tests; up to a day if we add the
  Bayesian-shrinkage and CI drift check. The proposal as written
  fits in a single PR.
- **Recommend prototype: yes**, conditional on adding the calibration
  drift check (point 3). Without it, this is a one-shot fix that
  silently goes stale; with it, this is a permanent improvement
  that the benchmarks pipeline keeps honest.
