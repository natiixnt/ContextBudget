# V56: Early-kill subprocess when output exceeds budget signal

## Hypothesis
The current runner kills a subprocess only when its captured **raw bytes**
exceed a hard cap (16 MiB) or the wall-clock timeout fires (120 s). Both
thresholds are blind to compressed semantics: a `git log` that produces 1
MiB but whose first 50 commits already saturate the agent budget keeps
running, generating CPU time, IO and pipe traffic that will be parsed and
then discarded. V56 claims that running a *streaming compressor* INLINE
with the byte-capture loop, and tracking a running estimate of compressed
tokens, lets us send SIGTERM as soon as `compressed_tokens_so_far >=
budget * safety_factor`. For commands whose output is monotone in
compressed-token contribution (git log, grep/rg, find, ls -R, pytest -v
with many failures, lint with many findings) this saves ~95% of subprocess
runtime and IO on long-tailed runs without changing output semantics for
the agent. For non-streamable compressors (git diff needs the full file
hunk to compute +/- counts), V56 is opt-in only, so the existing default
path is untouched.

## Theoretical basis

Let the subprocess produce a stream of output bytes b_1, b_2, ..., b_N.
Define f: prefix -> compressed-token estimate, where f is non-decreasing
in the prefix length (true for git log: each new commit can only add
tokens to the compressed view; true for grep: each new path/match can
only add; **not strictly true** for git diff because a later hunk-trailing
line can change the per-file +/- summary - though monotone if we treat
compressed view as `partial diff up to file boundary`).

For a budget B with safety factor s in (0, 1] (e.g. s = 0.85), the optimal
kill index is:

```
k* = min { k : f(b_1..b_k) >= s * B }
```

By definition, bytes b_{k*+1..N} are wasted from a token-budget POV.
The cost saved is approximately:

```
saved_subprocess_seconds   ~= (N - k*) / throughput_bytes_per_second
saved_pipe_io              ~= (N - k*) bytes
saved_parser_work          ~= O(N - k*) regex / state-machine work
```

### Worked example: `git log` with N = 1 MB output, top-50-commits matter

Empirically, default `git log` formats commits as ~120 bytes of header
plus ~80-200 bytes of indented subject + body per commit. Take the
denser case: ~200 bytes per entry. So 50 commits ~= 10 KB of raw bytes.

The compact-tier git_log compressor renders one line per commit at
roughly `<sha7> <author-initials> <date> <subject>` ~= 60-70 chars,
which is ~16-20 cl100k tokens per commit (initials and ISO dates are
sub-token-efficient).

50 commits in compact view ~= 1000 tokens. If the agent's
`max_output_tokens` for this call is 1000, the kill point is reached
after k* ~= 10 KB of raw bytes consumed. The remaining 1 MB - 10 KB
= 990 KB never needs to traverse the pipe.

Throughput estimate: `git log` against a hot pack file streams
~50 MB/s on a typical SSD. Wall-clock cost of letting it run to
completion on 1 MB:

```
1 MB / 50 MB/s = 20 ms
```

Wall-clock cost of stopping at 10 KB:

```
10 KB / 50 MB/s ~= 0.2 ms     (subprocess CPU)
+ 1 ms                        (SIGTERM dispatch + reap)
~= 1.2 ms
```

Ratio: ~17x faster on this call. On a cold repo where `git log` has to
walk uncached pack files (~10 MB/s), the savings stretch to 100 ms ->
6 ms, ~16x faster, and avoid 1 MB of disk reads from the pack.

For a longer history (say 10 MB output, 5000 commits matter only first
50): same kill point, savings scale to ~165x.

### Per-stream f estimator

The streaming estimator maintains running counters per compressor
schema. For git_log:

```
on each line:
    if line starts with "commit ":
        commit_count += 1
        approx_tokens += 18    (sha + author + date + subject avg)
    elif line.startswith("    ") and current_commit_subject_done:
        # body line, dropped at COMPACT
        pass
    elif line.startswith("Author: "):
        approx_tokens += 0     (folded into the 18 above)
```

The per-line cost is ~10-50 ns of Python work, dominated by the
prefix-gated `startswith` (already idiomatic in current parsers, see
BASELINE prefix-gating note). On a 1 MB stream that is ~5000 lines,
total estimator overhead is ~250 us - negligible vs the 20 ms that the
subprocess would otherwise have run.

For grep / rg --json: f counts distinct file paths plus capped per-file
match count; one increment per line.

For find: counts entries; estimator increments by 4-8 tokens per line
based on path-tree sharing rate (rolling average maintained over last
256 lines).

For pytest: counts FAILED / ERROR lines and per-failure first
meaningful traceback line; passes contribute 0 (they collapse to
"96 passed" footer regardless).

For lint: counts unique (rule_id) and first occurrence line per file;
later occurrences contribute ~0.

### Kill threshold

The kill condition is:

```
compressed_so_far >= s * min(hint.max_output_tokens,
                              hint.remaining_tokens * 0.30)
```

where 0.30 is `_BUDGET_SHARE` (already in `redcon/cmd/budget.py`). The
safety factor s defaults to **1.20** (over-fill by 20% to give the
compressor headroom; the COMPACT formatter will then drop tail entries
during final selection without losing the head). Setting s < 1 would
risk under-filling because the streaming estimator is approximate.

## Concrete proposal for Redcon

Three new pieces, all gated on opt-in per-compressor capability so the
default path is bit-for-bit identical to today.

### 1. Streaming compressor protocol

Add to `redcon/cmd/compressors/base.py`:

```python
@runtime_checkable
class StreamingCompressor(Protocol):
    """Optional capability: produce a running compressed-token estimate
    while bytes are still arriving. Compressors implementing this can
    drive early-kill in the runner."""

    schema: str

    def stream_state(self) -> object:
        """Return a fresh per-call state object."""
        ...

    def stream_feed(self, state, chunk: bytes) -> int:
        """Feed a chunk; return updated compressed-token estimate (cumulative)."""
        ...

    def stream_finalize(self, state) -> None:
        """Called once the stream is done (EOF or kill). Must be idempotent."""
        ...
```

Only `git_log`, `grep`, `find`, `pytest`, `lint`, `git_status` initially
implement it. `git_diff`, `docker`, `kubectl`, `pkg_install`, listing
(`ls -R`, tree) do not - their f is not monotone in stream prefix.

### 2. Runner integration

`redcon/cmd/runner.py` gains an optional `stream_observer: Callable[[bytes], int] | None`
parameter on `RunRequest`. When set, after each successful
`_append_capped(stdout_buf, chunk, ...)` the runner calls it with the
chunk, reads back the cumulative-compressed-token estimate, and
compares to a kill threshold passed alongside:

```python
@dataclass(frozen=True, slots=True)
class RunRequest:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str] | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = MAX_OUTPUT_BYTES
    # NEW (V56)
    stream_observer: "StreamObserver | None" = None    # callable wrapper
    early_kill_token_budget: int | None = None         # None = disabled
```

In the read loop:

```python
if observer is not None and proc.stdout in ready and chunk:
    est = observer.feed(chunk)
    if est >= early_kill_token_budget:
        early_kill_reason = f"compressed-tokens estimate {est} >= budget {early_kill_token_budget}"
        _terminate(proc)
        break
```

Symmetric for stderr if the compressor opts in (most don't; stderr is
usually small and structurally different).

The runner returns the existing `RunResult` with two new fields:

```
early_killed: bool
early_kill_token_estimate: int | None
```

These propagate to `CompressionReport.notes` and the
`_meta.redcon` block emitted by MCP.

### 3. Pipeline glue

`redcon/cmd/pipeline.py::compress_command`:

```python
compressor = detect_compressor(argv)
observer = None
budget_for_kill = None
if isinstance(compressor, StreamingCompressor):
    state = compressor.stream_state()
    observer = StreamObserver(compressor, state)
    safety = 1.20
    budget_for_kill = int(safety * min(
        effective_hint.max_output_tokens,
        int(effective_hint.remaining_tokens * 0.30),
    ))
request = RunRequest(
    argv=argv, cwd=cwd_path, timeout_seconds=...,
    stream_observer=observer,
    early_kill_token_budget=budget_for_kill,
)
run_result = run_command(request)
```

After the run, the existing per-compressor `compress(...)` call still
fires on the captured (possibly truncated) bytes. The compressor must
already be robust to mid-stream truncation - the quality harness in
`redcon/cmd/quality.py:168-175` already feeds it `b"truncated mid-stream
because the buffer ran out at exactly thi"` and asserts no crash. So
graceful degradation is **already a tested invariant** of every
compressor on the allowlist; V56 just exercises it more often.

When `run_result.early_killed`, the pipeline appends a note:
`"early-killed at ~N compressed tokens; tail of subprocess output not captured"`
so the agent can decide whether to re-run with a higher budget.

### 4. Determinism handling

Cache key MUST include the `early_kill_token_budget` (or its absence).
Otherwise two runs with different budgets would clobber each other's
cache entries with semantically different outputs. Concretely, extend
`build_cache_key` to incorporate `("ek", budget_for_kill)` as a tuple
member of the canonicalised argv-equivalent. This is a strict superset
of the current key - constraint #6 satisfied.

## Estimated impact

- Token reduction: **0 pp** on the compressed output itself (V56 is a
  cost-side optimisation, not a token-side one). The compressed output
  the agent sees is the same as it would see if the subprocess had run
  to completion *and the output had been truncated to budget*. V56's
  win is upstream of the agent.
- Latency on long-tailed commands:
  - git log (1 MB raw, 50 commits matter): ~17x faster (~20 ms -> ~1 ms).
  - grep on a 200 MB log file with first 100 hits saturating budget:
    >100x (full grep walks the file; killed grep stops at the first
    saturation chunk).
  - pytest -v with 5000 passing tests + 3 failures: ~3-5x for test
    *collection-and-run* time only if --exitfirst not set; if user has
    -x already, ~1x (early-exit handled by pytest itself).
  - find on a tree where the first directory contains > budget worth of
    paths: ~5-10x.
- Latency on short outputs: ~0% change. The observer is called only on
  chunks; for sub-chunk outputs (single 64 KiB read) the kill threshold
  is checked once, fires exactly when it would have fired post-collection.
- Cold-start: regex / streaming-state objects are lazy-imported per
  compressor; no global module cost. Adds ~150 LoC, no new top-level
  imports.
- Affects:
  - `redcon/cmd/runner.py`: new RunRequest fields + read-loop branch.
  - `redcon/cmd/pipeline.py`: glue to wire observer when compressor opts in.
  - `redcon/cmd/cache.py`: cache key superset including budget.
  - 6 of 11 compressors gain a new optional surface; the other 5 unchanged.
  - Quality harness gains a new check: "if streaming, the streamed
    estimate must converge to the actual compressed token count within
    25% on the corpus."

## Implementation cost

- Lines of code:
  - `base.py` protocol: ~25 LoC.
  - `runner.py` integration: ~30 LoC (one branch in the read loop, two
    new RunRequest / RunResult fields).
  - `pipeline.py` glue: ~25 LoC.
  - `cache.py` key extension: ~10 LoC.
  - 6 streaming-compressor implementations: ~40 LoC each = ~240 LoC.
  - Quality harness convergence check: ~50 LoC.
  - Tests: ~150 LoC.
  - **Total: ~530 LoC.**
- New runtime deps: none. Pure stdlib `re` + existing tokenizer.
  No network. No embeddings. Determinism preserved as long as the
  cache key is extended (see above).
- Risks to determinism:
  - Two runs with the same argv but different `remaining_tokens` (the
    agent's running budget) would receive different early-kill points
    and different captured byte prefixes. **This already breaks the
    current cache invariant** (key is argv + cwd, not budget). Fix: as
    described, extend the key. Without that fix, V56 is a regression.
  - The stream_observer's feed function must be a pure function of the
    chunk and prior state. Easy to enforce in code review.
- Risks to robustness:
  - A killed subprocess's last chunk may end mid-line. The compressor
    must still parse what it has. Already covered by the `_check_robustness`
    fixture (line 175 of quality.py: `b"truncated mid-stream because
    the buffer ran out at exactly thi"`). New fixtures should add
    "truncated at end of nth git-log entry" and "truncated mid-pytest-FAILURE-block"
    to make the harness explicitly cover the V56 trigger pattern.
  - SIGTERM race: between sending SIGTERM and the subprocess actually
    stopping, more bytes may arrive in the pipe. The runner already
    drains those (see `_drain_remaining`, runner.py:265), so the
    captured prefix is at least as long as the kill-decision prefix.
- Risks to must-preserve guarantees:
  - On `git log` early-killed, must_preserve `r"\bcommit\b|^[0-9a-f]{7,40} "`
    still matches because at least one commit was captured (the budget
    is sized to fit ~50 commits; we kill at >=1.20 * budget). Empty-output
    edge case (kill before first commit): handled by the existing
    `if not result.entries: return ""` branch in git_log compressor.

## Disqualifiers / why this might be wrong

1. **Subprocess CPU is rarely the bottleneck.** Most allowlisted
   commands (git, grep, find) are I/O-bound on warm caches; they finish
   in 5-50 ms even on large outputs. The 20 ms -> 1 ms saving is real
   but tiny in agent-loop terms (LLM call latency dominates by 10-100x).
   V56 may be a true optimisation that has near-zero observable impact
   in production. **However**, it does reduce wasted disk I/O on cold
   caches, where commands like `git log` over a 50k-commit repo first
   call can take 500 ms+; on those, 17x adds up to a meaningful
   ~470 ms saving per such call. The case for V56 is "the long tail of
   slow runs" rather than "the median".
2. **Already partially achieved by `redcon/cmd/rewriter.py`.** The
   rewriter canonicalises argv and can inject `-n 50` into git log
   when `prefer_compact_output` is on. That is a *better* solution
   when applicable: it tells the tool itself to stop early, which is
   strictly cheaper than V56 (no inline parsing, no SIGTERM machinery).
   V56 only earns its keep on commands without a native `-n` / `--head`
   / `-l` analog, or where the right N is unknown until the agent's
   running budget is known. Concretely: grep with a streaming pipe
   (no native cap), find with a complex predicate (cap is post-filter),
   pytest with --tb=short (no first-N option). For `git log`, V56 is
   redundant if the rewriter is on.
3. **Streaming f estimator may diverge from final f.** A compressor
   that does a final dedup or top-K cut (grep dedups paths; pytest
   collapses warnings) may underestimate during stream - we kill too
   late - or overestimate - we kill too early. Mitigation: convergence
   harness (mentioned above) bounds divergence within 25%; safety factor
   1.20 is calibrated to absorb the same. Empirical test: on the M9
   benchmark fixtures, run streaming feed and compare cumulative
   estimate to final compressed_tokens; assert |est_final / actual - 1|
   < 0.25 for every fixture.
4. **The 16 MiB hard cap already serves the worst case.** V56 saves
   bytes between "budget reached" and "16 MiB reached". For the median
   command this window is empty (output well under both). For the
   pathological case the existing cap fires. The "interesting middle"
   - outputs in the 100 KB to 5 MB range that exceed budget but not the
   hard cap - is where V56 shines. Quantifying that band on real agent
   traces is required to justify the implementation cost; absent that
   data, this could be an in-search-of-a-problem optimisation.
5. **Backpressure already exists implicitly via the OS pipe buffer.**
   The kernel's 64 KiB pipe buffer + the runner's blocking `read1`
   creates natural backpressure: when the runner doesn't drain, the
   subprocess blocks on write. The runner, however, is in a tight
   read loop, so it always drains - meaning backpressure is never
   exercised in the current code. V56 effectively replaces that
   never-used soft signal with an explicit kill. (This relates to V59
   "PIPE pause backpressure" - V56 is the harder-edged version. V59
   is more conservative: pause without killing, resume if budget is
   later raised. V56 is non-resumable, so once killed the work is lost.)

## Verdict

- Novelty: **medium**. The general technique (early termination on
  budget) is standard in stream processing. The novel bits for Redcon
  are (a) using *compressed* tokens not raw bytes as the kill signal,
  (b) declaring it a per-compressor capability so robustness is opt-in,
  (c) integrating it with the cache key to preserve determinism. None
  of those individually is a breakthrough, but together they make a
  clean architectural addition. Not a token-side breakthrough by the
  BASELINE bar (no >=5 pp compact-tier shift, no >=20% cold-start cut).
- Feasibility: **high**. All required machinery (Popen + select +
  bounded buffers + SIGTERM ladder) already exists in `runner.py`. The
  protocol is a small extension. Most compressors are already
  prefix-gated on first-byte dispatch (per BASELINE), which is exactly
  the streaming pattern.
- Estimated speed of prototype: **2-3 days** for the core (protocol +
  runner + pipeline + 2 streaming compressors: git_log + grep) with
  tests and convergence harness. Another 2-3 days to roll out to the
  remaining 4 streaming-eligible compressors.
- Recommend prototype: **conditional-on-X**, where X = "production
  trace shows >5% of `redcon_run` calls have raw-bytes / final-compressed-tokens
  ratio > 10x". Without that evidence, V56 is correct but unmotivated;
  with it, V56 is a clean, deterministic, robust win that compounds
  with rewriter (V56 covers cases rewriter cannot) and with log-pointer
  tier (V56 fires *before* log-pointer's 1 MiB threshold, reducing how
  often log-pointer is needed at all).

## File pointers

- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/runner.py
  (lines 30-69: caps and timeouts; lines 152-227: the read loop where
  the observer hook lives; lines 265-289: existing drain logic that
  already handles post-kill bytes; lines 292-309: SIGTERM/SIGKILL
  ladder reused as-is)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/pipeline.py
  (lines 107-148: where the compressor is detected and run, the glue
  for stream_observer; lines 60-63: log-pointer threshold which V56
  pre-empts)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/budget.py
  (line 47: `_BUDGET_SHARE = 0.30`, the multiplier the kill threshold
  reuses)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/quality.py
  (lines 168-175: existing robustness corpus including
  `truncated mid-stream`, the property V56 amplifies)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/compressors/base.py
  (lines 32-57: the Compressor Protocol, sibling location for the new
  StreamingCompressor Protocol)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/compressors/git_log.py
  (lines 27-30: line-level regexes already prefix-gated, ready to be
  reused as the streaming feeder for git_log)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/rewriter.py
  (related: argv-level capping that supersedes V56 when applicable -
  V56 only earns its keep where rewriter cannot)
