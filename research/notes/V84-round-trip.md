# V84: Round-trip lossless flag - skip compression when raw fits in budget

## Hypothesis
When a command's raw output already fits comfortably under both the per-call
hard cap (`hint.max_output_tokens`) and the per-call share of the remaining
context budget (`_BUDGET_SHARE * remaining_tokens`), running the parser is
pure overhead: a CPU-burned regex pass that returns text materially identical
to (or longer than, due to the schema header) the raw bytes. A short-circuit
guard at the top of `compress_command` - "if raw_tokens <= effective cap then
emit raw with `schema=raw_passthrough`, `level=VERBOSE`" - would save parser
CPU on the long tail of small invocations (e.g. `git status` with three files,
`ls` of a flat directory, `pytest` runs that pass with one or two tests).

The prediction: a measurable but small wall-clock win on warm sessions that
stream many small commands, no token-count change, no quality regression.

## Theoretical basis
For a command with raw token count `R` and a chosen tier ratio `r in {1.0,
0.15, ~0.05}`, the existing `select_level` already returns VERBOSE whenever
`R * 1.0 <= min(budget_cap, hard_cap)`. VERBOSE in most compressors is "full
list with all entries" which is structurally close to raw. So at the level-
selection layer Redcon already reaches the right decision. What it does *not*
skip is the parse + re-format round trip. For a compressor with parse cost
`C_p(R)` and format cost `C_f(R)` both linear in R, the wasted work per small
call is:

    W(R) = C_p(R) + C_f(R) - C_passthrough(R)

where `C_passthrough(R) = decode + estimate_tokens` (already roughly the floor).

Across a session of N calls drawn i.i.d. from a heavy-tailed output-size
distribution `f(R)`, expected savings is:

    E[saving] = N * integral_0^{R*} [C_p(R) + C_f(R) - C_passthrough(R)] f(R) dR

where `R* = min(budget_cap, hard_cap)`. Empirically `f(R)` for interactive
agent traffic is dominated by `R << R*` (most `git status` outputs are 30-200
tokens), but per-call CPU at those sizes is also tiny - sub-millisecond. The
integral collapses to a small constant.

In other words: the savings exist but are bounded above by `N * O(parser
constant)`, which on Python's regex engine is on the order of microseconds
to a low-ms range. Compare against the dominant cost in any of these calls,
which is the subprocess fork+exec (1-50 ms). The compression step is rarely
on the critical path.

## Concrete proposal for Redcon
Add a guard early in `redcon/cmd/pipeline.py::compress_command`, just before
`compressor.compress(...)` is dispatched. Compute `raw_tokens` once with the
cheap `_tokens_lite.estimate_tokens`. If `raw_tokens <= floor_cap` and
`hint.quality_floor in {VERBOSE, COMPACT}` (no need to satisfy ULTRA's must-
drop semantics), emit a `raw_passthrough` CompressedOutput at VERBOSE.

```python
# pipeline.py, after run_command() returns, before compressor.compress()
floor_cap = min(effective_hint.max_output_tokens,
                int(effective_hint.remaining_tokens * 0.30))
raw_text = run_result.stdout.decode("utf-8", errors="replace")
raw_tokens = estimate_tokens(raw_text)
if (
    compressor is not None
    and raw_tokens <= floor_cap
    and effective_hint.quality_floor != CompressionLevel.ULTRA
    and not run_result.stderr
):
    compressed = CompressedOutput(
        text=raw_text,
        level=CompressionLevel.VERBOSE,
        schema="raw_passthrough",
        original_tokens=raw_tokens,
        compressed_tokens=raw_tokens,
        must_preserve_ok=True,
        truncated=False,
        notes=run_result.notes + ("round_trip_bypass",),
    )
else:
    compressed = compressor.compress(run_result.stdout, run_result.stderr, ctx)
    compressed = _normalise_whitespace(compressed)
```

Optional: behind a `BudgetHint.round_trip_bypass: bool = False` flag so callers
can preserve the schema-typed output when they want it (e.g. MCP clients that
key on `schema == "git_status"` to render UI badges).

## Estimated impact
- Token reduction: 0 absolute pp. May *regress* tokens by a small amount on
  small inputs - the existing VERBOSE format is sometimes shorter than raw
  (drops empty lines, strips porcelain padding, normalizes branch headers).
  E.g. `git status` ULTRA goes from "## main\nM file" -> "branch:main M:1".
  Bypassing keeps the longer raw form. On the long tail of *very* small
  inputs (3-line git status, 200 tokens) this is < 10 tokens.
- Latency: warm-path savings on the order of 0.1-2 ms per call where the
  compressor would otherwise run regex. Cold path unchanged. Cache hits
  unaffected (already short-circuited above this point).
- Affects: only `pipeline.py`. No compressor changes. Cache behaviour
  unchanged. Quality harness unchanged (it tests compressors directly,
  bypassing the pipeline guard).

## Implementation cost
- ~20 lines in `pipeline.py`, plus 1 flag in `BudgetHint`, plus ~3 unit tests.
- No new runtime deps.
- Risks:
  - **Schema downgrade** - downstream code that switches on
    `report.output.schema == "git_status"` would break for small inputs.
    Mitigation: gate behind explicit flag (default off) or always preserve
    schema label even in passthrough.
  - Determinism preserved - `estimate_tokens` is deterministic, the threshold
    test is total-ordered.
  - Must-preserve preserved - raw text trivially preserves all patterns since
    those patterns are derived from raw structure.
  - Cache key unchanged.

## Disqualifiers / why this might be wrong
1. **Already mostly in place behaviourally.** `select_level` returns VERBOSE
   for any input that fits, and the VERBOSE formatter for most compressors is
   already a near-passthrough that just normalises a few fields. The actual
   CPU cost of `parse_status` + `_format_verbose` on a 3-entry `git status`
   is well under a millisecond. The "saving" is a fraction of a fraction of
   subprocess overhead, which itself is dwarfed by cache hits in steady-state
   sessions.
2. **Tokens can go up, not down.** The existing VERBOSE/COMPACT outputs do
   incidental cleanup (collapse blanks, normalise branch headers, drop
   `--porcelain=v1` artifacts). Bypassing yields *more* tokens for the same
   information on small inputs. Net loss.
3. **Schema label is load-bearing for MCP `_meta.redcon`.** Recent commit
   257343 standardised on `_meta.redcon.schema = "<compressor name>"` so
   agents and dashboards can dispatch on it. A surprise `raw_passthrough`
   schema for "small" calls breaks that contract; agents would need extra
   logic to merge two schema namespaces.
4. **Misses the real CPU cost.** The parser is not where pipeline time goes.
   `Popen` fork+exec, stdio drain, cache key hashing, and tokenizer counting
   each cost more than the regex pass on small inputs. Removing parse cost
   improves the fast path by single-digit percent at most, undetectable in
   normal use.
5. **Robustness regression surface.** The compressor's robustness invariants
   (binary garbage tolerance, mid-stream truncation handling, 5000-newline
   inputs) are only exercised when `compress()` runs. Bypassing on small
   inputs means hostile-but-small payloads skip the hardened path. Marginal
   risk in practice but real.

## Verdict
- Novelty: **low** - this is a micro-optimisation, and `select_level` already
  reaches the equivalent quality decision. The only delta is "skip parser
  CPU", and parser CPU is not the bottleneck.
- Feasibility: **high** - 20 lines of code, no new deps, easy to test.
- Estimated speed of prototype: **hours** (under half a day including tests
  and a microbenchmark to confirm or refute the savings claim).
- Recommend prototype: **no** (verify-or-skip per V84 brief). Existing tier
  selection plus the cache plus the log-pointer tier already cover the
  important cases. The remaining win is too small to justify a new flag and
  a schema-namespace divergence. If anything is worth doing here, it is
  measuring `parse_status` / `compress` CPU under a realistic session trace
  to confirm the bottleneck sits elsewhere; once that is logged, this vector
  can be retired with evidence.
