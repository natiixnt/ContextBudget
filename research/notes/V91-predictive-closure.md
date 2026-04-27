# V91: Predictive closure - bundle the next-line lookups the agent will need

## Hypothesis

After Redcon emits a structured failure report (pytest, lint, grep,
stack-trace), the agent's next move is *empirically deterministic*:
read a small line window around the cited address. We claim that
**eagerly reading a 5-9 line window around every cited `file:line` and
inlining it inside the original CompressedOutput** trades a small,
bounded prefix tax for a much larger expected saving from skipped
follow-up reads. Concretely: for the pytest compressor today the
COMPACT body for one failure is ~22 cl100k tokens (FAIL header + 1
clipped message line). Adding a 5-line context window from the cited
file adds ~45-55 tokens. A single follow-up `Read(path, line-2,
line+5)` round trip costs ~25 tokens of MCP framing plus ~50 tokens
of returned body plus ~30 tokens of agent-side prompt-text - call it
100-130 tokens minimum. Break-even is therefore one avoided follow-up
read in ~2 cited failures, well under the empirical follow-up rate
when the agent is actively fixing the test (it must read the source
to write a fix). This is **predictive caching at carrier
granularity**: the encoder pre-resolves the deep links it just
emitted.

This is the call-by-value dual to V44 (call-by-need deep links) and
the eager arm of V09 (selective re-fetch markers). Same address space,
opposite shipping policy.

## Theoretical basis

Treat the agent's per-failure follow-up as a Bernoulli with
probability `r` (the "read rate"). Per cited address `i`:

```
B_i  = inline-context tokens we would emit eagerly
       (5 lines * ~9 tokens cl100k + 1 frame line = ~46)
L_i  = current cost (no context emitted; agent must fetch) = 0
F_i  = round-trip framing of one Read call
       (request prompt fragment ~30, MCP envelope ~25, returned body ~B_i) = 25 + 30 + B_i
r_i  = empirical probability that the agent reads address i this turn
```

Inlining always: deterministic cost `B_i`.
Not inlining: expected cost `r_i * (B_i + F_i)`.

Eager inlining wins iff:

```
B_i  <  r_i * (B_i + F_i)
=> r_i  >  B_i / (B_i + F_i)         ... (1)
```

This is the symmetric flip of V44's Eq. (1). For typical numbers:

```
B_i = 46, F_i = 55 (= 25 framing + 30 prompt-side fragment) => r* = 46 / 101 = 0.456
```

So **whenever the agent reads more than ~46% of cited addresses,
predictive closure saves tokens in expectation**. For pytest failures
during an active fix, observed read rates approach 1.0 (the agent
*must* see the source line to write the patch). For triage workflows
("which 50 tests broke?") the read rate per cited failure drops, but
then the COMPACT tier likely already promoted to ULTRA via budget
selection, in which case V91 is a no-op (ULTRA tier exempt).

Aggregate over `n` cited carriers in one output, with the
quality-harness floor ratio at COMPACT (30% reduction floor on inputs
>= 80 raw tokens):

```
ExpectedSave = sum_i  r_i * (B_i + F_i)  -  B_i             ... (2)
```

For a recorded pytest run with 6 failures, B_i = 46, F_i = 55, and a
plausible r_i = 0.7 across all six (mid-range between triage and
fix):

```
ExpectedSave = 6 * (0.7 * 101 - 46) = 6 * 24.7 = ~148 tokens
```

vs the marker cost being `n * 46 = 276` added tokens unconditionally.
Net session saving = 148 tokens (about 1 medium agent turn worth) per
pytest invocation when the agent does end up reading.

A second-order effect: V91 also shrinks the **wall-clock latency** of
the agent's path-to-fix. One serial MCP round trip removed per cited
failure is ~50-200 ms each on local stdio MCP. For 6 failures
fix-then-rerun loop that's a half-second saved per iteration -
outside the token economy but real to the user.

## Concrete proposal for Redcon

A new module **`redcon/cmd/predictive_closure.py`** plus an opt-in
hook on the test_format and stack-trace renderers. All shipped
behind `BudgetHint.predictive_closure: bool = False` until measured.

**1. Per-carrier `closure_window()` resolver**

```python
# redcon/cmd/predictive_closure.py  (sketch only - do NOT implement now)

from pathlib import Path

@dataclass(frozen=True, slots=True)
class ClosureWindow:
    path: str             # canonical relative path, same as link grammar
    center_line: int      # 1-indexed
    lines: tuple[str, ...]   # the window itself, line-trimmed
    span_before: int
    span_after: int

# Hard caps - load-bearing for the gate (see "Risk" below).
WINDOW_BEFORE = 2
WINDOW_AFTER = 4
MAX_LINE_LEN = 200          # clip individual lines
MAX_TOTAL_LINES_PER_OUTPUT = 60  # over all carriers

def resolve_window(
    path: str, line: int,
    *, before: int = WINDOW_BEFORE, after: int = WINDOW_AFTER,
    cwd: Path,
) -> ClosureWindow | None:
    # Determinism: same path + same on-disk content + same range -> same output.
    full = (cwd / path)
    if not full.is_file():
        return None
    try:
        # Read only what we need; line-by-line for memory bound.
        text_lines = full.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    n = len(text_lines)
    if not (1 <= line <= n):
        return None
    lo = max(1, line - before)
    hi = min(n, line + after)
    return ClosureWindow(
        path=path, center_line=line,
        lines=tuple(_clip(s, MAX_LINE_LEN) for s in text_lines[lo - 1 : hi]),
        span_before=line - lo, span_after=hi - line,
    )
```

**2. Render hook in `test_format._format_compact`**

```python
def _format_compact(r: TestRunResult, ctx: ClosureCtx | None = None) -> str:
    lines = [_summary_line(r)]
    if r.failures:
        lines.append("")
        used_window_lines = 0
        for failure in r.failures:
            location = _format_location(failure)
            head = f"FAIL {failure.name}" + (f" ({location})" if location else "")
            lines.append(head)
            short_msg = _first_meaningful_line(failure.message)
            if short_msg:
                lines.append(_clip(short_msg, 200))
            # Predictive closure: bundle the lines the agent will likely read next.
            if (ctx is not None and ctx.enabled
                and failure.file is not None and failure.line is not None
                and used_window_lines < MAX_TOTAL_LINES_PER_OUTPUT):
                window = resolve_window(failure.file, failure.line, cwd=ctx.cwd)
                if window is not None:
                    lines.append(f"  ~{failure.file}:{window.center_line - window.span_before}-{window.center_line + window.span_after}")
                    for off, body in enumerate(window.lines):
                        ln = (window.center_line - window.span_before) + off
                        marker = ">" if ln == window.center_line else " "
                        lines.append(f"  {marker}{ln:>4} {body}")
                    used_window_lines += len(window.lines)
    ...
```

The window block is **6-9 short lines per failure**, sub-50 tokens
typical. Lexicographically deterministic; line-numbered; the centre
line is `>`-marked to mirror pytest's own style so must-preserve
patterns matching the failure text still hit.

**3. Pipeline plumbing**

```python
# redcon/cmd/pipeline.py::compress_command (sketch)
ctx = ClosureCtx(
    enabled=hint.predictive_closure and level == CompressionLevel.COMPACT,
    cwd=run_cwd,
)
# Pass into the compressor's CompressorContext.
```

V91 is gated **only at COMPACT**:

- VERBOSE already includes the original snippet (`>`-prefixed body),
  so windows are redundant noise.
- ULTRA must stay tiny; per BASELINE the floor is 70% reduction and
  inputs <80 raw tokens skip the floor. Adding 50 tokens here would
  routinely break it.

**4. Determinism contract**

The resolver reads from disk, so to keep
"same-input-same-output-byte-identical" the cache key must include a
path-set fingerprint. Two clean options:

- **Stat-fingerprint**: include `(path, mtime_ns, size)` for every
  file the closure read into the canonicalised cache key. Cheap.
  Loses cache hits across editor saves, which is correct.
- **Content SHA**: read the closure window, hash bytes, include hash
  in the key. More expensive (one hash per cited file) but
  truly content-keyed.

Either way it's a strict superset of the existing
`(canonical_argv, cwd_hash)` key, satisfying BASELINE constraint #6.

**5. Risk gate (load-bearing)**

V91 enlarges output. To keep within the COMPACT 30% reduction floor,
gate every closure attempt on:

```python
def closure_fits(remaining_budget: int, current_tokens: int,
                 added_tokens_estimate: int, raw_tokens: int) -> bool:
    # Refuse to break the COMPACT 30% reduction floor.
    floor_max = int(raw_tokens * 0.70)
    return (current_tokens + added_tokens_estimate) <= floor_max \
        and (current_tokens + added_tokens_estimate) <= remaining_budget
```

Concretely: the renderer runs failures in deterministic order, builds
windows greedily, and stops the moment `closure_fits` returns False.
This keeps the quality harness happy at all three tiers.

**6. Files touched (sketch)**

- `redcon/cmd/predictive_closure.py` - new module, ~80 LOC.
- `redcon/cmd/types.py` - add `ClosureWindow`, optional context
  dataclass.
- `redcon/cmd/budget.py` - `BudgetHint.predictive_closure: bool =
  False`.
- `redcon/cmd/compressors/test_format.py` - thread `ClosureCtx`,
  render windows in `_format_compact`.
- `redcon/cmd/compressors/pytest_compressor.py`,
  `cargo_test_compressor.py`, `npm_test_compressor.py`,
  `go_test_compressor.py` - pass through the context. No parser
  change.
- `redcon/cmd/pipeline.py::compress_command` - construct the context
  from hint + run cwd.
- Optional: `redcon/cmd/compressors/lint_compressor.py` - same
  treatment for cited `path:line:col` lint diagnostics; fewer
  failures-per-run but identical mechanics.
- Quality fixtures - new ones with a real on-disk source file so the
  closure resolver has something to read.

## Estimated impact

- **Token reduction (per-call, COMPACT, immediate)**: pytest output
  *grows* by ~30-45% relative to current COMPACT. Concretely a 6-fail
  output grows from ~130 tokens to ~400. **Per-call reduction
  drops from 73.8% to ~50% on the standard pytest fixture.**
- **Token reduction (per-session, with follow-ups)**: net session
  save of ~120-200 tokens per pytest run when `r_i >= 0.5`,
  break-even at `r_i ~ 0.46` (Eq. 1). Net loss `~270` tokens when
  `r_i = 0`.
- **Crossover** (Eq. 1): `r* = B / (B + F)`. For B=46, F=55 -> 0.46.
  For larger windows (B=80) crossover rises to ~0.59. Smaller windows
  (B=25) push it down to ~0.31, but at the cost of less actually
  *useful* context.
- **Latency**: cold-start unchanged. Per-call cost adds one
  `Path.read_text` per cited failure: ~0.2 ms each, ~1 ms typical
  total. Stat-fingerprint cache key bump: another ~0.1 ms.
  Wall-clock saving from skipped agent round trips: 50-200 ms each
  on local stdio MCP, dominating the per-call cost.
- **Affects**: pytest, cargo_test, npm_test, go_test (all share
  test_format.py, all carry `file:line` failures), lint
  (path:line:col), stack-trace compressor if/when it lands. Does not
  touch git_diff (no centre line to predict from, hunks already
  carry the lines), grep (the match line *is* the body), git_log,
  git_status, ls, find, docker, pkg_install, kubectl.

## Implementation cost

- ~180 LOC: new module (~80), test_format hook (~30), pipeline wire-up
  (~20), per-compressor pass-through (~10), tests/fixtures (~40).
- **No new runtime deps.** Uses only stdlib `pathlib`. No network. No
  embeddings. No model.
- Cache-key bump: stat-fingerprint or content-hash on cited files, a
  strict superset of the current key (BASELINE #6 satisfied).
- **Determinism**: fully preserved. Identical disk -> identical
  window. Stat-fingerprint cache key correctly invalidates on edit.
- **Robustness**: malformed `file:line` -> resolver returns None ->
  fall through to no-window output (current behaviour). Cap on
  total window lines (`MAX_TOTAL_LINES_PER_OUTPUT = 60`) bounds
  worst-case adversarial input. Per-line length clip
  (`MAX_LINE_LEN = 200`) mirrors `_clip` already used elsewhere.
- **Must-preserve**: window block is *additive*; the failure-name
  pattern (`re.escape(f.name)`) and existing failure message lines
  are unchanged. Patterns continue to verify.

## Disqualifiers / why this might be wrong

1. **Empirical read rate may be below crossover.** If real agents
   triage long pytest lists without reading any source ("which test
   files are these failures in?"), `r_i` collapses below 0.46 and
   V91 just inflates output. This is the dual symmetric concern to
   V44's "agents fetch too often". The honest gate is a recorded
   trace; without it, ship behind the flag default-off.
2. **Stale window risk.** If the test ran against a file at revision
   X and the agent has already started editing, the resolver reads
   the *post-edit* file - which has wrong line numbers and worse,
   maybe-fixed content that contradicts the failure message. Same
   class of bug V44 has under SHA-pinning. Mitigation: emit the
   stat-fingerprint into the window header
   (`~path.py:42-48 @mtime=...`) so the agent can detect drift, and
   recommend running redcon close to the test invocation.
3. **The resolver opens files outside what the command produced**,
   which subtly widens the trust boundary of `redcon_run`. Today
   `redcon_run` only inspects what its subprocess emitted to
   stdout/stderr. V91 introduces filesystem reads keyed off
   *content of stdout*. A malicious test file path in stdout (e.g.
   `/etc/shadow:1`) could be coerced into reading sensitive files.
   Mitigation: confine the resolver to the run cwd subtree (reject
   absolute paths and `..` segments). The pytest path format is
   already relative-to-cwd in practice.
4. **Already partially overlapping with verbose tier.** VERBOSE
   already emits up-to-8 `>`-prefixed snippet lines from the pytest
   *failure block*. V91 differs in that it reads from the *source
   file*, not from the failure block - so for short test functions
   the window includes lines pytest never showed (assert helper,
   fixture scope, surrounding test). The novelty is "see what the
   agent will go looking for", not "show what pytest already
   printed". Still, on benchmarks where pytest's traceback already
   contains the right window, V91 is duplicate noise.
5. **Latency saved is wall-clock, token spent is real**. V91 trades
   a *certain* token cost for an *uncertain* token + latency
   saving. From the budget perspective (Redcon's stated mission)
   this is a regression on every COMPACT call where the agent
   doesn't follow up. The user-perception case for V91 is strong
   *only* if you also instrument the followed-up case. Without
   that, this looks like inflation.
6. **Composes with V44 in a way that may cancel.** V44 says
   "replace inline body with a link, agent fetches on demand". V91
   says "eagerly inline a body the agent will probably need".
   Running both at once produces *both* the link *and* the eager
   body, which is strictly worse than either alone. The clean
   integration is: V09 emits refetch candidates -> V44 renders
   them as links -> V91 *resolves* a link's body iff the
   compressor's predictor says `r_i > 0.46`. V91 is then the
   "eager" arm of the V44 dispatcher.

## Verdict

- Novelty: **medium**. Predictive closure as a per-carrier mode is
  not a textbook construct; the lazy/eager dichotomy with V44 is
  the cleanest framing. Strictly an extension of the must-preserve
  + deep-link surface area. Below "breakthrough" because it does
  not compound across calls (V41-V50 territory) and depends on a
  tunable read-rate that may not hold across agent populations.
- Feasibility: **high**. The resolver is 30 lines of stdlib; the
  test_format hook is mechanical; the cache-key superset is
  routine. Fixture infrastructure (a real on-disk file the
  resolver can read) is the minor friction.
- Estimated speed of prototype: **2 days** for pytest-only behind
  `BudgetHint.predictive_closure`, plus stat-fingerprint cache
  keying, plus quality-fixture rework. **1 week** to extend across
  the four test runners + lint and add a recorded-trace replay
  measuring `r_i` per carrier class.
- Recommend prototype: **conditional-on** instrumenting at least
  one recorded agent trace to estimate `r_i` per carrier class
  per workflow. If the median `r_i` clears 0.5 across pytest +
  cargo_test, build it and ship it co-flagged with V09 and V44 so
  the three modes (signal / link / eager body) compose cleanly.
  If `r_i` is consistently below 0.4, do **not** build V91 -
  V44 (lazy link) is the correct shape and V91 is a regression.
