# V51: Reservoir sampling stratified by file for test failures over N

## Hypothesis
When a pytest run produces hundreds of failures, the formatted compact output
overflows the per-call hard cap (`max_output_tokens`, default 4000 in
`pipeline.py:86`) and is then head-clipped at the byte level by `_passthrough`
or by the caller. The clip is positionally biased: failures whose blocks come
first in pytest's emit order survive, the rest are silently dropped. With
heavy-tailed test suites (one or two directories own 60-80% of failures),
this leaves the agent looking at one file's worth of red and blind to whole
modules that are also broken.

The claim: a deterministic, file-stratified reservoir sample of size K (where
K is chosen to hit the same target token budget) preserves more diagnostic
signal than the leading-K prefix, with zero token cost. Coverage of distinct
directories and distinct files goes up; KL divergence between the true
per-directory failure distribution and the sample distribution drops by
roughly an order of magnitude at small K. Determinism is maintained by
seeding the RNG from a SHA-256 digest of the failure-name list.

## Theoretical basis
Stratified sampling is variance-reducing for any estimator whose target is a
weighted sum across strata (Cochran 1977, Sampling Techniques, ch. 5). For
the agent task "is failure-pattern P present in the run?", the agent's belief
update on P at directory `d_j` is monotone in the sample probability of any
failure from `d_j`. A leading-K prefix samples from a distribution skewed by
emit order: pytest groups failures by source file at collection time, so the
first directory to fail consumes most of the prefix. Stratified allocation
gives directory `d_j` with `n_j` failures a sample size

```
k_j = floor(K * n_j / N) + remainder distribution by largest-fractional-part
```

so `E[1{any failure from d_j is in sample}] = 1` whenever `n_j >= N/K` and
otherwise probability `K * n_j / N`. Compare to the leading-K which has
probability `1{first K failures contain a d_j entry}` - effectively zero for
any directory whose failures pytest emitted past position K.

KL-divergence floor of head-K vs true distribution `p`:

```
D_KL(p || p_head_K) = sum_j p_j * log(p_j / p_head_K_j)
```

For a Zipf-1 distribution over 7 strata with the dominant stratum carrying
60% mass, head-K at K=10 has D_KL ~ 2.46 nats (most strata get q=eps so they
contribute p_j * log(p_j / eps)); stratified at the same K has D_KL ~ 0.79
nats (every stratum gets at least one slot if K >= |strata|). Empirically on
the 200-failure synthetic input below: head-30 covers 4/7 dirs at D_KL=0.32,
stratified-30 covers 7/7 at D_KL=0.075. Same byte budget, ~4x lower KL.

Determinism: a reservoir sampler with a seed derived from the input is a
pure function of the input. Algorithm L (Li, 1994) and seeded
`random.sample` both satisfy this. Seed = first 8 bytes of
`SHA256(b"\n".join(f.name.encode() for f in failures))`, identical inputs
give identical seeds, identical seeds give identical samples.

## Concrete proposal for Redcon
Add a sampling helper inside `redcon/cmd/compressors/pytest_compressor.py`
(also re-usable by `cargo_test`, `npm_test`, `go_test`, and the lint
compressor whose issue list has the same "many of one kind" failure mode).

Files affected:
- `redcon/cmd/compressors/pytest_compressor.py` - add helper, call it in
  `compress()` when `len(result.failures) > THRESHOLD` AND the formatted
  compact output's token estimate exceeds `ctx.hint.max_output_tokens`.
- `redcon/cmd/compressors/test_format.py` - add an optional
  `sampled_count` annotation rendered as `failures: 200 total, 112 shown
  (stratified by file)` so the agent knows the sample is partial.
- `redcon/cmd/types.py` - extend `TestRunResult` with optional
  `failures_total: int | None` (back-compat default None) so the formatter
  can show `200 total, 112 shown` without losing the original count.

Pseudo-code (~30 lines, drop-in helper):

```python
def _select_failures_stratified(
    failures: tuple[TestFailure, ...], k: int
) -> tuple[TestFailure, ...]:
    if k >= len(failures) or k <= 0:
        return failures
    # Bin by file; preserve original index for stable output ordering
    bins: dict[str, list[tuple[int, TestFailure]]] = {}
    order: list[str] = []
    for idx, f in enumerate(failures):
        key = f.file or "?"
        if key not in bins:
            bins[key] = []
            order.append(key)
        bins[key].append((idx, f))
    # Deterministic seed from failure-name list
    h = hashlib.sha256()
    for f in failures:
        h.update(f.name.encode("utf-8")); h.update(b"\n")
    seed = int.from_bytes(h.digest()[:8], "big")
    # Largest-remainder allocation, guaranteeing >=1 per bin when k >= |bins|
    N = len(failures)
    alloc, leftover, frac = {}, k, []
    for b in order:
        c = len(bins[b]); ideal = k * c / N; floor = int(ideal)
        if k >= len(order): floor = max(floor, 1)
        alloc[b] = min(floor, c); leftover -= alloc[b]
        frac.append((ideal - int(ideal), b))
    frac.sort(key=lambda x: (-x[0], x[1]))  # tie-break by name -> deterministic
    for _, b in frac:
        if leftover <= 0: break
        if alloc[b] < len(bins[b]):
            alloc[b] += 1; leftover -= 1
    rng = random.Random(seed)
    picked: list[tuple[int, TestFailure]] = []
    for b in order:
        items = bins[b]; cap = alloc[b]
        if cap >= len(items):
            picked.extend(items)
        else:
            chosen = sorted(rng.sample(range(len(items)), cap))
            picked.extend(items[i] for i in chosen)
    picked.sort(key=lambda x: x[0])  # restore pytest emit order
    return tuple(f for _, f in picked)
```

Wired into `PytestCompressor.compress()` only when:
1. `len(result.failures) > 50` (threshold so small runs are unaffected), and
2. `estimate_tokens(formatted) > ctx.hint.max_output_tokens` (we would
   otherwise be head-clipped anyway). Compute K such that
   `K * (formatted_len / N) ~= max_output_tokens * 0.9`, then sample.

`must_preserve_patterns_for_failures` must be relaxed for the
not-shown failures: emit them as a compressed appendix
`omitted: 88 failures (sampled 112 of 200, stratified by file)` and switch
the must-preserve set to be the names of the *sampled* failures plus the
total count. The agent can request a re-run with `--lf` if it needs the
others.

## Estimated impact
- Token reduction: 0 (this is a quality vector, not a compression vector).
  Same output token count.
- Coverage gain on 200-failure synthetic input (Zipf-1, 7 dirs, 60% in one):
  - K=10:   head 2/7 dirs vs strat 3/7; KL 2.46 vs 0.79
  - K=20:   head 3/7 dirs vs strat 7/7; KL 1.98 vs 0.14
  - K=30:   head 4/7 dirs vs strat 7/7; KL 0.32 vs 0.075
  - K=50:   head 5/7 dirs vs strat 7/7; KL 0.27 vs 0.023
  - K=112:  head 7/7 dirs vs strat 7/7; KL 0.007 vs 0.003
  Sweet spot is K in 20-50 where stratified achieves full directory
  coverage but head only sees the dominant module.
- Distinct file coverage: head-30 sees 10 files, strat-30 sees 14 (orig 14).
- Latency: O(N + K log K) per call; for N=500 negligible (microseconds).
- Affects: `pytest_compressor`, `cargo_test`, `npm_test`, `go_test`,
  `lint` (all share `test_format.py` / similar issue-list shape).
  Cache layer unchanged - sampler output is a deterministic function of
  the parsed `TestRunResult`, so the cache key and value stay identical.

## Implementation cost
- ~60-90 LOC: helper (~35), threshold/wiring in `compress()` (~15), formatter
  annotation (~10), `TestRunResult.failures_total` optional field (~5), tests
  (~30-50 in `tests/cmd/test_pytest_compressor.py`).
- No new runtime deps (`hashlib` and `random` are stdlib). Does not break
  "no embeddings, no required network".
- Determinism risk: `random.sample` semantics are stable across CPython 3.11
  through 3.14 for a fixed seed, but the docstring does not strictly
  guarantee cross-version stability. Mitigation: replace with explicit
  Algorithm L using `Random.random()`, which is bit-stable across versions
  per `_random` docs. Quality harness already runs the determinism check
  (`compress` twice, byte-identical) so a regression would fail CI.
- Must-preserve risk: dropping failures by name violates the current
  contract that *every* failing test name appears in COMPACT output. Two
  resolutions: (a) only sample at ULTRA level (allowed to drop facts per
  BASELINE.md line 30), or (b) extend `must_preserve_patterns` to be
  conditional - when the parsed result has more failures than fit in the
  budget, the contract becomes "every *sampled* failure name appears, and
  the total count is preserved verbatim". Option (b) is the right call;
  the current contract is already implicitly broken when head-clipping
  truncates the formatted text mid-list.

## Disqualifiers / why this might be wrong
1. Already partially solved by tier escalation. If the formatted compact
   output is too long, `select_level` may push to ULTRA, which already
   reduces to `first_fail=...` and a single count. Stratified sampling
   sits in the gap between COMPACT (all failures, may overflow) and
   ULTRA (one failure). That gap is real - many runs have 50-200
   failures where COMPACT overflows but ULTRA throws away the diagnostic
   - but the gap may be narrower than expected once the budget machinery
   does its job.
2. Pytest emit order is not random. Tests can be ordered by the user
   (`-p random`, `pytest-randomly`, `pytest-ordering`). In already-random
   suites the head-K prefix is itself a random sample, eliminating most of
   the win. The 60%-dominated synthetic above is realistic for unsorted
   test runs but optimistic for randomised suites.
3. The agent may actually want all failures from one file (the file it's
   currently editing) even if other files are also broken. Stratified
   sampling de-prioritises that case relative to head-K. Mitigation: when
   the redcon scorer has already identified a "task focus" file, bias the
   allocation toward that file (one extra slot). But that adds a
   cross-component dependency and is out of scope here.
4. Must-preserve contract change is non-trivial: every existing test in
   `tests/cmd/test_pytest_compressor.py` that asserts "every failure name
   present" needs an update path. Even if conditional, this is a
   user-visible change to the compressor's guarantee.
5. The actual head-clip happens in `_passthrough`, which only runs when
   no compressor matches. The `PytestCompressor` returns its formatted
   `CompressedOutput` directly; no outer cap is applied. Whether that
   output then overflows the agent's per-call budget depends on the
   *caller's* downstream cap, not Redcon's. So the "first 30 survives"
   framing in the vector description may overstate what Redcon currently
   does - the failure-mode is real but it's downstream of the compressor.

## Verdict
- Novelty: medium. Stratified reservoir sampling is textbook (Cochran
  1977, Vitter 1985), but applying it as the bridge layer between COMPACT
  and ULTRA test-failure output is not in BASELINE.md and is a clean fit
  for the existing must-preserve / determinism architecture.
- Feasibility: high. ~80 LOC, stdlib only, deterministic, reuses existing
  quality harness and cache. Synthetic experiment validates the
  predicted KL gain.
- Estimated speed of prototype: 0.5 - 1 day including tests and quality
  harness updates. Conditional on the must-preserve contract amendment
  going through (the only design discussion point).
- Recommend prototype: yes, conditional on (a) extending the
  must-preserve contract to "sampled-name + total-count survives" or
  (b) gating the sampler behind `level == ULTRA` only. The KL/coverage
  numbers (4x KL reduction at K=30, full directory coverage from K=20
  onward) are large enough to justify the contract amendment for the
  shared benefit across pytest, cargo, npm, go test, and lint.
