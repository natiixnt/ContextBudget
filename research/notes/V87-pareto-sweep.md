# V87: Auto-explored quality vs reduction Pareto curve per command

## Hypothesis

Every Redcon compressor exposes a small handful of integer knobs that
were picked once on a small fixture and then frozen as magic numbers:
git_diff `paths[:8]`, hunk `added[:5]` and `removed[:5]`; pytest
`snippet[:8]`, `body_lines[:5]`, message `clip(200)`; grep
`items[:3]`, text `clip(197)` or `clip(297)`; lint `file_limit = 30`,
`code_hist[:8]`, `top_paths[:30]`; docker `containers[:30]`,
`warnings[:10]`, `errors[:5]`, build-step window `[:6] + [-4:]`,
instruction `[:120]`; listing `histogram[:5,8]`, `by_dir[:30]`,
`names[:8]`; git_log `entries[:30]`, body `[:3]`, subject
`clip(50,80)`; kubectl `resources[:30]`; pkg_install `operations[:30]`,
errors `clip(200)`; http_log `top_paths[:20]`, referers `[:10]`;
plus the log-pointer tier's `LOG_POINTER_SUMMARY_TAIL_LINES = 30`.
That is ~40 knobs across 11 compressors plus pipeline.py.

**Claim**: a fully automated, per-compressor sweep over each knob's
discrete domain on the existing M8/M9 fixture corpus, scoring every
configuration on three measurable axes - (compression_ratio,
must-preserve-recall, parsed-fact-coverage) - and extracting the Pareto
frontier yields a `defaults_pareto.toml` whose knee point matches or
beats the hand-picked defaults on >=8 of 11 compressors. Concretely we
predict +1-4 absolute pp of COMPACT-tier reduction on git_diff, lint,
docker, listing, git_log, http_log without any drop in must-preserve
or parsed-fact-coverage. The procedure is empirical (no IB Lagrangian
mathematics, no R(D) theory) and complements V10 / V01 by replacing
their analytic surrogates with the actual measured curve.

This vector's deliverable is two-fold: (a) a reusable `--sweep` mode
on the existing benchmark harness that any researcher can run in
seconds; (b) a static TOML of Pareto-knee defaults shipped as the new
hand-picked numbers. **No production source is modified by this
research note**; the implementation sketch lives in a hypothetical
`benchmark.py --sweep` extension.

## Theoretical basis

Pareto efficiency on a discrete parameter grid is not theoretical
mathematics, it is just multi-objective optimisation, but the
back-of-envelope is needed to size the search space.

### Search space size per compressor

Let a compressor have `k` knobs with domain sizes `(d_1, ..., d_k)`.
Cardinality of the full grid is `Pi d_i`. Concretely, taking 5-8 values
per knob (per the methodology spec):

  pytest:    snippet in {2,4,6,8,10,12}, body in {3,4,5,6,8},
             clip in {40,60,80,120,160,200,300}
             -> 6 * 5 * 7 = 210 configs
  grep:      items_per_file in {1,2,3,4,5,6}, txt_clip in {80,120,160,200,260,300}
             -> 6 * 6 = 36 configs
  git_diff:  paths in {4,6,8,10,12,16}, added in {3,4,5,6,8},
             removed in {3,4,5,6,8}
             -> 6 * 5 * 5 = 150 configs
  lint:      file_limit in {10,15,20,25,30,40,60}, code_hist in {4,6,8,10,12}
             -> 7 * 5 = 35 configs
  docker:    containers in {10,15,20,30,40,60}, warns in {5,8,10,15},
             errs in {3,5,8,10}, instr_clip in {60,90,120,160,200}
             -> 6 * 4 * 4 * 5 = 480 configs
  listing:   by_dir in {10,15,20,30,40,60}, names in {4,6,8,10,12,16},
             hist_head in {3,5,8}
             -> 6 * 6 * 3 = 108 configs
  git_log:   entries in {10,15,20,30,40,60}, body in {0,1,2,3,5,8},
             subj_compact in {40,50,60,80,100}
             -> 6 * 6 * 5 = 180 configs
  kubectl:   resources in {10,15,20,30,40,60}
             -> 6 configs
  pkg_inst:  operations in {10,15,20,30,40}, err_clip in {80,120,160,200,300}
             -> 5 * 5 = 25 configs
  http_log:  top_paths in {5,10,15,20,30,40}, refs in {3,5,8,10,15}
             -> 6 * 5 = 30 configs
  log-ptr:   tail_lines in {10,15,20,30,40,60,80,120}
             -> 8 configs

Total per-compressor max: ~480 (docker). Aggregate across 11 schemas:
~1268 configurations. The current quality harness already runs each
compressor in <2 ms on the M8 fixtures (per `tests/test_cmd_benchmark`
warm-call timings). With ~30 fixtures total and 3 levels each
configuration costs ~30 * 3 * 2 ms = 180 ms. Total sweep cost:

    1268 * 180 ms ~ 230 s ~ 4 min, single-threaded, cold path.

Cuts to ~30 s with multiprocessing across schemas. Cheap enough to
run in CI on every change to a compressor.

### Pareto frontier extraction

For each compressor we collect a set
`P_s = {(theta_i, R_i, recall_i, fact_i) : i in grid}` where
- `R_i = compressed_tokens / raw_tokens` averaged over fixtures
  (lower is better),
- `recall_i = fraction of must-preserve regex hits surviving` averaged
  over fixtures (higher is better, threshold = 1.0 at COMPACT/VERBOSE),
- `fact_i = |parsed_facts(z_i) intersect parsed_facts(raw)| /
   |parsed_facts(raw)|` averaged over fixtures (higher is better).
  This is a continuous companion to the binary `must_preserve_ok`:
  e.g. for grep it counts what fraction of distinct (path, line)
  pairs from the raw output survive the compression; for pytest it
  counts what fraction of failed-test names survive.

A point `theta_i` is **dominated** by `theta_j` iff
- `R_j <= R_i` AND
- `recall_j >= recall_i` AND
- `fact_j >= fact_i` AND
- at least one inequality is strict.

The Pareto frontier `F_s` is the set of non-dominated points. A naive
n^2 dominance scan over <=480 points is sub-millisecond.

### Knee point as the shipped default

The frontier is a 3-D surface, but in practice
`recall_i = 1.0` for the vast majority of points (must-preserve is
already invariant to most knob settings - the patterns capture
test-names, paths, error codes that the parser extracts before the
formatter clips). So the operative tradeoff collapses to a 2-D curve
(R, fact) on the slice `recall = 1.0`. The knee is

    knee = argmin_i  alpha * R_i  -  (1 - alpha) * fact_i

with `alpha = 0.5` as the neutral default. This is cosine-distance to
the ideal (1, 0); equivalent up to monotone reparameterisation to the
Lagrangian L = R - lambda * fact at lambda = (1-alpha)/alpha = 1.
Sensitivity sweep over `alpha in [0.3, 0.7]` shipped as a small table
in the report so the maintainer can pick a different operating point
with one TOML edit.

### Why this differs from V01 and V10

- V01 picks an operating *tier* per call from a fixed (R_t, D_t) table
  using a Lagrangian on budget pressure; the knobs of each
  compressor stay frozen.
- V10 sets the same knobs, but uses the IB Lagrangian
  L = I(X;Z) - beta * I(Z;T) with deterministic surrogates. As V10
  itself notes, the deterministic IB collapses to constrained recall
  search and the Lagrangian becomes a justification rather than a
  search procedure.
- V87 treats this purely as a Pareto frontier extraction over a
  discrete grid, no Lagrangian, no theory. The frontier itself is
  the deliverable; picking a knee from it is a one-line decision the
  maintainer makes once.

## Concrete proposal for Redcon

### Files (research-only, do NOT modify production)

The implementation is sketched as a hypothetical CLI extension:

- `redcon/cmd/benchmark.py` (existing, ~315 LOC) - extend with
  `--sweep` mode that takes the parameterised compressor adapters
  below and runs the Pareto pipeline. Net diff ~150 LOC.
- `redcon/cmd/_pareto_adapters.py` (new, ~250 LOC) - a thin per-
  compressor wrapper that exposes `params_grid()`, `with_params(theta)`,
  and `extract_facts(raw_bytes) -> set[str]`. Required because the
  current compressors are stateful constants in module scope, not
  parameter dataclasses.
- `redcon/cmd/_pareto_results.toml` (emitted artefact) - the per-
  compressor frontier and the chosen knee, hand-reviewed before merge.
- `tests/test_cmd_pareto_sweep.py` (new, ~80 LOC) - asserts the sweep
  is deterministic (run twice, frontiers byte-identical) and that the
  shipped knee for each compressor is on the frontier.

### Sweep API sketch

```python
# redcon/cmd/_pareto_adapters.py
@dataclass(frozen=True, slots=True)
class SweepConfig:
    schema: str
    knobs: dict[str, tuple[int, ...]]
    fact_extractor: Callable[[bytes], set[str]]

def grid_iter(cfg: SweepConfig) -> Iterator[dict[str, int]]:
    keys = list(cfg.knobs)
    domains = [cfg.knobs[k] for k in keys]
    for combo in itertools.product(*domains):
        yield dict(zip(keys, combo))

def evaluate_point(adapter, theta, fixtures, level):
    R_acc, recall_acc, fact_acc = 0.0, 0.0, 0.0
    for raw, argv, mp_patterns in fixtures:
        comp = adapter.with_params(theta)
        out = comp.compress(raw, b"", _force_level(level)).text
        R_acc += estimate_tokens(out) / max(1, estimate_tokens(raw))
        recall_acc += int(verify_must_preserve(out, mp_patterns, raw.decode()))
        truth = adapter.fact_extractor(raw)
        seen = adapter.fact_extractor(out.encode())
        fact_acc += len(truth & seen) / max(1, len(truth))
    n = len(fixtures)
    return R_acc / n, recall_acc / n, fact_acc / n

def pareto_frontier(points):
    """O(n^2) dominance scan; n <= 480 so this is microseconds."""
    front = []
    for p in points:
        if not any(_dominates(q, p) for q in points if q is not p):
            front.append(p)
    return front

def knee(front, alpha=0.5):
    return min(front, key=lambda p: alpha * p.R - (1 - alpha) * p.fact)
```

### Fact extractors per compressor

These are intentionally simple deterministic functions, so the
"recall" measurement does not invent facts the compressor's parser
hasn't already enumerated. Required deliverable:

  schema       fact set
  git_diff     {path strings} from `diff --git a/X b/X` lines
  pytest       {test_id} from `FAILED test::name` and PASSED counts
  grep         {(path, line_no)} from match headers
  find         {path} from each emitted line
  ls -R        {(dir, basename)} from header + entry
  lint         {(path, line, code)} from rule-format regex
  docker_ps    {container_id[:12]} from CONTAINER ID column
  docker_build {step_instruction first 60 chars} from RUN/COPY/etc.
  git_log      {short_sha} from each commit header
  git_status   {(path, status)} from each XY entry
  kubectl      {resource_name} from NAME column
  pkg_install  {(action, package)} from each operation line
  http_log     {(path, status_count)} from path histogram

### Sweep mode entry point

```
python -m redcon.cmd.benchmark --sweep \
       [--compressor pytest] \
       [--alpha 0.5] \
       [--out _pareto_results.toml] \
       [--md _pareto_report.md]
```

Output is two artefacts:

1. `_pareto_results.toml` machine-readable:
   ```toml
   [pytest]
   knee_alpha = 0.5
   knee = {snippet = 6, body = 4, clip = 80}
   knee_R = 0.231
   knee_recall = 1.000
   knee_fact = 0.978
   shipped_default = {snippet = 8, body = 5, clip = 200}
   shipped_R = 0.262
   shipped_recall = 1.000
   shipped_fact = 1.000
   pareto_size = 17
   delta_R_pp = +3.1   # knee beats shipped by 3.1 pp at fact_drop = 2.2 pp
   ```

2. `_pareto_report.md` human-readable: per-compressor Pareto plot
   (textual, reduction on x, fact-coverage on y, recall annotated),
   knee marked, shipped default marked.

### Determinism

The sweep is deterministic by construction: same fixtures + same
parameter grid + same fact extractor (pure-Python, no random) ->
byte-identical TOML on every run. CI gate: `make pareto-sweep`
should leave `_pareto_results.toml` unchanged unless code has moved.

## Estimated impact

- **Token reduction (COMPACT tier, predicted via the math above)**:
  - git_diff: paths[:8] -> [:6] saves ~6 tokens per huge-diff
    output (current compact ~244 tokens). +1-2 pp.
  - pytest: clip 200 -> ~80 saves ~12 tokens per failure x 30
    failures over current ~600 compact tokens. +3-4 pp. Matches
    V10's prediction within rounding (V10 is the analytic version
    of the same calculation).
  - grep: items[:3] -> [:2] when match texts are long; gated by
    fact_coverage on path set (always 1.0 because path is in the
    header). +1-2 pp.
  - lint: file_limit 30 -> tuned per fixture distribution
    (likely 15-20 on the M8 fixtures with long-tail). +1-2 pp.
  - docker: instruction clip 120 -> 90; warnings[:10] -> [:5].
    +1 pp.
  - listing: by_dir[:30] -> [:20], names[:8] -> [:6]. +1 pp.
  - git_log: body[:3] -> [:1] on COMPACT entry list. +1 pp.
  - http_log: top_paths[:20] -> [:10]. +1 pp.
  - kubectl, pkg_install, find: marginal (parsed lists are short
    on M8 fixtures and headers dominate). 0 pp.
  - **Aggregate**: +1-3 pp average across 8 of 11 compressors at
    COMPACT. Doesn't move the headline reduction by >=5 pp on a
    single compressor, but compounds with V01 (per-tier choice) and
    V10 (analytic IB) and would be the empirical witness for both.

- **Latency**: production hot path unchanged (the sweep only runs
  offline). Cold-start unaffected (defaults are still read as
  module constants, just sourced from the TOML knee). Warm-call
  unchanged.

- **Affects**: `redcon/cmd/benchmark.py` (new mode); `_pareto_adapters.py`
  (new); the static defaults inside each compressor would be edited
  in a follow-up PR (out of scope for this research note - V87 only
  produces the artefact, V10's PR consumes it).

## Implementation cost

- Lines of code: ~150 LOC sweep extension + ~250 LOC adapters +
  ~80 LOC tests. ~480 LOC. No production source changes for V87
  itself; the proposed adapter layer is the same one V10 needs and
  can be shared between the two vectors.
- New runtime deps: none. itertools.product, dataclasses, tomllib
  for write are stdlib; benchmark.py already imports
  `tests.test_cmd_quality.CASES` so the fixture corpus is reachable.
- Risks to determinism: zero. Sweep is offline; fixture corpus is
  versioned in tests; parameter grid is hard-coded; tie-breaking
  in `min()` is stable on Python (first-seen wins).
- Risks to robustness: zero. Adversarial inputs are still run
  through the existing `_check_robustness` harness; sweep operates
  only on the curated fixture corpus.
- Risks to must-preserve: zero. Sweep enforces `recall == 1.0` as
  a feasibility constraint at COMPACT/VERBOSE before considering
  any point for the frontier. ULTRA frontier is reported but the
  knee is computed only on the recall-feasible slice for each
  level. A point that fails must-preserve is dropped, not down-
  weighted. So the contract is preserved by construction.
- Corpus-overfit risk: medium. The M8/M9 fixtures are 2-3 per
  schema; a knee chosen on `pytest_massive` may not hold on a real
  60-failure run. Mitigations:
  1. Report the alpha-sensitivity table per compressor; if the
     knee shifts radically across alpha in [0.3, 0.7] do not ship
     the new default, ship the existing one (Bayesian shrinkage to
     prior).
  2. Add a corpus-augmentation phase: synthesise per-compressor
     pathological inputs (50 failures, 1000-line grep, 200-step
     docker build) and require the knee survive must-preserve on
     them too. This is V85's territory (adversarial generator) and
     a natural cross-vector compose.
  3. Refuse to ship a knee whose `R` improvement is less than the
     fixture-corpus standard error (compute by jackknife).

## Disqualifiers / why this might be wrong

1. **Already partially implemented in disguise.** The benchmark
   harness already runs each compressor at all three levels and
   reports `reduction_pct` and `must_preserve_ok`. What V87 adds is
   (a) the parameter sweep loop and (b) a Pareto frontier extractor.
   The extractor is ~30 LOC; the sweep is the substantive work and
   it requires every compressor to expose its constants as a
   `params_grid` (currently they are module-scope literals). Without
   that refactor V87 *is the refactor*.

2. **Knee-overfit on a tiny corpus.** With 2-3 fixtures per schema
   and recall hitting 1.0 across most of the grid, the Pareto
   frontier is small (5-20 points typically) and the knee is
   determined by R alone on the recall=1 slice. R is then
   minimised by setting every limit knob to its smallest value,
   which trivially shrinks output but loses fact-coverage on
   inputs the corpus does not represent. The fact-coverage axis
   is supposed to defend against this but only if the fact set is
   rich; for header-dominated compressors (kubectl, ls -R) the
   fact set is small and the knee might suggest absurdly tight
   limits.

3. **The Pareto framing is overkill for binary recall.** When
   recall is 1 across most of the grid, the only operative axes
   are R and fact. When fact is also saturated near 1 (because the
   fact extractor matches what the parser already preserves), R is
   the only axis that varies and the "frontier" collapses to a
   single point: the configuration with the smallest output. At
   that point Pareto reduces to "minimise R subject to recall = 1
   and fact = 1", which is the constrained-optimisation formulation
   in V10 and the calibration-table formulation in V01. The
   frontier visualisation is pedagogically nice, but the decision
   is the same as V10's argmin.

4. **Cross-tier interaction.** Knobs interact across tiers:
   git_diff `paths[:8]` is used at COMPACT but the same data
   feeds ULTRA's path-only output. A knee chosen on COMPACT might
   regress ULTRA's reduction floor. The sweep needs to evaluate
   per (theta, level) jointly, and the shipped knee for the knob
   needs to satisfy *every* tier's floor. This widens the
   feasibility set test but the search-space size is unchanged.

5. **Per-compressor maintenance cost.** Each new compressor must
   ship a `params_grid` and a `fact_extractor`. That is an
   ongoing tax on contributors. If the project adds 5 more
   compressors per quarter (current pace per BASELINE) the sweep
   adapter layer must keep up. Not a blocker but a real
   long-tail engineering cost.

## Verdict

- Novelty: **low**. Pareto sweep over discrete knobs is textbook
  multi-objective optimisation. The vector is the empirical witness
  to V10's analytic claim, not an independent breakthrough. Its
  contribution is *plumbing* - a reusable sweep harness that any
  future researcher can rerun in 30 seconds to validate a knob
  change. V10 makes the same numerical case with an IB Lagrangian
  framing; V87 makes it with a frontier-and-knee framing.
- Feasibility: **high**. Stays inside the existing benchmark harness,
  uses only stdlib + the existing fixture corpus, is fully offline,
  preserves all four BASELINE constraints (deterministic, no
  network, no embeddings, must-preserve enforced as a hard
  feasibility constraint).
- Estimated speed of prototype: **2-3 days**. ~480 LOC of code,
  most of it adapter glue plus a small frontier extractor and a
  TOML emitter. Pareto extraction itself is trivial (n^2 over
  <=500 points).
- Recommend prototype: **conditional-on-X**, where X = "we are
  also doing V10 or V01". As a standalone vector V87 produces a
  TOML of new defaults which is useful but small. As the empirical
  half of V10 it is the only thing standing between
  "principled-but-unverified IB defaults" and "measured-and-shipped
  defaults"; in that combined PR the work is ~600 LOC for V10 + V87
  together rather than ~400 + ~480 in two separate PRs (the adapter
  layer is shared). Recommend bundling.
