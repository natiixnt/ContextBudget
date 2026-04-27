# V69: Coverage report compressor (delta vs main branch)

## Hypothesis

`coverage report` (and its in-process equivalent, `pytest --cov`) emit a
per-file table that is dominated by lines the agent does not need: every
file in the repo, with `Stmts / Miss / Cover` columns, sorted alphabetically,
ending in a `TOTAL` row. On a 400-file repo this is 400-450 lines of plain
ASCII per invocation. What an agent actually wants when it sees this output
is a three-bullet answer: (i) the total coverage percent, (ii) how that
moved versus the baseline (the same argv's last successful run on `main`,
or HEAD's merge-base), and (iii) which files dropped meaningfully -
typically the ones the current PR touched. Today none of those three
questions are answered cheaply: the agent reads the entire grid and
re-derives them.

The claim: a coverage compressor that parses the standard text grid,
stores per-file coverage in the same SQLite history infrastructure that
V16 and V47 already justify, and emits

    coverage: 84.7% (vs main 85.1%, -0.4pp; 412 files, 6 dropped)
    -3.1pp redcon/cmd/pipeline.py 92.4 -> 89.3
    -2.0pp redcon/cmd/quality.py    78.5 -> 76.5
    -1.6pp redcon/scorers/history.py 81.0 -> 79.4
    +1.2pp redcon/cmd/cache.py      88.4 -> 89.6
    (3 more files moved >=0.5pp; full table @.redcon/cov_runs/<digest>.txt)

cuts compact-tier tokens by **~85-92% on the steady-state CI path** and
**~70-80% even on the cold-baseline first-run path** (where we still
collapse 400 alphabetical rows to a top-K-dropped view, just without the
delta column). It composes with V16 (test-delta) and V47 (snapshot-delta)
because all three want the same `(argv_digest, branch / merge-base)` key
into history; the new contribution here is the *coverage-specific
canonical type* and the rule for selecting which files survive the cut.

## Theoretical basis

### 1. The information actually needed

Let the parsed coverage report be a multiset
`C_t = {(path_i, stmts_i, miss_i, cover_i) : i in 1..N}` plus an aggregate
`(stmts_T, miss_T, cover_T)`. The agent's downstream decision (continue,
re-run, add tests, fix code) depends on:

  - the aggregate move `cover_T - cover_T^{baseline}` (one scalar);
  - the *changed-file* coverage moves `delta_i = cover_i - cover_i^{baseline}` for `i` in the working tree's changed paths; and
  - any file with `delta_i <= -threshold` whether the PR touched it or not (regression on an untouched file is the *most surprising* signal and must not be lost).

Files where `delta_i = 0` and `cover_i == cover_i^{baseline}` carry
*zero* task-relevant information and can be dropped without ambiguity:
the totals plus the baseline plus the explicit deltas reconstruct them
exactly via the conservation identity

    sum_i (stmts_i * cover_i) == stmts_T * cover_T   (within rounding)

so the omitted rows are recoverable by any caller who needs them.

### 2. Coding-cost back-of-envelope

For a repo with N files, the absolute COMPACT encoding (current behavior:
no compressor, raw text passes through) is:

    cost_abs(N) ~= 25 + 14 * N   tokens

where 25 is the table header + `TOTAL` row and 14 is the cl100k cost of
one row like `redcon/cmd/pipeline.py     412     31    92.5%` (path
~7-9 tokens, three numerics ~5 tokens). For Redcon-itself
(N ~ 100), that is ~1425 tokens. For a mid-sized Django app
(N ~ 600), ~8425 tokens.

The delta encoding is:

    cost_delta(N, k) ~= 12 + 6 + 18 * k   tokens

where 12 is the totals + delta-vs-baseline header, 6 is the file-count
suffix, and 18 is the cost of one moved-file line
`-3.1pp redcon/cmd/pipeline.py  92.4 -> 89.3`. The `k` is the number of
files whose `|delta_i|` exceeds a threshold (default 0.5pp); empirically
on a single PR push `k` is 0-8 (the number of files the diff touched
that contained statements, capped by a top-K of e.g. 10 when the PR is
larger). For N = 400, k = 5:

    cost_abs(400)  ~= 25 + 14 * 400 = 5625
    cost_delta(400, 5) ~= 12 + 6 + 18 * 5 = 108

Reduction: **(5625 - 108) / 5625 ~ 98.1%**. Even on the cold-baseline
path where we have no prior to delta against, the same shape works:

    cost_cold(N, k) ~= 18 + 16 * k   tokens

(top-K *least-covered* files instead of dropped-most files), and for
`k = 10` on N = 400:

    cost_cold(400, 10) ~= 18 + 16 * 10 = 178

Reduction vs absolute: **(5625 - 178) / 5625 ~ 96.8%**.

### 3. Information-theoretic floor

The Shannon-optimal encoding of "which k of N files moved by more than
the threshold" needs `log2(C(N, k))` bits to identify the set, plus
~`k * log2(precision_levels)` bits for the per-file deltas. For
N = 400, k = 5, precision = 1pp granularity (~100 levels), this is

    log2(C(400, 5)) + 5 * log2(100)  ~ 32.3 + 33.2 = 65.5 bits
                                     ~ 8-12 cl100k tokens for the IDs alone

The 108-token estimate above carries the *full path strings* and
human-readable absolute coverage on each side (`92.4 -> 89.3`), which
are not strictly needed for the bit-floor but are needed for an LLM
agent to act without re-fetching. We're paying ~10x the Shannon floor
in exchange for self-containedness; that is the right operating point
for an agent-facing channel and matches the choice already made by the
test-delta and diff compressors.

### 4. Why not just diff the two `coverage report` text outputs?

A naive byte-diff of two coverage reports would hit `git diff`'s 97%
reduction (already in BASELINE) but would lose:

  - alphabetical-vs-by-delta sort (the agent has to scan the diff to
    find regressions);
  - threshold-based filtering (the diff would emit 0.1pp jitter as
    real edits);
  - the "untouched file regressed" signal (silently buried in the diff).

So the structural delta is doing real work that a text-diff cannot.
This mirrors why `pytest_compressor` exists despite `git diff` being
generic: structured parsing lets us do *semantic* compression
(threshold, sort-by-impact, full-table-spill).

## Concrete proposal for Redcon

### Files touched

  - **NEW `redcon/cmd/compressors/coverage_compressor.py`** (~180 LOC):
    parser for the standard `coverage report` text grid + `coverage
    report -m`/`coverage report --skip-empty` variants + the
    `pytest --cov` post-test summary block.
  - **`redcon/cmd/types.py`** (~15 LOC): add `CoverageEntry` and
    `CoverageResult` dataclasses (frozen, slotted, mirroring
    `LintResult` shape).
  - **`redcon/cmd/registry.py`** (~6 LOC): add `_is_coverage_report`
    matcher and lazy registration entry, in line with how
    `_is_lint` / `_is_pkg_install` are wired.
  - **`redcon/cmd/history.py`** (~30 LOC, *shared with V16/V47*):
    add a generic `result_baseline` table keyed on
    `(argv_digest, baseline_ref, schema)` storing a JSON blob of the
    parsed canonical type. V16's `test_baseline`, V47's general
    snapshot store, and V69's coverage baseline collapse into this
    one table - the schema column disambiguates the JSON shape.
  - **`redcon/cmd/pipeline.py`** (~10 LOC): the same baseline
    lookup-and-rerender hook V16/V47 install. V69 piggy-backs.
  - **`redcon/cmd/quality.py`** (~30 LOC for fixtures): two golden
    inputs - a 400-file alphabetical `coverage report` text and
    the same with three rows perturbed downward - asserting
    determinism, must-preserve, and reduction floor.

Detection rule (pure argv inspection, no I/O):

    argv == ("coverage", "report", *_) or
    argv == ("python", "-m", "coverage", "report", *_) or
    argv contains contiguous ("pytest", ..., "--cov", ...) in any order, or
    argv contains "--cov-report=term" anywhere.

The `pytest --cov` case parses the *coverage block* embedded in
pytest stdout (between `---------- coverage ----------` and the
trailing test-summary line); the pytest compressor itself stays
unchanged - V69 runs on stdout, picks the coverage block, and emits
*one combined* CompressedOutput where the test summary and the
coverage delta are stacked.

### Sketch (`redcon/cmd/compressors/coverage_compressor.py`)

```python
"""
Coverage-report compressor.

Parses the standard `coverage report` text table:

    Name                              Stmts   Miss  Cover
    -----------------------------------------------------
    redcon/cmd/pipeline.py              412     31  92.5%
    ...
    -----------------------------------------------------
    TOTAL                             18421   2810  84.7%

Emits a compact delta-vs-baseline view: total %, change vs baseline,
top-K files with |delta| >= threshold, with a full-table spill pointer
for callers that want the raw grid.
"""
from __future__ import annotations
import re
from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.types import (
    CompressedOutput, CompressionLevel, CoverageEntry, CoverageResult,
)
from redcon.cmd._tokens_lite import estimate_tokens

# "redcon/cmd/pipeline.py    412   31   92.5%"   (also "92" without "%")
_ROW = re.compile(
    r"^(?P<path>\S[^\s].*?)\s{2,}"
    r"(?P<stmts>\d+)\s+(?P<miss>\d+)"
    r"(?:\s+(?P<branch>\d+)\s+(?P<brmiss>\d+))?"   # --branch columns
    r"\s+(?P<cover>\d+(?:\.\d+)?)%?\s*$"
)
_HEADER = re.compile(r"^Name\s+Stmts\s+Miss\b")
_TOTAL = re.compile(r"^TOTAL\s+(\d+)\s+(\d+)(?:\s+\d+\s+\d+)?\s+(\d+(?:\.\d+)?)%?\s*$")
_RULE = re.compile(r"^-{5,}\s*$")
# pytest --cov embeds the table; locate it.
_COV_BLOCK_START = re.compile(r"^-+\s*coverage:.*$", re.IGNORECASE)


class CoverageCompressor:
    schema = "coverage"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        # Only the TOTAL line and any file with |delta| >= threshold are
        # promised at COMPACT. ULTRA exempt as usual.
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        if argv[:2] == ("coverage", "report"):
            return True
        if argv[:3] == ("python", "-m", "coverage") and "report" in argv:
            return True
        if "pytest" in argv[0:2] and any(
            a == "--cov" or a.startswith("--cov=") or a == "--cov-report=term"
            for a in argv
        ):
            return True
        return False

    def compress(self, raw_stdout, raw_stderr, ctx):
        text = (raw_stdout or raw_stderr or b"").decode("utf-8", "replace")
        result = parse_coverage(text)
        baseline = getattr(ctx, "baseline", None)   # injected by pipeline (V16/V47 hook)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        body = _format(result, baseline, level, threshold_pp=0.5, top_k=10)
        comp_tokens = estimate_tokens(body)
        # must-preserve: TOTAL line + any file we explicitly mention
        must = (
            re.escape(f"{result.total_cover:.1f}"),
            *(re.escape(path) for path in _changed_paths(result, baseline, 0.5)[:10]),
        )
        return CompressedOutput(
            text=body, level=level, schema=self.schema,
            original_tokens=raw_tokens, compressed_tokens=comp_tokens,
            must_preserve_ok=verify_must_preserve(body, must, text),
            truncated=False, notes=ctx.notes,
        )


def parse_coverage(text: str) -> CoverageResult:
    entries: list[CoverageEntry] = []
    total = None
    in_block = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if _HEADER.match(line):
            in_block = True
            continue
        if not in_block:
            # pytest --cov: skip until the coverage block header.
            if _COV_BLOCK_START.match(line):
                in_block = True
            continue
        if _RULE.match(line) or not line:
            continue
        m = _TOTAL.match(line)
        if m:
            total = (int(m[1]), int(m[2]), float(m[3]))
            in_block = False
            continue
        m = _ROW.match(line)
        if m:
            entries.append(CoverageEntry(
                path=m["path"].strip(),
                stmts=int(m["stmts"]),
                miss=int(m["miss"]),
                cover=float(m["cover"]),
            ))
    return CoverageResult(
        entries=tuple(entries),
        total_stmts=total[0] if total else sum(e.stmts for e in entries),
        total_miss=total[1] if total else sum(e.miss for e in entries),
        total_cover=total[2] if total else _compute_total(entries),
    )


def _format(curr, baseline, level, *, threshold_pp, top_k):
    if level is CompressionLevel.ULTRA:
        if baseline is None:
            return f"coverage: {curr.total_cover:.1f}% ({len(curr.entries)} files)"
        d = curr.total_cover - baseline.total_cover
        return f"coverage: {curr.total_cover:.1f}% ({d:+.1f}pp vs baseline)"
    by_path_b = {e.path: e for e in (baseline.entries if baseline else ())}
    moved: list[tuple[float, CoverageEntry, float]] = []
    for e in curr.entries:
        prev = by_path_b.get(e.path)
        if prev is None:
            if baseline is not None and e.cover < 100.0:
                moved.append((e.cover - 100.0, e, 100.0))   # treat new file as "was 100"
            continue
        delta = e.cover - prev.cover
        if abs(delta) >= threshold_pp:
            moved.append((delta, e, prev.cover))
    moved.sort(key=lambda t: (t[0], t[1].path))     # most-negative first
    head = (
        f"coverage: {curr.total_cover:.1f}%"
        + (f" (vs baseline {baseline.total_cover:.1f}%, "
           f"{curr.total_cover - baseline.total_cover:+.1f}pp; "
           f"{len(curr.entries)} files, {len(moved)} moved)"
           if baseline else f" ({len(curr.entries)} files)")
    )
    lines = [head]
    for delta, e, prev_cov in moved[:top_k]:
        lines.append(f"{delta:+.1f}pp {e.path}  {prev_cov:.1f} -> {e.cover:.1f}")
    if len(moved) > top_k:
        lines.append(f"({len(moved) - top_k} more files moved >=0.5pp)")
    return "\n".join(lines)
```

The `CoverageEntry` / `CoverageResult` types in `redcon/cmd/types.py`
are 12 lines of frozen-slotted dataclass and follow the existing
`LintIssue` / `LintResult` template exactly. JSON-serializing
`CoverageResult` for the baseline blob is a one-liner via
`dataclasses.asdict`.

Detection wiring in `registry.py` mirrors `_is_lint` and is one
function plus one `register_lazy(...)` call.

### Composition with V16 and V47

V16 stores a `(argv_digest, branch)` -> `TestRunResult` row. V47
proposes the generic snapshot table for any canonical result type.
V69 needs exactly that generic table, partitioned by `schema`. The
*single* shared schema is

    CREATE TABLE result_baseline (
      argv_digest TEXT NOT NULL,
      baseline_ref TEXT NOT NULL,    -- "main", merge-base sha, or branch
      schema TEXT NOT NULL,          -- 'pytest' | 'coverage' | 'git_status' ...
      generated_at TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      PRIMARY KEY (argv_digest, baseline_ref, schema)
    );

V69 ships the row for `schema='coverage'`; V16 for `schema='pytest'`;
V47 for everything else. Implementing V69 *forces the right shape*
of the shared table because coverage has the largest payload (one
row per file in the repo) and stresses the JSON-blob path the most.

The `baseline_ref` choice for V69 is **the merge-base of HEAD with
`origin/main`** (or local `main` if `origin/main` is missing), not
the latest commit on `main`. Reason: the agent's "drop vs main"
expectation is "what would the diff look like if I PR'd this", which
is what merge-base captures. This matches the corrected V16
disqualifier #3.

### Concrete examples

Raw `coverage report` for a 412-file repo (~5800 tokens):

    Name                                           Stmts   Miss  Cover
    ---------------------------------------------------------------------
    redcon/__init__.py                                  3      0   100%
    redcon/cmd/__init__.py                              4      0   100%
    redcon/cmd/_tokens_lite.py                         91      4    96%
    redcon/cmd/budget.py                               58      2    97%
    redcon/cmd/cache.py                                85     10    88%
    ... (407 more rows) ...
    redcon/scorers/relevance.py                       142     27    81%
    ---------------------------------------------------------------------
    TOTAL                                           18421   2810    85%

V69 COMPACT with baseline (~32 tokens):

    coverage: 84.7% (vs baseline 85.1%, -0.4pp; 412 files, 4 moved)
    -3.1pp redcon/cmd/pipeline.py  92.4 -> 89.3
    -2.0pp redcon/cmd/quality.py   78.5 -> 76.5
    -1.6pp redcon/scorers/history.py 81.0 -> 79.4
    +1.2pp redcon/cmd/cache.py     88.4 -> 89.6

V69 COMPACT cold (no baseline) (~58 tokens):

    coverage: 84.7% (412 files)
    51.3% redcon/cmd/semantic_fallback.py
    62.0% redcon/scorers/history.py
    66.8% redcon/cmd/quality.py
    71.4% redcon/cache/run_history_sqlite.py
    ... (6 more rows, capped)

V69 ULTRA (6 tokens):

    coverage: 84.7% (-0.4pp vs baseline)

### Log-pointer interaction

Coverage reports for monorepos can run >1 MiB (we have evidence of
10k-file Pants/Bazel grids). The existing
`pipeline.py` log-pointer tier (raw > 1 MiB -> spill to
`.redcon/cmd_runs/<digest>.log` + tail-30) **already covers** this
with no V69-specific work, but V69 should additionally write the
*structured* full result to `.redcon/cov_runs/<digest>.json` (so
follow-up `redcon_quality_check` calls or a hypothetical
`redcon_run --expand-coverage` can rehydrate the full grid without
re-running coverage.py). ~15 LOC, optional.

## Estimated impact

### Token reduction

  - **CI loop, baseline present, k=0-3 moved files**: raw grid
    typically 1500-8000 tokens (depending on repo size); compressed
    output 25-65 tokens. **~96-99% reduction.**
  - **Cold baseline, first run on a branch**: raw 1500-8000 tokens;
    compressed top-K-uncovered view 50-200 tokens. **~92-97%
    reduction.**
  - **Worst case, k=20 moved files** (huge refactor): top-K cap
    holds output to 12 + 18*10 + 5 = ~197 tokens. Reduction floor
    is still well above the 70% ULTRA threshold, well above the
    30% COMPACT threshold.

This is on a *new* compressor where the prior baseline is
"raw output passes through unchanged" (no compressor matched), so
the apples-to-apples reduction is enormous. As a *contributor to
BASELINE.md's compact-tier table*, V69 lands somewhere between
git diff (97.0%) and find (81.3%). Ballpark: **~95% compact
reduction**.

### Latency

Cold path: parse a 5-10k-line text grid via one regex per line. The
prefix gating already used in diff/lint applies (only lines starting
with a non-space, non-dash character can be data rows). Estimated
~3-6 ms on a 400-file grid; one `SELECT payload_json` from the
shared baseline table (~0.3 ms with the existing connection); one
`UPSERT` (~0.5 ms). Coverage.py itself takes 100-2000 ms to *produce*
the report, so V69 adds ~0.5% latency.

Warm path: full pipeline cache hit on identical argv+cwd+HEAD.
Untouched.

Cold-start (import-time) latency: V69 follows the lazy-registration
pattern in `registry.py::_bootstrap_lazy` and adds ~30 us to first
detect call (one regex compile, lazy). No regression on the
sub-200-ms cold-start budget BASELINE pins down.

### Affects

  - New: `coverage_compressor.py`, types in `types.py`, registry
    entry, history shared-table addition.
  - Composes with: V16, V47 (shared baseline infra). When all three
    ship, the same `result_baseline` table serves all three; if V69
    ships first, V16/V47 can drop in without schema migration.
  - Untouched: cache key (V69 is a fresh compressor, cache key
    semantics unchanged), other compressors, scorers,
    rewriter, quality harness contract.

## Implementation cost

  - **LOC**: ~180 production (compressor) + 25 (types) + 6 (registry)
    + 30 (shared baseline table) + 10 (pipeline hook, shared with
    V16/V47) + 200 test (parsing goldens for `coverage report`,
    `coverage report -m`, `coverage report --branch`, `pytest --cov`
    block, baseline-miss fallback, top-K cap, threshold respect,
    quality harness reduction-floor and determinism).
  - **New runtime deps**: zero. coverage.py output is plain text;
    no parser library needed. Matches BASELINE rule "no required
    network, no embeddings."
  - **Determinism**: preserved. Sort key
    `(delta, path)` is total (delta float; path string). Threshold
    is a constant. Top-K is a constant. SQLite `ORDER BY` not
    used in the hot path - we sort in Python deterministically.
  - **Cache key**: unchanged. V69 is one more compressor in the
    `detect_compressor` chain, picked by argv prefix.
  - **Must-preserve guarantee**: contract is "the TOTAL coverage
    number and every path that appears in the compressed output
    (i.e. every file the compressor *chose* to mention) must
    survive at COMPACT". This is *narrower* than "every row from
    the input grid must survive" - acceptable because the grid is
    deterministically reconstructible from totals + omitted-rows-
    are-unchanged + delta entries. ULTRA exempt as usual.
  - **Robustness**: the regex is anchored to a 2+-space gap between
    the path and the first numeric column, which is exactly what
    coverage.py emits. Falls back to "no entries parsed" gracefully
    if the format changes (output: `coverage: <unparsed>` flag),
    matching how `lint_compressor` handles unrecognized input.

## Disqualifiers / why this might be wrong

  1. **Coverage report consumption is rare in agent loops.** Most
     agents don't ask for coverage; they ask for tests. If `pytest
     --cov` is the only realistic invocation path, then V69's
     value is bound up in pytest workflows specifically. This narrows
     the deployment surface but does not invalidate it - the
     `pytest --cov` block in stdout is exactly where compression
     pays off because that block today bloats pytest output by
     1500-8000 raw tokens on cov-instrumented test runs, and the
     existing pytest compressor passes that block through verbatim
     (it has no coverage parser).

  2. **Coverage report formats are not stable across coverage.py
     versions.** Old coverage.py used "Cover" without the percent
     sign; newer versions emit "Cover" with `%` and an optional
     `Branch` column. The regex above handles both, but minor format
     drift (a future column, a localised header) would silently
     drop rows. Mitigation: a fixture corpus pinned to coverage.py
     6.x and 7.x outputs, plus a `--no-skip-covered` regression
     fixture. Risk is real but low - coverage.py format is one of
     the more stable text outputs in the Python ecosystem.

  3. **The merge-base baseline is expensive on large repos.**
     `git merge-base HEAD origin/main` is O(graph depth) and on
     a multi-thousand-commit history with shallow clones can fail
     outright. Fallback: when `git merge-base` fails or times out
     (>500 ms), key on `(argv_digest, branch)` instead and treat
     the latest local-branch run as the baseline. This is the same
     trade-off V16 raised. The fallback is correct (better than
     nothing) but produces a "vs your last run on this branch" view
     instead of "vs main", which can confuse the agent. The header
     should say which baseline was used: `vs main` vs
     `vs branch:foo`.

  4. **`coverage report` as an argv detection is too narrow.**
     Many CI configurations invoke coverage indirectly:
     `coverage run -m pytest && coverage report`,
     `coverage xml`, `pytest --cov-report=html`, etc. V69 only
     handles the `term` text output. The XML/JSON/HTML paths would
     need separate parsers (XML is structured and easy; HTML is a
     trap; JSON should be its own compressor) - but they should
     not be lumped into V69 because each format has its own
     canonicalisation. Scoping to `--cov-report=term` (default) is
     correct, just narrow.

  5. **Subsumed by V47.** V47 (snapshot delta vs prior `redcon_run`)
     is the generic version. A complete V47 with coverage as one
     of its result types covers V69 entirely; V69 only stands alone
     if V47 is descoped. The right framing is "V69 is V47's
     coverage instantiation, productised as a first-class
     compressor with coverage-specific scoring (threshold, top-K,
     PR-changed-files weighting)". The threshold and top-K logic
     are the parts V47 cannot generically supply - they're
     domain-specific - so V69 is a thin but real addition on top
     of V47's table.

  6. **No existing CompressedOutput field for "spill pointer to
     parsed structure".** The compressor wants to emit
     `(.redcon/cov_runs/<digest>.json)` so a follow-up call can
     rehydrate the full grid. The current `CompressedOutput.notes`
     tuple is the natural carrier, but historically `notes` is
     used for human messages, not machine pointers. Either reuse
     `notes` (low effort, slight semantic stretch) or add a new
     `attachments` field (cleaner, breaks the dataclass shape).
     Picking the former is fine but is a tiny API smell.

  7. **The "untouched file regressed" case requires git diff
     awareness.** The compressor as sketched lists *all* moved
     files by absolute delta, not weighted by "was this file in
     the PR's diff". That misses the agent-priority signal that
     a regression on an untouched file is more surprising than the
     same delta on a touched file. Mitigation: take a
     `changed_paths` parameter (computable from `git diff --name-
     only origin/main...HEAD`) and re-rank moved files so that
     `(untouched, regression)` comes before `(touched,
     regression)`. ~15 extra LOC. Not in the initial sketch.

## Verdict

  - **Novelty: medium.** Coverage-delta tooling exists in
    third-party CI services (Codecov, Coveralls); inside Redcon's
    deterministic-local-first niche it is genuinely missing - the
    BASELINE table has 11 compressors and no coverage entry, and
    `coverage report` is not in the existing `detect_compressor`
    chain. The technique is borrowed from the V16/V47 family;
    the coverage-specific contributions are the threshold-based
    filter, the top-K-uncovered cold view, and the
    PR-changed-files weighting hook.
  - **Feasibility: high.** ~250 production LOC, no new deps, no
    determinism risk, no cache key disruption, parser is one regex.
    The largest dependency (shared baseline table) is shared with
    V16 and V47 and pays off three times.
  - **Estimated speed of prototype: 1-2 days** for a working
    compressor with golden fixtures and quality-harness coverage on
    `coverage report` only; +1 day for `pytest --cov` block
    integration; +1 day for the merge-base baseline plumbing and
    the changed-files re-rank.
  - **Recommend prototype: yes**, in the order:
      - **(a)** ship the cold-baseline path (top-K uncovered + totals)
        as a standalone compressor first - this delivers the ~92-97%
        reduction with zero new infrastructure and validates the
        parser on a real coverage corpus;
      - **(b)** add the shared `result_baseline` table once, jointly
        with whichever of V16/V47 ships next - V69 is the natural
        first beneficiary because its baseline blob is the largest
        and most stresses the JSON path;
      - **(c)** add the `git diff --name-only` changed-files re-rank
        last, gated behind a `record_history=True`+`is_git_repo=True`
        check so non-git invocations are unaffected.

  This is the lowest-risk, fastest-prototype member of the
  delta-vs-baseline family (V16, V47, V69). Its main contribution
  to the family is forcing the right shape of the shared baseline
  table - a coverage payload is the largest of the three and so
  characterises the JSON-blob design space best - and adding a
  twelfth compressor to the BASELINE table at a reduction tier
  (~95%) that lands near the top of the existing list.
