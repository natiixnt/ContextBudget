# V57: Anytime-algorithm pipeline - emit best-so-far compressed output on interrupt

## Hypothesis

The current `compress_command` runs the chosen compressor to completion or
crashes. Subprocess execution is bounded (`timeout_seconds`, 16 MiB cap, log
pointer at 1 MiB), but the parsing / formatting stage downstream of
`run_command` has no analogous escape hatch. If a parser were ever to wedge
on a pathological input (a `tree` walk with millions of entries, a `find`
with a 30 MB path list, a future SQL EXPLAIN compressor on a query plan with
nested loops several million rows deep), the only outcomes are: (a) succeed,
(b) hit the runner's hard timeout and bubble `CommandTimeout`, (c) raise some
parser-internal error. None of these returns a compressed result. The claim
of V57: each compressor exposes a `partial_emit(state) -> CompressedOutput`
checkpoint, the pipeline registers a `signal.SIGALRM` (or `SIGTERM` on the
process boundary) handler that calls `partial_emit` on the most recent
checkpoint and returns whatever fraction has been processed so far,
truncation-flagged. **This is a robustness story, not a token-reduction
story** - on the existing 11 compressors, parse times are sub-2 ms even on
huge outputs (the runner already documents this as the reason no streaming
was added). So the value is purely cold-tail: turn a 0% "no result" into a
70-90% "result over a partial input" on the <1% of workloads that hit the
boundary.

## Theoretical basis

Anytime algorithms are due to Dean and Boddy (1988) and Horvitz (1988): a
computation that maintains a "current best answer" and can be interrupted
at any time, returning a partial result whose quality is monotonically
non-decreasing in elapsed time. The standard performance profile is

```
Q(t) = quality at time t
Q(t) is non-decreasing
Q(t -> infty) = Q*
intervention rule: at SIGALRM, return state -> partial_emit(state)
```

For a streaming line-oriented parser, a natural Q(t) is the fraction
`f(t) = lines_consumed(t) / total_lines`. Each Redcon compressor's
`compress` method already maps raw bytes to a structured canonical type
(`DiffResult`, `ListingResult`, `LintResult`, etc.), then formats it. So
"best-so-far" for any of them is just the canonical type built from the
prefix of input that has been consumed.

Back-of-envelope on the value:

```
let p = P(parse takes >= timeout) on real workloads
existing data points (BASELINE.md): "sub-2 ms even on huge outputs"
for parse to dominate the 120 s default timeout, raw size would need to
exceed roughly:
    2 ms * (120 s / 2 ms) = 60_000x of "huge" sample
"huge" sample observed: ~16 MiB (the cap)
=> parse-bound timeouts effectively impossible at current cap
real bound: log_pointer at 1 MiB triggers spill BEFORE parse runs
=> p ~= 0 in equilibrium
```

But the runner's *subprocess* timeout (`CommandTimeout`) is a different
beast and fires on slow tools (e.g. `git log --all` on a 200 k-commit repo,
`find /` on a deep tree, `grep -r` on a monorepo without `.gitignore`
exclusions). When `CommandTimeout` raises today, `compress_command` raises
too - the partial bytes already drained into `stdout_buf` are *thrown away*.
That is the actual lift surface for V57: salvage the partial subprocess
output and run a partial-aware parse on it, instead of re-raising.

Quantification via the runner code path:

```
runner.py L161-202: stdout_buf is a bytearray that grows incrementally
                    until either (a) EOF, (b) cap hit, (c) deadline hit
runner.py L165-169: on deadline, _terminate(proc) AND CommandTimeout raised
                    stdout_buf is DISCARDED (caller receives an exception)
```

So the salvageable partial buffer already exists in memory and is already
time-bounded by the runner; V57 is fundamentally "stop discarding it."

Estimated fraction of real workloads:

```
Tools likely to hit subprocess timeout:
  - find on huge trees: <0.5% of redcon_run calls in agent sessions
  - git log -all on giant repos: <0.1%
  - docker build (already log-pointer'd): handled
  - grep -r without exclusions: ~1% (depends on repo)

Estimate: 0.5-2% of redcon_run calls would benefit.
Of those, value = (delta from "exception" to "useful 70% result") which
is qualitatively large but not measurable in pp on the BASELINE table.
```

So this contributes 0 pp to the COMPACT-tier reduction headline and a
non-trivial improvement to the 99th-percentile UX.

## Concrete proposal for Redcon

Two surgical changes, no breaking API.

**Change 1: `redcon/cmd/runner.py` returns a `RunResult` on timeout instead of raising.**

Today, `CommandTimeout` is raised from inside the loop and the partial
bytes are abandoned. Add a `partial_on_timeout: bool = False` knob to
`RunRequest`; when set, the runner returns the partial buffer with a
note `"timed out after Ns, partial output"` and `truncated_stdout=True`,
and lets the *caller* decide how to handle. Caller-driven, opt-in,
backward-compatible.

```python
# runner.py change inside the timeout branch
if now >= deadline:
    _terminate(proc)
    if not request.partial_on_timeout:
        raise CommandTimeout(...)
    notes_partial = (f"timed out after {request.timeout_seconds}s, partial output",)
    return RunResult(
        argv=request.argv, cwd=cwd, returncode=124,  # GNU timeout convention
        stdout=bytes(stdout_buf), stderr=bytes(stderr_buf),
        duration_seconds=time.monotonic() - started,
        truncated_stdout=True, truncated_stderr=True,
        notes=notes_partial,
    )
```

**Change 2: `redcon/cmd/compressors/base.py` adds an optional `partial_emit` protocol.**

```python
# base.py: new optional method on the Compressor protocol
class Compressor(Protocol):
    schema: str
    must_preserve_patterns: tuple[str, ...]
    def matches(self, argv: tuple[str, ...]) -> bool: ...
    def compress(self, raw_stdout: bytes, raw_stderr: bytes,
                 ctx: CompressorContext) -> CompressedOutput: ...
    # optional. default: same as compress() with notes appending "anytime: full".
    def partial_compress(self, raw_stdout: bytes, raw_stderr: bytes,
                         ctx: CompressorContext,
                         deadline_monotonic: float) -> CompressedOutput: ...
```

For listing / lint / grep compressors (line-oriented), `partial_compress`
is mechanical:

```python
# listing_compressor.parse_find rewritten as anytime-aware
def parse_find_anytime(text: str, deadline: float) -> ListingResult:
    entries: list[Listing] = []
    truncated = False
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if (i & 0x3FF) == 0 and time.monotonic() >= deadline:
            truncated = True
            break
        line = raw.strip()
        if not line:
            continue
        kind = "dir" if line.endswith("/") else "file"
        clean = line.rstrip("/")
        entries.append(Listing(path=clean, kind=kind, size=None,
                               depth=clean.count("/")))
    return ListingResult(source="find", entries=tuple(entries), truncated=truncated)
```

Deadline check is gated `(i & 0x3FF) == 0` so the per-line cost is one
cheap masked-and-compare, amortising the `time.monotonic()` syscall over
1024 iterations. On a 16 MiB / ~2 M-line `find` output that is ~2000
deadline checks total - sub-microsecond aggregate overhead.

**Change 3: `redcon/cmd/pipeline.py` wires the deadline.**

```python
# pipeline.py: compress_command thread the deadline through
parse_deadline = time.monotonic() + (timeout_seconds or 120)
try:
    run_result = run_command(request._replace(partial_on_timeout=True))
except CommandTimeout:  # only raised when partial_on_timeout=False
    raise
...
if hasattr(compressor, "partial_compress"):
    compressed = compressor.partial_compress(
        run_result.stdout, run_result.stderr, ctx,
        deadline_monotonic=parse_deadline,
    )
else:
    compressed = compressor.compress(run_result.stdout, run_result.stderr, ctx)
if "timed out" in " ".join(run_result.notes):
    # propagate the truncation into CompressedOutput.truncated
    compressed = dataclasses.replace(compressed, truncated=True)
```

No SIGALRM / signal handler is required: cooperative deadline checking is
strictly safer (signals + Python native code = subtle interactions, e.g.
inside regex C code SIGALRM is delivered late). The loop-level deadline
check gives the same semantics with no FFI-boundary surprises and works
on Windows (which lacks SIGALRM).

**Per-compressor coverage (priorities):**

1. `listing_compressor.py` (find / tree / ls -R): highest payoff, line-oriented,
   trivially anytime-able. 30 LoC delta.
2. `grep_compressor.py`: line-oriented, also trivial. 25 LoC.
3. `lint_compressor.py`: line-oriented per-issue. 25 LoC.
4. `git_diff.py`: hunk-oriented; checkpoint per file boundary, not per
   line, since a half-parsed hunk is invalid. 40 LoC.
5. Test runners (pytest/cargo/npm/go): test-failure-oriented; checkpoint
   per failure record. 30 LoC each but most share infrastructure.
6. `git_log.py`, `git_status.py`, `docker_compressor.py`,
   `kubectl_compressor.py`, `pkg_install_compressor.py`: leave default
   (no `partial_compress` method) - their typical inputs are small enough
   that anytime is unjustified. The protocol's optionality means no work
   here.

## Estimated impact

- Token reduction: **0 pp** on existing fixtures. All current quality-harness
  fixtures complete well within deadline; no path is exercised. This is
  the honest answer.
- Latency: +0 ms on the happy path (deadline check is `(i & 0x3FF) == 0`
  short-circuit + one `time.monotonic()` per 1024 lines ~= 50 ns/line
  amortised; below noise floor of the existing tokenizer cost).
- Robustness: turns 100% of `CommandTimeout`-raising calls in agent
  sessions (estimated 0.5-2% of `redcon_run` calls) into a returned
  `CompressedOutput` with `truncated=True`. Agent then knows to either
  re-issue with smaller scope or live with the partial.
- Affects: `runner.py` (one new `RunRequest` field, one branch),
  `pipeline.py` (deadline thread, `truncated` propagation),
  `compressors/base.py` (optional protocol method), 5-6 compressors
  (line-loop deadline gate). Cache layer unchanged (cache key still
  argv + cwd; partial results *should* still be cached - re-running a
  command that just timed out will time out again, so the cached
  partial is fresh value). Quality harness: add a "synthetic timeout"
  fixture that simulates deadline-clamped parse; assert
  `truncated=True` and `must_preserve_ok=True` on the prefix that did
  parse.

## Implementation cost

- Lines of code: ~35 in `runner.py` + ~25 in `pipeline.py` +
  ~10 in `base.py` + ~30 per anytime-able compressor x 5 compressors
  = ~220 LoC. Plus ~80 LoC of synthetic-timeout fixtures and tests.
- New runtime deps: none. Stdlib `time.monotonic` and existing structures.
- Risks to determinism: **medium**. A deadline-clamped parse on the same
  input could yield different prefixes on different machines (one host
  faster than another). Mitigation: deadline-driven anytime is a
  *robustness* feature, gated behind a non-default
  `RunRequest.partial_on_timeout=True`. Cache key must NOT change. But
  if a partial result lands in the cache and a faster machine would
  have completed, the slower result becomes sticky. Counter-mitigation:
  partial results carry `truncated=True` and the cache layer can be
  taught to *not* cache partials by checking
  `compressed.truncated and "timed out" in compressed.notes`. That
  preserves "deterministic same-input-same-output" because the
  non-deterministic case is excluded from the cache.
- Risks to robustness: low. Partial parse on garbage prefix is the same
  problem the existing harness already fuzzes against (binary garbage,
  truncated mid-stream); deadline-clamped is just "truncated mid-stream
  but with a real prefix."
- Risks to must-preserve guarantees: low to medium. Patterns are
  evaluated by `verify_must_preserve` against the formatted output; on
  truncation the expected patterns may not all be present. Solution:
  must-preserve is verified against the prefix of input actually
  consumed, not the full input - same semantics as the existing
  log-pointer tier (which is `must_preserve_ok=True` because it
  preserves what it can about its truncated view).

## Disqualifiers / why this might be wrong

1. **The frequency is genuinely <1%.** This is a robustness feature for
   the cold tail. On the BASELINE table it does not move a single
   number. If reviewers measure "breakthrough" by reduction-pp, V57
   loses on principle. We are honest about this in the verdict.
2. **The log-pointer tier already eats the 1 MiB+ case.** Real giant
   outputs spill to disk before the parser sees them, returning a
   tail-30 summary. That handles the byte-volume side. V57 only adds
   value when a parse-time-bound *or* a subprocess-time-bound is hit
   on inputs *under* 1 MiB - which is roughly "huge tree but small
   output bytes" (e.g., `find . -type d` on a deep but file-sparse
   monorepo). That is a thin slice.
3. **`signal.SIGALRM` doesn't work on Windows and the cooperative
   variant moves complexity into every line loop.** The proposal
   chooses cooperative deadline checks (mask + compare), which is
   correct, but does mean every anytime-able compressor must be
   refactored to expose its inner loop to the deadline. That is
   tedious and easy to forget on a future compressor (V61 SQL
   EXPLAIN, V67 Kubernetes events). Mitigation: a shared helper
   `_anytime_lines(lines, deadline)` generator that all line-oriented
   parsers can use, so each compressor adds 2-3 lines instead of 30.
4. **Caching partials is a correctness landmine.** If a partial lands
   in the cache, agents will get *worse* answers on cache hits than on
   cache misses (same input, different machine). Mitigation: do not
   cache `truncated and "timed out"` results, as proposed. But this
   contradicts the existing pipeline's "cache everything" simplicity.
5. **Subprocess-bound timeouts dominate parse-bound ones, but the user
   instruction emphasises parse-bound ("if compression itself takes
   too long").** Parse-bound essentially does not happen at current
   sub-2-ms cost. So the strict reading of V57 (anytime parser only)
   is even less impactful than this writeup; the real lift comes from
   salvaging *runner* timeouts, which is arguably a different feature.
6. **Already partially solved by `truncated_stdout` cap behaviour.** The
   runner's 16 MiB cap path *does* return partial output today (it sets
   `truncated=True`, kills the subprocess, and the parser runs on the
   capped buffer). So Redcon already has anytime-on-byte-cap. V57
   extends it to anytime-on-time-deadline. The novelty is the deadline
   axis, not the partial-result idea.

## Verdict

- Novelty: **low** (anytime algorithms are 1988 prior art; the
  cap-path partial result is already in Redcon; this is essentially
  generalising the existing byte-cap mechanism to the time axis).
- Feasibility: **high**. Cooperative deadline gates are local edits, no
  new deps, no signal complexity, optional per compressor.
- Estimated speed of prototype: 1-2 days for a runnable PoC across
  `find` and `grep` (the highest-payoff compressors), 3-5 days for
  full coverage with quality-harness fixtures.
- Recommend prototype: **conditional-on telemetry**. Before writing any
  code, instrument `redcon_run` to log when `CommandTimeout` is
  caught upstream and what fraction of agent sessions trip it. If the
  rate is below ~0.5%, this is below the threshold worth the 220 LoC
  + maintenance burden; just document the failure mode. If the rate
  is above ~2%, ship the runner half (Change 1 alone) since it gives
  most of the value with 35 LoC and the partial buffer is already in
  memory; defer the per-compressor anytime work until a specific
  command shows up in the telemetry.

## File pointers

- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/runner.py
  (L84-90: `RunRequest` dataclass, add `partial_on_timeout`;
   L156: `deadline = started + request.timeout_seconds`;
   L161-169: timeout branch where partial buffer is discarded today;
   L293-307: `_terminate` SIGTERM/SIGKILL is already correct for the
   subprocess side)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/pipeline.py
  (L114-119: `try/except CommandTimeout` - reroute to partial path;
   L124-148: branch on log-pointer / compressor.compress - inject
   `partial_compress` dispatch here;
   L182-211: `_normalise_whitespace` already re-counts tokens, so
   partial outputs report accurate `compressed_tokens`)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/compressors/base.py
  (Compressor protocol: add optional `partial_compress`)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/compressors/listing_compressor.py
  (parse_find L195-207, parse_tree L139-168, parse_ls L64-105: each is
  a one-pass line loop already - drop a deadline gate at the loop head)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/compressors/grep_compressor.py
  (line-oriented parser, same pattern)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/compressors/lint_compressor.py
  (issue-oriented loop, same pattern)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/quality.py
  (extend the harness with a "deadline-clamped synthetic input" fixture
  that asserts `truncated=True` and that prefix patterns that *did*
  appear remain `must_preserve_ok=True`)
