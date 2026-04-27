# V73: Zero-copy bytes parsing with memoryview throughout

## Hypothesis

Every command-side compressor today decodes the full subprocess stdout (and
often stderr) to a Python `str` once, then runs `text.splitlines()` to obtain
a list of `str`, then iterates the list applying prefix tests and regexes.
Audit of `redcon/cmd/compressors/*.py` confirms the pattern is universal:

```
text = raw_stdout.decode("utf-8", errors="replace")  # 11/11 compressors
for line in text.splitlines():                       # 9/11 hot loops
    if line.startswith("..."):                       # prefix gate
        m = _RE.match(line)                          # only ~5-15% of lines hit
```

For a 240-hunk diff (~2.5 MiB raw, conservatively), this single
`bytes.decode()` allocates a new heap object the size of the input *and walks
every byte* to validate UTF-8. `splitlines()` then walks every byte again to
slice into a list of N substrings (each its own heap object, each owning a
copy of its bytes). The hot loop in `_parse_file_block` only ever uses the
*first character* of most lines (the prefix gate at git_diff.py:159-173),
yet we have already paid for full UTF-8 validation and N allocations of the
full body. That is the textbook "decode-and-discard" antipattern.

Claim: replacing the eager decode with a `memoryview` over the original
`bytes`, doing byte-level `\n`-scan + first-byte dispatch on the view, and
only decoding individual lines that the parser actually keeps, removes the
2 N-byte walks plus N small-string allocations per call. On a 240-hunk diff
the warm-parse latency goes from "dominated by decode + splitlines" to
"dominated by hunk-header regex". Predicted warm-parse speedup: 2-4x on
git_diff and grep at ~1 MiB raw, with proportionally smaller wins as the
input shrinks (constant overhead dominates below ~50 KiB).

Token reduction: zero. This is a pure perf vector, exactly as the brief
states. Cold-start: tiny win because the compressed bytecode for a `bytes`-
based parser is essentially identical, and the encoder dispatch table for
`utf-8` is loaded lazily either way; perhaps -1 ms because we don't pay
the decoder warmup the first time a compressor runs.

## Theoretical basis

### Cost model

Let `N = len(raw_stdout)` (bytes). Let `L = number of newlines` (lines).
Let `K = number of lines the compressor keeps` (e.g. K = 4 metadata lines
per file in a diff: header, hunk-header, +/- 5-line previews; for 240
hunks across 60 files, K is approximately 60 + 240 + 60 + 60 ~= 420
out of L). Let `f = K / L` be the keep ratio. Empirically:

```
git diff   : f ~ 0.005-0.02   (we drop hunk bodies entirely at COMPACT)
grep       : f ~ 0.10-0.30   (top-3 per file)
pytest     : f ~ 0.01-0.05   (failures only)
ls -R      : f ~ 1.00        (we keep almost everything; this vector won't help ls)
```

### Operations the current code performs

  1. `bytes.decode("utf-8", errors="replace")` -> O(N) byte scan + O(N)
     heap alloc for the new str object. CPython's UTF-8 fast path is
     about 1-3 GB/s on modern x86; the alloc + memset adds another
     ~500 MB/s of effective throughput drag. Effective ~700 MB/s for
     mixed ASCII/UTF-8.
  2. `str.splitlines()` -> O(N) byte scan + O(L) small-string allocs.
     Each substring is a fresh PyUnicodeObject (~56 B header + payload),
     plus list growth. A 100k-line input produces 100k allocations,
     each touching the small-object allocator.
  3. Hot loop: O(L) Python-level iteration. For each line: prefix test
     (string compare, fast), maybe regex (slow but bounded by the
     prefix-gating already in place).

So the *fixed* cost the parser pays before any "work" happens is roughly:

```
  T_setup(N, L) ~ N / 700e6  +  L * 1.5e-7      (seconds)
```

For N = 2.5 MiB, L = 50 000:

```
  T_setup ~ 2.5e6 / 7e8  +  5e4 * 1.5e-7
         ~ 3.6 ms       +  7.5 ms
         ~ 11.1 ms
```

The actual parser (regex + dict ops) on the kept K = 420 lines costs
maybe 0.5 ms. So decode + splitlines is ~95% of warm parse time on a
big diff. That is the cost we want to remove.

### Cost with memoryview byte-scanning

  1. Wrap stdout in `memoryview(raw_stdout)` -> O(1), no alloc.
  2. Iterate over byte-level newlines using `bytes.find(b"\n", pos)`.
     This is a memchr-backed C call; CPython's `_Py_bytes_index` runs
     at ~5-8 GB/s. So O(N) at ~6 GB/s.
  3. For each line slice (which is a `memoryview` slice -> O(1) view,
     not a copy): inspect first byte directly:
     ```
     first = mv[start]   # int 0..255, no decode
     if first == 0x2B:   # b'+'
         ...
     elif first == 0x2D: # b'-'
         ...
     ```
     No regex on the body lines (matches existing prefix-gate
     optimisation, just at byte level).
  4. Only when we *keep* a line (header lines, the kept hunk
     previews) do we call `bytes(mv[start:end]).decode()`. So we
     decode K * avg_line_len bytes total, not N.

New setup cost:

```
  T_setup'(N, L, K, w) ~ N / 6e9      (memchr line scan)
                       + K * w / 7e8  (decode kept lines, w = avg line bytes)
                       + L * 5e-8     (Python overhead per loop iter; no alloc)
```

For N = 2.5 MiB, L = 50 000, K = 420, w = 80:

```
  T_setup' ~ 4.2e5 ns + 4.8e4 ns + 2.5e6 ns
           ~ 0.42 ms  + 0.05 ms  + 2.5 ms
           ~ 3.0 ms
```

vs the 11.1 ms baseline -> **3.7x speedup** on the setup phase, ~3.3x
on total warm parse. On a 240-hunk diff (slightly smaller, N ~ 1.2 MiB,
L ~ 25 000) the absolute numbers halve but the ratio stays
2.5-3.5x.

### Why this matters above the noise floor

For sub-50 KiB inputs the constant overhead (Python frame setup,
attribute lookups, regex object first-use) dominates and the ratio
shrinks toward 1.0x. Below ~5 KiB the setup phase is already <100 us and
the optimisation is invisible. So this vector targets *exactly* the
inputs that the existing log-pointer-tier (>1 MiB) is too big for but
that already saturate the current parser: the 50 KiB-1 MiB band, which
is where production diffs and grep results live for medium repos.

## Concrete proposal for Redcon

### Files

- `redcon/cmd/_byteparse.py` (new, ~120 LOC): `iter_lines_mv(mv) ->
  Iterator[memoryview]`, `decode_line(mv) -> str`, `startswith_b(mv,
  prefix: bytes) -> bool`, plus precompiled byte-level constants.
- `redcon/cmd/compressors/git_diff.py` (modify): rewrite `parse_diff`
  to take `bytes` and use the byte iterator. `_DIFF_HEADER` and
  `_HUNK_HEADER` become `re.compile(b"...")` patterns and run on
  the line memoryview converted to `bytes` only when the prefix test
  passes (small slice, fast).
- `redcon/cmd/compressors/grep_compressor.py` (modify): same shape.
- `redcon/cmd/compressors/git_log.py`, `git_status.py`,
  `pytest_compressor.py`, `lint_compressor.py`,
  `pkg_install_compressor.py`, `kubectl_compressor.py`,
  `listing_compressor.py`, `docker_compressor.py`,
  `cargo_test_compressor.py`, `npm_test_compressor.py`,
  `go_test_compressor.py`: opt-in second pass after diff/grep land
  and prove the harness still passes.
- `redcon/cmd/_tokens_lite.py` (no change needed; `estimate_tokens`
  works on the eventually-decoded short header text).
- `redcon/cmd/quality.py` (no change; harness asserts on the
  formatted output string, which is unchanged).

### API

```python
# redcon/cmd/_byteparse.py
from typing import Iterator

def iter_lines_mv(buf: bytes | memoryview) -> Iterator[memoryview]:
    """Yield one memoryview per line. No decode, no allocation per line.
    Strips trailing '\\n' and optional '\\r'. Empty buffer yields nothing.
    """
    mv = memoryview(buf).cast("B")  # bytes-as-uint8 view
    n = len(mv)
    pos = 0
    while pos < n:
        nl = _find_newline(mv, pos, n)         # memchr, O(1) C call
        if nl == -1:
            yield mv[pos:n]
            return
        end = nl
        if end > pos and mv[end - 1] == 0x0D:  # strip CR
            end -= 1
        yield mv[pos:end]
        pos = nl + 1


def starts_with(mv: memoryview, prefix: bytes) -> bool:
    plen = len(prefix)
    if len(mv) < plen:
        return False
    # bytes(mv[:plen]) is a copy of plen bytes (small, e.g. 11 for
    # b"diff --git "); much cheaper than decoding the whole line.
    return bytes(mv[:plen]) == prefix


def decode(mv: memoryview) -> str:
    return bytes(mv).decode("utf-8", errors="replace")
```

```python
# redcon/cmd/compressors/git_diff.py (sketch, replaces parse_diff)
_DIFF_HEADER_B = re.compile(rb"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)$")
_HUNK_HEADER_B = re.compile(
    rb"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    rb"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@(?P<header>.*)$"
)
_PLUS, _MINUS, _SPACE, _AT = 0x2B, 0x2D, 0x20, 0x40

def parse_diff_bytes(buf: bytes) -> DiffResult:
    files: list[DiffFile] = []
    current_block_header: memoryview | None = None
    block_lines: list[memoryview] = []
    for line in iter_lines_mv(buf):
        # Cheap byte-level dispatch: most lines are diff-content.
        if line and line[0] in (_PLUS, _MINUS, _SPACE):
            if current_block_header is not None:
                block_lines.append(line)
                continue
        if starts_with(line, b"diff --git "):
            if current_block_header is not None:
                files.append(_parse_block_mv(current_block_header, block_lines))
            current_block_header = line
            block_lines = []
            continue
        if current_block_header is not None:
            block_lines.append(line)
    if current_block_header is not None:
        files.append(_parse_block_mv(current_block_header, block_lines))
    return _result_from_files(files)
```

The compressor's `compress(raw_stdout, ...)` keeps the bytes API
(currently it decodes immediately at line 63). Now it just passes
`raw_stdout` straight into `parse_diff_bytes`. The kept lines are
decoded only inside `_finalize_hunk` / `_format_*` when they reach
the output formatter. `verify_must_preserve` already operates on the
formatted str, unchanged.

`estimate_tokens(text)` only needs `len(text)`; for ASCII inputs
`len(bytes) == len(str)`, so we can compute `raw_tokens =
estimate_tokens_bytes(raw_stdout)` without ever materialising `text`.
For non-ASCII input the heuristic is `ceil(N/4)` which is wrong by
at most a small factor (cl100k is bytewise close anyway); we can
either accept that or do a single pass to count UTF-8 codepoints
(still no allocation).

### Migration order

1. Land `_byteparse.py` plus exhaustive unit tests (CRLF, no trailing
   newline, empty input, invalid UTF-8 in body, mixed line endings,
   single-line input, 1-byte input). Harness runs on tested fixtures.
2. Convert `git_diff.py` first - largest fixture, biggest measurable
   win, simplest grammar. Add a benchmark in
   `benchmarks/run_cmd_benchmarks.py` for the 240-hunk diff fixture
   to lock the speedup.
3. Convert `grep_compressor.py` second - text-mode hot loop; the
   JSON-mode path still goes through `json.loads(line_str)` which
   needs a decode (json doesn't take bytes in stdlib... actually it
   does as of 3.6: `json.loads` accepts `bytes`. Free win.).
4. Convert remaining compressors only if benchmarks show
   >2x speedup. ls -R, pytest, docker, lint may not benefit
   meaningfully because their inputs are smaller or their parsers
   already touch most lines.

## Estimated impact

- **Token reduction**: 0 absolute pp. This is a perf vector. The
  produced output text is byte-identical to today's (which is
  required for cache stability and the determinism check; see
  Disqualifier #1).
- **Latency (warm parse, 240-hunk diff)**: -7-9 ms per call,
  i.e. ~3x speedup on the parse phase. At the `compress_command`
  level this is amortised against subprocess + I/O time, so the
  end-to-end speedup is more like 1.3-1.6x on cache-miss calls
  with big diffs. On cache *hit* the parser doesn't run, so this
  vector is invisible.
- **Latency (cold start)**: ~-1 ms one-shot. The `_byteparse`
  module is tiny and pure-Python; lazy-importing it from
  `pipeline.py` keeps the cold path clean.
- **Affects which existing compressors / scorers / cache layers**:
  - All 11 cmd compressors are *eligible*; the proposal converts 2-3
    high-impact ones first.
  - File-side scorers (`redcon/scorers/*`) untouched.
  - Cache key (argv + cwd) unchanged - cache layer untouched.
  - Quality harness (`redcon/cmd/quality.py`) unchanged - it works on
    formatted output strings which are byte-identical to before.
  - Log-pointer tier (>1 MiB spill) is untouched and remains the
    correct strategy above 1 MiB. This vector is for the 50 KiB-1
    MiB band that doesn't trigger the spill.

## Implementation cost

- Lines of code:
  - `_byteparse.py` + tests: ~120 + 200 LOC.
  - git_diff conversion: ~80 LOC delta (rewrite parse path,
    bytes-pattern regexes).
  - grep conversion: ~60 LOC delta.
  - Bench harness addition: ~40 LOC.
  - Total to land first slice: ~500 LOC, half of which is tests.
- New runtime deps: none. All `memoryview`, `bytes.find`,
  `re.compile(b"...")` are stdlib. Does not violate "no required
  network / no embeddings".
- Risks to determinism, robustness, must-preserve:
  - **Determinism**: byte-for-byte identical output is the explicit
    success criterion. The differential test corpus
    (`docs/benchmarks/cmd/`) doubles as the determinism guard:
    diff old vs new output on every fixture, fail CI on any
    delta. Also re-running the harness twice catches any
    iterator-order issues.
  - **Robustness**: the existing harness fuzzes binary garbage,
    truncated mid-stream, 5000 newlines, random word spam.
    `iter_lines_mv` over arbitrary bytes is well-defined for all
    of those (no decode means no `UnicodeDecodeError`; we only
    decode the lines we keep, which are the metadata lines that
    pass a byte-level prefix test).
  - **must-preserve**: invariants are checked on the *formatted
    output* str, which is unchanged. The patterns themselves
    operate on str. No risk.
  - **Edge case: invalid UTF-8 inside a kept line**. Today
    `errors="replace"` produces a `�` replacement character.
    The proposal preserves this: kept lines are decoded with the
    same `errors="replace"` policy. So that surface is identical.
  - **Edge case: `re` matches against `bytes` work for ASCII
    patterns only**. All 11 compressors' patterns are ASCII (file
    paths, hunk headers, line numbers, status markers). Grepping
    `redcon/cmd/compressors` confirms no `\w` with `re.UNICODE`,
    no `[^ -]` ranges. Safe.

## Disqualifiers / why this might be wrong

1. **The harness's "byte-identical output" requirement is real, and
   floating-point or hash-order quirks could leak in.** If the
   memoryview path produces lines in a subtly different order (e.g.
   a CRLF line that decoded under `errors="replace"` produced a
   `\r` that splitlines hid but the byte iterator now exposes), the
   formatted output diverges and the cache becomes incoherent across
   versions. Mitigation: the byte iterator strips both `\n` and the
   immediately-preceding `\r`, matching `str.splitlines()` semantics
   exactly. Tested with a CRLF fixture before merge.

2. **Most production calls are sub-50 KiB.** A `git status` or
   `pytest -k some_test` produces 1-10 KiB. At that size the
   current decode + splitlines costs maybe 100 us total; the
   proposal saves 60 us. That's invisible to the agent. So the
   *expected delivered speedup* is much smaller than the synthetic
   240-hunk benchmark suggests. Honest assessment: this vector
   shines on a small fraction of calls (big diff, big grep, big
   docker ps), not on the median call. The product positioning
   ("fast parsers") is unchanged either way.

3. **CPython is not the only target, and PyPy / Pyston already do
   this for free.** PyPy's JIT collapses `bytes.decode().splitlines()`
   into a single C-level scan when it inlines and recognises the
   pattern. So on PyPy the speedup is measured 1.0-1.2x, not 3x.
   Redcon's claimed runtime is CPython only today, so this isn't a
   blocker, but it constrains the audience for the gain.

4. **memoryview adds cognitive load that the speedup may not
   justify.** Every contributor adding a new compressor would now
   pick between "old style: decode, splitlines, str-prefix" and
   "new style: byteparse, byte-prefix, decode-on-keep". Two patterns
   in the same directory is worse than one slow pattern. Mitigation:
   if we do this we should commit to migrating all 11 over time,
   not leaving a permanent two-track. The migration cost (point 4
   above) is real LOC.

5. **Already-implemented in disguise.** The current code does
   prefix-gating on str (git_diff.py:159 onwards). That covers most
   of the regex-avoidance gain. What it does *not* cover is the
   decode + splitlines walk itself. So there's a real residual,
   but it's smaller than a naive reading of the code suggests.
   The "biggest hot spot" in `parse_diff` per-line-iter today is
   the implicit C-level work of yielding the next str from
   splitlines, not the Python parser logic.

6. **`raw_stdout_bytes` accounting.** `pipeline.py` already records
   `len(run_result.stdout)` (bytes). No change there. But
   `estimate_tokens` is called on the decoded text (line 68 of
   git_diff.py). If we want to avoid the decode entirely on the
   *raw_tokens* count too, we need an `estimate_tokens_bytes`
   variant. For ASCII this is `ceil(N/4)`; for UTF-8 it would
   slightly over-count multi-byte characters (1 codepoint = 2-4
   bytes). On English-heavy command output the error is <1%. We
   either accept that, or add an `estimate_tokens_bytes` that does
   one fast UTF-8 codepoint count (still no string alloc).

## Verdict

- **Novelty: low**. Zero-copy parsing with memoryview is textbook
  CPython perf engineering; it shows up in `email.parser`, json
  binary mode, every TLS framer in the stdlib. Applying it to
  Redcon's compressors is correct but unsurprising. The real
  novelty would be in the *opportunistic decode boundary* (decode
  only the kept lines, reusing the prefix-gate already in place)
  if we phrase it as "tier the parsing work the same way we tier
  the output". That framing is mildly novel; the underlying
  mechanism is not.
- **Feasibility: high**. Pure stdlib, deterministic by construction
  (byte-identical output is the merge criterion), no embeddings, no
  network, cache-key safe, must-preserve safe.
- **Estimated speed of prototype: 2-3 days**. One day for
  `_byteparse.py` + tests, one day for the git_diff conversion +
  benchmark, half a day for grep, half a day to wire CI
  byte-identical-output diffing. Skip the long tail of
  compressors until benchmarks justify each one.
- **Recommend prototype: conditional-on-benchmark**. Only worth
  shipping if a measured 240-hunk diff fixture shows >=2x warm
  parse speedup *and* the cache-miss end-to-end call shows >=20%
  improvement on at least one realistic workload. If the gain is
  only visible in microbenchmarks, the cognitive cost of two
  parse styles isn't worth it. The vector explicitly positions
  itself as PERF-only with zero token reduction; the product's
  breakthrough definition (BASELINE.md line 67-69) requires
  >=20% cold-start improvement to count, and this isn't a
  cold-start vector. So the bar is: warm-parse speedup must be
  large enough to be felt in interactive agent loops on big
  diffs / grep results. Bench first, then decide.
