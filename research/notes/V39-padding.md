# V39: Trailing-zero / padding analysis on counts and timings

## Hypothesis

CLI tools and (possibly) Redcon's own formatters emit numbers with
fixed-width decimal padding ("0.00", "100.0%", "12.00s", "0:00:00.000",
"1234.000") whose trailing zeros encode no information but do consume
cl100k tokens. A trim-trailing-zero pass on numeric fields in
compressor output should give a small-but-free reduction, especially on
table-shaped outputs (`cmd-bench` summary, `ls -l`, `find` rows, `lint`
file-count tables, `kubectl get` columns). The hypothesis predicts a
non-trivial reduction only if (a) the agent-facing compressor outputs
actually contain such padded numerals, and (b) tiktoken's BPE merges
the trimmed form into strictly fewer tokens than the padded form.

## Theoretical basis

### 1. Token cost of redundant zeros under cl100k

For an unsigned float `n.f1...fk00...0` with `z >= 1` trailing zeros
after a non-zero digit, write `s` for the padded form and `s'` for the
trimmed form. Both forms decode to the same real number; the only
question is the BPE encoding length `T(.)`. Empirically (this study,
Section 4):

    T(s) - T(s') in {0, 1, 2}   (never negative)

with `2` achieved when the trim crosses a percent / unit suffix
boundary that forces re-segmentation. Worked example, cl100k_base:

| Padded | T | Trimmed | T | dT |
|---|---|---|---|---|
| `100.0%` | 4 | `100%` | 2 | **+2** |
| `+99.0%` | 5 | `+99%` | 3 | **+2** |
| `-25.0%` | 5 | `-25%` | 3 | **+2** |
| ` in 12.00s` | 6 | ` in 12s` | 4 | **+2** |
| ` in 1.00s` | 6 | ` in 1s` | 4 | **+2** |
| `1234.000` | 4 | `1234` | 2 | **+2** |
| `0:00:00.000` | 7 | `0:00:00` | 5 | **+2** |
| `12.50s` | 6 | `12.5s` | 6 | 0 (no merge change) |
| `0.430 ms` | 4 | `0.43 ms` | 4 | 0 |

So the per-occurrence saving is **either 2 tokens or 0** (the latter
when the trimmed form happens to land on a same-token-count BPE
segmentation). There is no token-inflation risk from trimming.

### 2. Information-theoretic justification

A trailing zero after the implicit precision boundary contributes
`log2(10) ~ 3.32` bits to the printed string but zero bits to the
quantity it encodes (because the precision is already captured by the
non-zero digits). Claude Shannon, *A Mathematical Theory of
Communication* (1948): redundant symbols in a source code add length
without adding information. Trimming them is a strict-Pareto operation
on (length, mutual information with the underlying value).

### 3. Aggregate-saving back-of-envelope

Let

  - `F` = number of fixture x level outputs in the agent-facing surface
  - `n_F` = expected count of trim-eligible numeric substrings per output
  - `p_save = 1.0` (probability a trim saves >=2 tokens; **see Section
    4: the actual measured fraction is `p_save < 1`, not 1**)
  - `delta_tok = 2` (token saving per successful trim)

Expected aggregate token saving:

    E[saved] = F * n_F * p_save * delta_tok

For Redcon's M8 fixture corpus: `F = 21 fixtures * 3 levels = 63`. The
question reduces to measuring `n_F` and `p_save` empirically. Section
4 settles it.

### 4. Empirical measurement on the M8 fixture corpus (this study)

Methodology: ran every fixture in `tests/test_cmd_quality.py::CASES`
through its compressor at all three levels (63 outputs, 35,725 total
cl100k tokens). Scanned every numeric substring (regex
`[+-]?\d+\.\d+(?:%|s|ms|MB|kB|KiB|MiB)?`) for trim eligibility, then
re-tokenized the trimmed form and computed the delta.

Result table:

| Level | Fixtures | Numeric substrings | Trim-eligible | Token savings |
|---|---|---|---|---|
| verbose | 21 | 4063 | 1 | **0** |
| compact | 21 | 4232 | 1 | **0** |
| ultra | 21 | 177 | 0 | **0** |
| **all** | **63** | **8472** | **2** | **0 tokens** |

Both trim-eligible occurrences came from `pip_install_typical` and
were the substring `0.110` extracted from the version string
`fastapi-0.110.0` - i.e. **information-load-bearing semver, not
formatting padding**. Trimming would change `0.110.0` to `0.11.0`,
which is a semantically distinct version. **Net safe trim savings on
the agent-facing surface: zero tokens.**

Specific patterns checked (counts across all 63 outputs):

| Pattern | Description | Hits |
|---|---|---|
| `\b\d+\.0+\b` | trailing-zero decimal (e.g. `12.0`) | 4 (all version-string slices) |
| `\b\d+\.\d*?[1-9](0+)\b` | decimal with trailing zero after non-zero (`1.430`) | 2 (also version slices) |
| `\b\d+\.0+%`, `[+-]\d+\.0+%` | percent with `.0` (`100.0%`, `+99.0%`) | **0** |
| `\b0\.0+s\b`, `\b0\.0+ms\b` | zero-second placeholder | **0** |
| `\b\d+\.0+s\b` | round-second timing (`12.0s`) | **0** |
| `\b00:00:00\.\d+\b` | ISO-clock zero | **0** |

Real durations that *do* appear in fixtures: `12.34s`, `2.35s`,
`450ms`, `50ms`. All already minimal; nothing to trim.

### 5. Where the padding actually lives

Searched production source for fixed-width float formatters:

  - `redcon/cmd/compressors/test_format.py:118`:
    `f" in {duration:.2f}s"` - emits `1.00s`, `12.00s` for round
    durations. **Not exercised by current fixtures** (test runners in
    fixtures emit non-round durations like 12.34s).
  - `redcon/cmd/compressors/docker_compressor.py:376,385`:
    `f" {step.duration_seconds:.1f}s"` - emits `0.0s`, `5.0s`. **Not
    exercised** (docker fixture has no per-step timestamps).
  - `redcon/cmd/compressors/pkg_install_compressor.py:253`:
    `f"in {result.duration_seconds:.1f}s"` - emits `0.5s`. **Not
    exercised** (pip fixture has no `in 12.34s` line).
  - `redcon/cmd/quality.py:82-83`: `:.1f%` and `:.0f%` for floor
    diagnostics. Not in compressor output; only emitted on quality
    failure messages (developer surface).
  - `redcon/cmd/pipeline.py:279,289`: `{raw_kb:.1f} KiB` for spill-log
    pointer bodies. Surface to agent only when raw > 1 MiB.

The biggest concentration of `.X0%` and `.X.00` floats is in
`redcon/cmd/benchmark.py` (`:+.1f%` for reductions, `:.2f` for ms
columns). That output is the developer benchmark report, not consumed
by an agent. Measured: `python -m redcon.cmd.benchmark` emits 3052
cl100k tokens of which `74` (~2.4%) would be saved by trimming `.0%`
to `%`. Out of scope for V39 (developer-facing).

### 6. Downstream-parser sensitivity check

The brief asks us to confirm no parser depends on fixed-width numbers.
Checked:

  - `tests/test_cmd_quality.py`: contains zero `\d+\.\d` regexes; the
    harness only uses `must_preserve_patterns` and floor comparisons
    (`reduction_pct >= floor`).
  - `redcon/cmd/benchmark.py`: round-trips `reduction_pct: float` as a
    dataclass field; the markdown rendering uses `:+.1f%` *for
    display*; nothing parses the rendered string back.
  - `redcon/cmd/history.py`: stores `round(out.reduction_pct, 4)` as a
    REAL column; numeric value, not text.
  - `must_preserve_patterns` in every shipped compressor (git_diff,
    git_log, git_status, pytest, cargo_test, go_test, npm_test, grep,
    ls, tree, find, lint, docker, pkg_install, kubectl): zero
    patterns of the form `\d+\.\d{N}` requiring fixed decimal width.
    The pytest/cargo/npm/go test compressors derive their preserve
    patterns from `re.escape(failure.name)`, where `failure.name` is a
    test identifier - never a number.

**Confirmed: no parser, harness, or must-preserve regex needs padded
numbers.** Trimming is safe.

## Concrete proposal for Redcon

Given the empirical near-zero impact on the *current* fixtures, the
honest proposal is **do not ship a generic post-pass** but **fix the
three formatters where padding is built in**, so that *future* outputs
that exercise these paths benefit. This is a defensive change, not a
breakthrough.

### Patch A: `_format_duration` to emit minimal seconds (test_format.py)

```python
# redcon/cmd/compressors/test_format.py
def _format_duration(duration: float | None) -> str:
    if duration is None:
        return ""
    if duration >= 1.0:
        # was: f" in {duration:.2f}s"  -> emits '1.00s', '12.00s'
        # use %g to drop trailing zeros, but cap precision at 4 sig-figs
        return f" in {duration:.4g}s"
    return f" in {duration * 1000:.0f}ms"
```

Effect: `1.0s -> 1s`, `12.0s -> 12s`, `12.34s -> 12.34s` (unchanged),
`12.30s -> 12.3s`. Saves 2 cl100k tokens per round-second test run.

### Patch B: docker step seconds (docker_compressor.py)

```python
# was: f" {step.duration_seconds:.1f}s"  -> '5.0s'
dur = (
    f" {step.duration_seconds:.3g}s"
    if step.duration_seconds is not None
    else ""
)
```

Effect: `5.0s -> 5s`, `5.5s -> 5.5s`. Saves 2 tokens per round-second
docker step. With ~8 steps in a typical build, saves up to 16 tokens.

### Patch C: pkg_install duration (pkg_install_compressor.py)

```python
# line 253, was: head_parts.append(f"in {result.duration_seconds:.1f}s")
head_parts.append(f"in {result.duration_seconds:.3g}s")
```

Saves 2 tokens per pip/npm/cargo install with a round-second runtime.

### Skip-list (do not patch)

  - `:+.1f%` in `benchmark.py`: developer-facing.
  - `:.1f` / `:.0f` in `quality.py`: error-message surface, not in the
    compressed output.
  - `:.1f` in `pipeline.py` spill-log message: agent-facing but emits
    `1234.5 KiB`-style numbers; no padding observed.

**Total production-code change: 3 lines.**

### Anti-pattern alternative: generic post-pass

A naive `re.sub(r'(\d+\.\d*?[1-9])0+\b', r'\1', text)` post-pass on
every compressor output is rejected because:

  - It would corrupt semver / version strings (`fastapi-0.110.0` ->
    `fastapi-0.11.0`).
  - It would corrupt fixed-width log timestamps the agent might match
    against.
  - It would touch numbers inside hash-like substrings.

Targeted formatter fixes (Patches A-C) avoid these collisions because
each patch site emits a freshly-formatted float into a known textual
slot, never one extracted from upstream tool output.

## Estimated impact

  - Token reduction on current shipped fixtures: **0 tokens**
    (measured; the fixtures don't exercise the padded paths).
  - Token reduction on real-world *round-duration* runs:
    - test runners (pytest 1s/2s/5s/12s/60s test suites): **2 tokens
      per run**, applied to every COMPACT/VERBOSE level emission.
    - docker builds with timestamped steps: **2-16 tokens per build**
      depending on step count.
    - pip install with `in 5.0s`-style stderr: **2 tokens per run**.
  - Per-call ceiling: ~20 tokens saved on a docker-build-with-timing
    output. Below the 5-pp breakthrough threshold by 1-2 orders of
    magnitude.
  - Latency: **-/+0.0 ms**. f-string `:.3g` is the same cost as
    `:.1f`.
  - Affects: `test_format.py`, `docker_compressor.py`,
    `pkg_install_compressor.py`. Three files, three lines.
  - Cache: zero impact. Cache key is argv+cwd hash, not output bytes.
  - Quality harness: must regenerate the M8 baseline if the harness
    pins exact output bytes. Spot-checked - it does not (only
    must-preserve regex matching, which uses literal test names).

## Implementation cost

  - 3 LOC total in production source.
  - 0 new runtime deps.
  - 1 LOC test patch (a synthesized `pytest_round_seconds` fixture
    with `in 12.00s` to lock in the trimming behaviour).
  - **Determinism**: `:.3g` and `:.4g` are deterministic (rely only on
    the input float and the standard formatter).
  - **Robustness**: `:.3g` handles negatives, zeros, and `inf`/`nan`
    same as `:.1f` would.
  - **Must-preserve**: no compressor's `must_preserve_patterns`
    contains a numeric width. Verified above.

## Disqualifiers / why this might be wrong

  1. **Zero measured impact on the M8 corpus.** The padded paths are
     not exercised by current fixtures. Shipping the patch helps
     hypothetical future runs only. By the V33 / V38 negative-result
     standard, this is a "prove-the-absence" exercise more than a
     win.
  2. **Per-occurrence saving is at most 2 cl100k tokens.** Even if
     every COMPACT output emitted one round-second duration (~21
     tokens of upside total per benchmark run), that is below the 5-pp
     breakthrough bar. V39 is a polish, not a frontier move.
  3. **Already partially-handled by `:.0f` / `:.1f` choices.** The
     team has already optimized the *low-magnitude* path (sub-second
     -> ms with `:.0f`, no padding). The *high-magnitude* path is the
     only residual; round multi-second durations are rare in test
     runs and rarer still in CI logs (real test runs almost never
     land on an integer second).
  4. **Generic post-pass would corrupt version strings.** Already
     measured: 2 of 2 trim-eligible substrings in the corpus were
     semver slices. A generic regex pass is unsafe; only the targeted
     formatter patches are.
  5. **The big padding source is the developer-facing benchmark
     report**, not the agent-facing compressor surface. That's a
     report-rendering issue, not a context-budget issue. Out of scope
     for V39 by the brief's wording ("counts and timings" - those
     surfaces emit ~2 tokens of padding per agent call, not 74).
  6. **cl100k merge-table dependence.** The `delta_tok in {0, 2}`
     observation holds for cl100k_base. On o200k_base (the GPT-4o
     family) the merge structure differs; `+99.0%` may already be a
     single token in o200k. A win here is cl100k-specific.

## Verdict

  - Novelty: **low**. f-string format-spec change is mechanical. The
    interesting contribution is the negative-on-current-fixtures
    measurement.
  - Feasibility: **high**. 3-line production patch, no new deps.
  - Estimated speed of prototype: **30 minutes** including the
    regression-locking fixture.
  - Recommend prototype: **conditional-yes** on landing the patches
    if and only if a future fixture exercises a round-second
    duration. Until then, the change is pre-emptive; the tradeoff is
    "3 lines + 1 test for 2 tokens per round-duration future run".
    Acceptable as good hygiene, not as a breakthrough.

## Honest summary (what the brief asked for)

The brief said: *this is genuinely small but might add up over the
table-shaped outputs (cmd-bench, ls, find, lint with file-count
rows)*. After measurement: **agent-facing compressor output on the M8
fixture corpus contains no trailing-zero padding worth trimming.** The
two trim candidates were both version-string slices (load-bearing
information, not padding). Table-shaped outputs (ls, find, lint,
kubectl) emit integer counts only ("8 steps", "30 errors", "5 nodes")
- there is no `.0%` in their compressed forms, only in the
developer-facing `benchmark.py` markdown report.

The three formatters that *do* hard-code padding (`test_format._format_duration`,
`docker_compressor` step duration, `pkg_install_compressor` head)
emit padded floats only on round-second inputs that none of the
current fixtures contain. Patching them to use `:.3g` / `:.4g` saves 2
tokens per round-second occurrence and is risk-free, but the saving
floor across the current corpus is exactly **0 tokens**.

Confirmed safe-to-trim against downstream parsers: no harness regex,
must-preserve pattern, or benchmark consumer requires fixed-width
numeric formatting. The constraint is non-binding; the opportunity is
near-empty.

The vector is closed as **prove-the-absence confirmed, plus a 3-line
defensive patch recommendation for round-duration future paths.**
Novelty: low. Recommend prototype: conditional-yes (cheap hygiene
patch only).
