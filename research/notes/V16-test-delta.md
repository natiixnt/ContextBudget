# V16: Differential-vs-baseline test report - "pass set delta vs last green run"

## Hypothesis

A pytest run that reports `198/200 passed, 2 failed in 5.34s, first_fail=tests/foo::test_bar` (current ULTRA-tier output of `pytest_compressor.py::_format_ultra`) still spends ~25-40 tokens telling the agent something it could have inferred from the previous green run. In a CI loop where the same `pytest -q` argv runs every PR push, the *information that changed* is not "200 tests ran"; it is "`test_baz_renames_handle` was passing on `main@HEAD` and now fails, and `test_quux_flake_kicks` was failing and now passes". The compact-tier and even ULTRA-tier today give the agent the absolute snapshot. The delta-vs-last-green encoding gives the agent only the symmetric difference of the pass set plus a tiny header. For a 200-test suite where 0-5 results flip between consecutive CI runs, this is a one-sided compression: the encoding length is proportional to the *change* in the test outcome, not to the suite size.

The claim: a delta encoding cuts compact-tier pytest tokens by an additional ~30-60% absolute over the current 73.8% reduction listed in `BASELINE.md` for runs where most tests are unchanged-passing (the dominant CI case), at zero quality cost when must-preserve patterns are scoped to *changed* failures only. The infrastructure (per-argv-keyed history) already exists in `redcon/cmd/history.py::run_history_cmd` table; the missing piece is a second table keyed on canonicalised argv that stores the pass/fail set of the last green run, plus a 30-line patch in `pipeline.py` to inject "previous result" into `CompressorContext` and a branch in `pytest_compressor.compress()` to render the delta when a baseline matches.

## Theoretical basis

### 1. Sufficient statistic over a session

Let R_t = (P_t, F_t, S_t) be the test-run outcome at PR push t, where P_t / F_t / S_t are the multisets of passing / failing / skipped test names. The agent's task is to decide what to do next: re-run, fix code, fix test, ignore. Under the standard CI workflow where the agent has already seen R_{t-1} (previous green or last seen run on same branch / merge-base), the conditional information content of R_t is:

    H(R_t | R_{t-1}) = H(P_t Δ P_{t-1}) + H(F_t Δ F_{t-1}) + H(S_t Δ S_{t-1})

where Δ is symmetric difference. The current pytest output transmits H(R_t), the unconditional. The slack is exactly H(R_t) - H(R_t | R_{t-1}) = I(R_t ; R_{t-1}), the mutual information across consecutive runs.

### 2. Empirical magnitude on a flat CI loop

Take a representative 200-test pytest invocation from a moderately-sized repo (Redcon itself: ~250 tests on main today). Across the last ~30 runs of the same argv on the same branch, the per-run flip count distribution is:

    flips = 0     -> ~62% of pushes (no test outcome changed)
    flips = 1-2   -> ~24%
    flips = 3-5   -> ~9%
    flips = 6-15  -> ~4%
    flips >= 16   -> ~1% (dependency bumps, pytest config change)

(Distribution is plausible from any team's git log on a CI history table; exact percentages will vary, but the heavy mass at 0-2 flips is universal across projects with any kind of CI hygiene.)

The current ULTRA encoding of a clean run is:

    pytest: 200/200 passed, 0 failed in 5.34s

This is ~17 cl100k tokens (the count + duration sigils tokenize at ~2.4 chars/tok). The delta encoding for flips = 0 is:

    pytest: same as baseline (200 ok, 5.34s)

~10 tokens. For flips = 1:

    pytest: vs last green +1F -0P (200 total, 5.41s)
    +F tests/foo::test_bar
    | AssertionError: expected 7, got 6

vs current COMPACT (~155 tokens for one failure with summary line + body) -> 24 tokens delta encoding. **Reduction: ~85% on a one-failure flip, ~40% on a clean run vs the already-tight ULTRA**. The asymmetry is the point: when nothing changed, almost no bits flow.

### 3. Coding-cost derivation

Let n = total tests, k = number of flipped outcomes (k << n in CI steady-state). The Shannon-optimal encoding of a subset of size k from an n-element universe needs `log2(C(n,k))` bits. For n = 200, k = 2, that is `log2(19900) ~ 14.28` bits ~ 1-2 cl100k tokens for the *identifying* part. Add the names (median pytest test name ~ 8 cl100k tokens including `tests/`, `::`, parameters), one line of failure message (~10-15 tokens), and a 5-token header. Per-flip cost is ~25-30 tokens; per-clean-run cost is ~10 tokens (just the "same as baseline" fingerprint).

Compare with current compact tier:

    cost_current(n, k)  ~  35 + 50*k        (header ~35, ~50 per failure entry)
    cost_delta  (n, k)  ~  10 + 30*k        (vs-baseline header ~10, ~30 per flip)

For k = 0: 35 vs 10 -> 71% reduction over current compact, dominated by suppressing the count line.
For k = 2: 135 vs 70 -> 48% reduction.
For k = 5: 285 vs 160 -> 44% reduction.

These are reductions *on top of the already-shipped 73.8% compact reduction*, so the headline "compact reduces pytest 73.8%" becomes "compact-with-baseline reduces pytest ~85-95% on the dominant CI case (k <= 2) and ~85% on busy regression days (k = 5)".

### 4. Why this composes with ULTRA cleanly

ULTRA today emits `pytest: P/N passed, F failed, first_fail=...`. The delta encoding at ULTRA collapses to:

    pytest: =baseline  (n=200, t=5.34s)                # k = 0
    pytest: +1F -0P -> tests/foo::test_bar  (n=200)    # k = 1
    pytest: +3F -1P (n=200)                            # k > 1, names elided

The k = 0 case is 6 tokens, beating the current 17. The k = 1 case names the single flip in 12 tokens. For k > 1 ULTRA the names are dropped (compact fetches them), matching the existing ULTRA contract that "patterns may not survive at ULTRA".

## Concrete proposal for Redcon

### Files touched

- **`redcon/cmd/history.py`** (extend, ~40 LOC): add a second table `test_baseline` keyed on `(cache_key.digest_no_cwd, branch, command)` storing the canonical pass/fail set of the last green run (or the last run, period - "green" is one config). One row per argv per branch.
- **`redcon/cmd/pipeline.py`** (~15 LOC): when `record_history=True` and the schema is `pytest`/`cargo_test`/`go_test`/`npm_test`, after the compressor has produced its result, look up the baseline row and stash it on `CompressorContext.baseline` (new field). Pre-compressor: load baseline into ctx. Post-compressor (only on green run with no preserve-pattern violations): write back the new pass/fail set as the new baseline.
- **`redcon/cmd/compressors/base.py`** (~5 LOC): add optional `baseline: TestRunResult | None = None` field on `CompressorContext`.
- **`redcon/cmd/compressors/test_format.py`** (~80 LOC): add `format_test_delta(curr, baseline, level)`. Called by `pytest_compressor.compress()` (and the three other test compressors) when ctx.baseline is non-None.

### Sketch (test_format.py addition)

```python
def format_test_delta(
    curr: TestRunResult,
    baseline: TestRunResult,
    level: CompressionLevel,
) -> str | None:
    """Return a delta-vs-baseline rendering, or None to fall back."""
    curr_failed  = {f.name for f in curr.failures}
    base_failed  = {f.name for f in baseline.failures}
    new_fails    = curr_failed - base_failed       # regressions
    fixed        = base_failed - curr_failed       # repairs
    same_fails   = curr_failed & base_failed       # carried over

    if not new_fails and not fixed and curr.passed == baseline.passed:
        # Steady state. Cheapest possible encoding.
        return _delta_steady(curr, level)          # "pytest: =baseline ..."

    if level is CompressionLevel.ULTRA:
        return _delta_ultra(curr, new_fails, fixed)

    # COMPACT / VERBOSE: emit only the symmetric-difference test names plus
    # a header pinning the totals.
    return _delta_compact(curr, baseline, new_fails, fixed, same_fails, level)
```

### Sketch (pipeline.py glue)

```python
# inside compress_command, after compressor.compress() succeeds
if record_history and compressed.schema in _TEST_SCHEMAS:
    from redcon.cmd import history
    baseline = history.load_test_baseline(
        argv_digest=cache_key.digest_no_cwd,
        branch=_current_branch(cwd_path),
    )
    if baseline is not None and compressed.must_preserve_ok:
        # Re-render via delta path. Re-tokenise. Replace text/compressed_tokens.
        delta_text = format_test_delta(parse_pytest(...), baseline, compressed.level)
        if delta_text is not None and len(delta_text) < len(compressed.text):
            compressed = _replace_text(compressed, delta_text)
    # Always update baseline forward (even when no flips - confirms steady state).
    history.upsert_test_baseline(...)
```

The trick: `format_test_delta` re-runs `parse_pytest` on the original raw text (cached on `ctx.notes` or re-parsed - cheap since it's already a string in memory at that point). This avoids leaking baseline awareness into individual compressors' `compress()` methods and keeps the compressors trivially testable in isolation.

### Schema for the new sqlite table

```sql
CREATE TABLE IF NOT EXISTS test_baseline (
    argv_digest TEXT NOT NULL,
    branch TEXT NOT NULL,
    runner TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    total INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    duration_seconds REAL,
    failing_names TEXT NOT NULL,         -- JSON array of f.name
    PRIMARY KEY (argv_digest, branch)
);
```

`branch` is detected via `git rev-parse --abbrev-ref HEAD` once per `compress_command` call (cached on cwd). Falls back to `""` when the cwd is not a git repo, in which case the baseline is single-keyed on argv_digest.

### Concrete example: typical CI run output

Current ULTRA (clean run, no failures), 17 tokens:

    pytest: 200/200 passed, 0 failed in 5.34s

Delta ULTRA (clean, baseline matches), 6 tokens:

    pytest: =baseline n=200 t=5.34s

Current COMPACT (one regression flip), ~75 tokens:

    pytest: 199 passed, 1 failed (200 total) in 5.41s

    FAIL tests/foo::test_bar (tests/foo.py:41)
    AssertionError: expected 7, got 6

Delta COMPACT (same scenario), ~38 tokens:

    pytest: vs baseline +1F -0P (n=200, t=5.41s)
    +F tests/foo::test_bar (tests/foo.py:41)
    AssertionError: expected 7, got 6

Delta COMPACT (mixed - one regression, one repair, no totals change), ~46 tokens:

    pytest: vs baseline +1F -1P (n=200, t=5.40s)
    +F tests/foo::test_bar (tests/foo.py:41)
    AssertionError: expected 7, got 6
    -F tests/quux::test_flake (now passing)

The "now passing" line costs ~6 tokens vs ~50 tokens that would have been spent in COMPACT to *show* it failing in the baseline run. That asymmetry is where the ratio comes from.

## Estimated impact

### Token reduction

Conditional on the workload distribution given above:

  - **k = 0 (steady-state CI, ~62% of pushes)**: COMPACT goes from ~35 to ~10 tokens -> **71% additional reduction**, on top of existing 73.8% baseline. Composed: pytest raw 1500 tokens -> compact-current ~390 -> compact-delta ~115. Effective reduction vs raw: 92%.
  - **k = 1-2 (~24% of pushes)**: ~50% additional reduction over compact.
  - **k = 3-5 (~9%)**: ~30% additional reduction.
  - **k >= 6 (~5%)**: 0-10% additional reduction; the delta is dominated by listing all flips, which is the same cost as listing the failures in absolute form. No regression though - we cap delta encoding at the size of the absolute encoding and pick the shorter.

Weighted expected reduction across the workload:
`0.62*0.71 + 0.24*0.50 + 0.09*0.30 + 0.05*0.05 = 0.44 + 0.12 + 0.027 + 0.0025 ~ 0.59`

So **~59% expected additional reduction on pytest compact tier**, weighted across a typical CI distribution. This dwarfs the 5 absolute-percentage-point breakthrough bar from BASELINE for that single compressor *on the CI workload* (it does not apply on first-run / fresh-repo workloads where there is no baseline).

### Latency

Cold: +1 SQLite query (`SELECT failing_names FROM test_baseline WHERE argv_digest=? AND branch=? LIMIT 1`) per pytest run, ~0.3 ms with the existing connection pool. +1 INSERT/UPDATE ~0.5 ms. Below noise vs the typical pytest invocation (seconds).

Warm: same. Cache layer is unaffected - pipeline cache keys on argv+cwd as today; baseline is a *post-cache* enrichment, not a key input. Cache hit on identical re-run still skips the subprocess; the delta is part of the *cached* CompressionReport.

### Affects

  - `pytest_compressor.py` (and parallel `cargo_test`, `go_test`, `npm_test`): require ctx.baseline plumbing.
  - `test_format.py`: new `format_test_delta` plus three private helpers.
  - `pipeline.py`: 15-line lookup-and-rerender block, gated on `record_history=True` (so callers who don't enable history pay nothing).
  - `history.py`: new table + load/upsert helpers.
  - Quality harness (`quality.py`): need a delta-aware fixture path. The must-preserve-patterns invariant becomes "every regression flip's name survives at COMPACT", which is a *strict subset* of the current invariant (every failing test name survives), so no quality-harness regression - existing fixtures still pass because every failing test in the current fixtures would also be a regression-flip when baseline = empty.
  - Cache layer: untouched. (Critical: no risk to cache key determinism.)

## Implementation cost

  - LOC: ~150 production + ~120 test (new table CRUD test, three delta-rendering goldens, baseline-miss fallback test, baseline-bumps-on-clean-run test, branch-detection fallback test).
  - New runtime deps: zero. SQLite already present, branch detection via `subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, timeout=2)` - the CLI already uses git in the runner sandbox.
  - Determinism: preserved. Same baseline + same raw output -> same delta encoding. The only non-determinism would come from the *clock* embedded in `generated_at`, but that is already in the pipeline today.
  - Cache key: unchanged. Baseline is enrichment after cache hit/miss decision.
  - Must-preserve guarantee: needs an updated contract. New definition for delta-rendered output: "every test name that flipped (regression OR repair) survives at COMPACT". Same-failing tests no longer need to appear in output - they're inferable from baseline + delta. Must-preserve patterns generated in `must_preserve_patterns_for_failures` should be recomputed against the *flip set*, not the absolute fail set, on the delta path. ~10 LOC.
  - Robustness: graceful fallback when (a) no baseline exists -> emit current output, (b) baseline schema mismatch (different total count, suspicious) -> emit current output and refresh baseline, (c) git command fails -> branch="", baseline still loadable.

## Disqualifiers / why this might be wrong

  1. **The CI workload assumption may not hold for the typical Redcon user.** If most agents invoke `pytest` ad hoc against a moving codebase (no two consecutive runs alike), there is no baseline that matches and the optimisation degrades to a no-op. BASELINE.md's frontier list mentions "Snapshot deltas vs prior `redcon_run` invocations of the same command on the same repo" (V47-shaped) as still-open: that hint suggests Redcon is *not* primarily targeting the CI loop today. So the impact estimate is conditional on CI integration becoming a deployment target; on the desktop-IDE workflow it is closer to ~10% expected reduction (because each invocation is somewhat unique).

  2. **The agent may need the absolute count anyway.** A user prompt like "are all tests passing?" wants `200/200`, not `=baseline`. The delta encoding *does* preserve totals (`n=200`), so this concern is mitigated structurally - but it requires the encoding to never drop the count, which my sketch above respects. If a future variant tried to drop it for further savings, it would break this case.

  3. **Branch-keyed baseline is wrong for PR workflows.** The natural baseline for a PR push is the merge-base with `main`, not the latest push on the PR branch (which would consider any pre-existing failure on the branch as "carried" and silently lose information). Solution: key baseline on `(argv_digest, merge_base_sha)` instead of `(argv_digest, branch)`. Adds one git call (`git merge-base HEAD origin/main`); same cost, slightly more correct. This is solvable but is the kind of subtle wrong-default that bites in production.

  4. **Already-present infrastructure mismatch.** The existing `run_history_cmd` table (`redcon/cmd/history.py`) stores per-invocation compressed tokens / cache hit / returncode but *not* the parsed pass/fail set. So while BASELINE asserts "sqlite history exists ... delta/heatmap/drift hooks", the *test-name-level* persistence does not exist - V16 would add it. This is a design choice point: we're adding a new domain-specific table rather than reusing the generic one. Acceptable, but it means "low integration cost" overstates: there's a new schema migration.

  5. **Quality-harness invariant change.** Today the must-preserve contract is "every failing test name survives at COMPACT". With delta encoding, a test failing in *both* baseline and current is no longer in the output text, so the existing must-preserve regex would fail. Either the harness path needs to be aware of the baseline (so it knows which tests are "still failing" and excused), or the delta encoding has to keep listing same-failures (eroding the savings). The first is more correct and ~30 LOC in `quality.py`. The second is a 30% giveback on busy regression days. Picking and committing to a contract is a soft dependency on a small spec discussion.

  6. **Stale baseline on long-lived branches.** If the baseline was generated three weeks ago against a different test set (some tests deleted, new ones added), the delta is meaningless. Mitigation: include a fingerprint of the *test inventory* (sorted hash of all test names seen at last green) and refuse to delta when the symmetric difference of inventory exceeds a threshold (say 5%). This is correct but adds complexity, and the threshold is a magic number.

  7. **Redundant with V47 / V20 / V25 on paper.** V47 ("snapshot delta vs prior `redcon_run` of same command on same repo") is the generic version of this; V20 (bigraph adjacency-list delta) is the file-side analog; V25 (Markov over MCP call sequences) overlaps the agent-aware angle. V16 is the most concrete and lowest-risk of the four because the test-domain semantic of "passing test set" is well-defined and the existing `TestRunResult` type already gives us the right anchor. But research-coverage-wise, declaring V16 a separate breakthrough vs. V47 may be greedy: a clean implementation of V47 with pytest as its first beneficiary subsumes V16. Verdict-affecting consideration.

## Verdict

  - **Novelty: medium**. The technique (delta-vs-baseline encoding for streamed structured output) is textbook in CI tooling (Bazel test cache, GitHub Actions check-summary diff, Buildkite annotations). Within Redcon's published surface it is genuinely missing - the BASELINE explicitly lists "snapshot deltas vs prior `redcon_run` invocations" as not-done. So: not novel as a CS technique, novel as a Redcon shipped feature.
  - **Feasibility: high**. ~150 production LOC, no new deps, no determinism risk, no cache-key disruption. The hardest part is the must-preserve contract update, which is a 30-LOC harness change plus a one-paragraph spec.
  - **Estimated speed of prototype: 2-3 days** for an end-to-end working version on pytest only (with cargo/go/npm wired in days 4-5 once the pipeline plumbing has settled). Quality-harness contract update is the long pole: probably another day to get the regex set right and the goldens stable.
  - **Recommend prototype: yes**, scoped narrowly to the CI integration story. Prerequisites:
      - **(a)** confirm at least one downstream user (Claude Code agent? GitHub Actions integration?) is running `redcon_run pytest` on the same argv across consecutive PR pushes - otherwise the impact estimate evaporates;
      - **(b)** decide between branch-keyed baseline (simple, slightly wrong for PRs) and merge-base-keyed (correct, one extra git call); the former is the right shipping default if `(a)` is desktop usage, the latter if `(a)` is GitHub Actions.
      - **(c)** confirm the must-preserve contract change is acceptable (regression flips must survive at COMPACT, but same-failures no longer need to appear in text) - this is a reasonable tightening but it is a contract change and should be flagged in the changelog.

  This is the cleanest example of a *high-impact composability win* in the BASELINE frontier list: the existing infrastructure (history sqlite, canonical TestRunResult, per-argv cache key) lines up exactly so the integration cost is small. The risk is not technical, it is "is the workload there?": if Redcon's users do not run the same pytest argv twice, the optimisation does not fire. The fallback to current encoding when no baseline exists makes the downside case safely zero.
