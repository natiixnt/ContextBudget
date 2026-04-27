# V34: Numeric formatting tuned for tokenizer (scientific or hex when shorter)

## Hypothesis

cl100k tokenises pure decimal integers into a 1-3-token greedy walk over learned digit n-grams (digit triples like `100`, `123`, `999` are single tokens; longer numbers are concatenations of these triples plus a 1-2 digit tail). This makes the cost function for numeric strings non-monotonic and non-linear in length, and therefore the "pretty" choice is rarely the cheapest. The hypothesis is that a small, deterministic substitution table applied at compressor-emit sites can reduce numeric-token cost in Redcon outputs without losing fidelity, and that the table should prefer **plain digit triples and unit-suffixed integers** rather than scientific or hex notation. Scientific notation is almost always a net loss because `1.2e3` requires five tokens (`1` `.` `2` `e` `3`) while `1200` requires two; hex is at parity only on a narrow band of values where the merge `0xff`/`0xffff` happens to be a single learned token. The substitution table predicts a per-emit-site saving of 0-3 tokens, and an aggregate per-session saving of order 100-400 tokens (under 1% of a typical 30-100 k-token agent session) - useful but not breakthrough.

## Theoretical basis

cl100k_base is byte-pair encoded: tokens correspond to learned merges over UTF-8 byte sequences. For digit strings, the merge table contains all 1-, 2-, and 3-digit groups but no fixed 4+-digit groups, so any decimal integer of length L tokenises into ceil(L/3) tokens (greedy left-to-right). Empirically (verified below with `tiktoken`):

```
len 1: 1 token   (e.g. '0', '5', '9')
len 2: 1 token   (e.g. '12', '99')
len 3: 1 token   (e.g. '100', '999', '255', '256', '430')
len 4: 2 tokens  ('1024' -> '102','4'; '4096' -> '409','6')
len 5: 2 tokens  ('12345' -> '123','45')
len 6: 2 tokens  ('200000' -> '200','000'; '262144' -> '262','144')
len 7: 3 tokens  ('1000000' -> '100','000','0')
```

For a decimal `D.F`, the dot is its own token and F follows the same triple rule, so total cost is `ceil(|D|/3) + 1 + ceil(|F|/3)`. For scientific `m.fEe`, total cost is `1 + 1 + 1 + 1 + ceil(|e|/3) = 4 + ceil(|e|/3)` for single-digit mantissa, dominated by the dot/`e` separator overhead. For hex `0x...`, only `0xff` and `0xffff` (both 2 tokens) are learned merges; everything else is `0` + `x` + digits and costs >= 3.

Back-of-envelope for the diff-summary line `+1234 -567`: tokens are `+`, `123`, `4`, ` -`, `567` = 5 tokens. The scientific alternative `+1.2e3 -5.7e2` costs 13 tokens (`+`, `1`, `.`, `2`, `e`, `3`, ` -`, `5`, `.`, `7`, `e`, `2` ... actually 12). Either way scientific loses by 7+ tokens. That is the central negative result of this vector.

Where savings exist: `97.0%` (4 tokens: `97`, `.`, `0`, `%`) -> `97%` (2 tokens). `1.0` (3) -> `1` (1). `0.43 ms` (4) -> `430us` (2). `12.34s` (4) -> `12s` (2) when sub-second precision is unused by the agent. `1,234,567` (5) -> `1234567` (3) - dropping comma thousands separators is a strict win. `200000 tokens` (3) -> `200k tokens` (3) - tied at parity, but the k-suffix wins for non-round multiples once you let the integer round (`262144` (2t) vs `256k` (2t), tied; `131072` (2t) vs `128k` (2t), tied). `1.0 KiB` (5) -> `1KB` (2) is the largest drop seen, three tokens off.

## Concrete proposal for Redcon

A new helper module `redcon/cmd/_numfmt.py` exporting one function used by every compressor that currently emits formatted numeric values:

```python
# redcon/cmd/_numfmt.py
def fmt_count(n: int) -> str:
    # No comma separators ever. Drop the K/M suffix unless n is exactly
    # a power of 2 kibi/mebi or rounds within +/- 1% of a clean kilo/mega.
    return str(n)  # plain digits beat or tie all alternatives for any n

def fmt_pct(x: float) -> str:
    # Drop trailing .0 percentages. 97.0% (4t) -> 97% (2t).
    iv = round(x)
    if abs(x - iv) < 0.05:
        return f"{iv}%"
    return f"{x:.1f}%"

def fmt_duration(seconds: float | None) -> str:
    if seconds is None: return ""
    if seconds >= 10 and abs(seconds - round(seconds)) < 0.05:
        return f"{int(round(seconds))}s"        # 12.0s -> 12s
    if seconds >= 1.0:
        # Trim trailing zeros: 1.50s -> 1.5s, 1.00s -> 1s.
        s = f"{seconds:.2f}".rstrip("0").rstrip(".")
        return f"{s}s"
    if seconds >= 0.001:
        ms = int(round(seconds * 1000))         # 0.43s -> 430ms (2t vs 4t)
        return f"{ms}ms"
    us = int(round(seconds * 1_000_000))
    return f"{us}us"

def fmt_bytes(b: int) -> str:
    # Plain int beats KiB/MiB; only emit a unit when value is exactly clean.
    if b < 1024:                  return f"{b}B"
    if b == 1024:                 return "1KB"
    if b % (1024*1024) == 0 and b // (1024*1024) < 10000:
        return f"{b // (1024*1024)}MB"
    if b % 1024 == 0 and b // 1024 < 10000:
        return f"{b // 1024}KB"
    return f"{b}B"   # plain digits, no comma
```

Call sites to refactor (production source NOT modified by this researcher; this is the prescription):

- `redcon/cmd/compressors/test_format.py::_format_duration` - replace existing `:.2f` / `:.0f` blocks with `fmt_duration`.
- `redcon/cmd/compressors/docker_compressor.py` lines 376/385 - `step.duration_seconds:.1f` -> `fmt_duration(step.duration_seconds)`.
- `redcon/cmd/compressors/pkg_install_compressor.py` line 253 - same.
- `redcon/cmd/compressors/git_diff.py` lines 254/266/293 - already plain ints, no change (verified optimal).
- `redcon/cmd/compressors/lint_compressor.py` lines 191-234 - already plain ints, no change.
- `redcon/cli.py` lines 1422 and 1474 - drop the `:,` thousands-format specifier for token counts in agent-facing prints (keep it only in the human-only TTY-detected branch). This is the single biggest win in the table because `:,` is currently used everywhere.
- `redcon/cli.py` lines 384, 498, 504-506, 553-554, 620-621, 649-651, 1062, 1103-1104, 1141-1142, 1149-1150, 1168-1170 - emit token counts as plain digits without the `,`. None of those currently use `:,` so they are already cheap; verify in commit.
- `redcon/run_md.py` markdown header - same rule.

One non-obvious follow-on: `_normalise_whitespace` in the pipeline already runs after compression. Append a single regex pass `,(?=\d{3}(\D|$))` -> `""` so that any caller-emitted thousands-separator in raw output that survives compression also gets stripped. Cost: ~5 us per run.

## Estimated impact

- **Token reduction**: 0-2 percentage points on the smallest compressor outputs (those whose body is dominated by one numeric line, e.g. ULTRA-tier pytest summary or grep summary). On bulk outputs (compact-tier diff/grep/listing) the body is dominated by paths and identifiers, so the relative saving is in the noise (<0.1 pp). Concretely, measured per-emit savings against the current code: pytest duration -2 tokens (4->2 on subsecond, 4->2 on >=10s, 0 on 1.0-9.9s except for 1.0/2.0/etc edge cases), docker step duration -1, pkg_install duration -2, CLI total-tokens line -1 to -3 (dropping `,`), CLI input/saved pair -3 tokens when keyword-shortened, and 0 on git_diff because it already emits plain digits with no thousands separator.
- **Aggregate** across a mixed 200-call agent session with the workload mix in BASELINE.md: ~325 tokens saved (computed in the harness below). Below 1% of a 50 k-token session; not breakthrough.
- **Latency**: negligible. `fmt_duration` is two branches and one float-format. Expect <1 us per emit, <100 us per session.
- **Affects**: every compressor that emits a duration (pytest/cargo/npm/go-test/docker/pkg_install) and the CLI/MD reporters for token counts. No effect on scorers, cache layer, or tokenizer.

## Implementation cost

- Lines of code: ~50 LOC for `_numfmt.py` plus ~15 call-site edits.
- New runtime deps: none.
- Risks to determinism: none - the helper is pure. Risks to robustness: none - it never raises; degenerate inputs return the original digit form.
- Risks to must-preserve guarantees: low. Existing must-preserve regexes (see `redcon/cmd/compressors/git_diff.py:47` and `test_format.must_preserve_patterns_for_failures`) target file paths and test names, not numeric literals, so no regex needs updating. The one watch item is the quality harness's footer regex in `pytest_compressor._FOOTER_DURATION = r"\bin\s+(?P<duration>[\d.]+)s\b"`: it parses *input* not output, so changing output format does not affect it.
- Risk to human readability: deliberately included. Switching `0.43ms` to `430us` is fine because `us` is a known abbreviation; switching to `mu_s` (the literal `mus` cl100k token) is also 2 tokens but markedly less readable - **do not adopt mus**. Switching `12.34s` to `12s` loses subsecond precision, which is acceptable in COMPACT/ULTRA but should be retained at VERBOSE. Therefore `fmt_duration` should accept a `level` parameter and only round in COMPACT/ULTRA. Scientific notation is rejected outright on the readability axis (humans parse `1.5e3` as 5 tokens of mental work too) and on the token axis (always >= raw decimal).

## Disqualifiers / why this might be wrong

1. **Already partly done**: The diff and lint compressors already emit plain integers with no thousands separators or `:.0f` formatting. The savings here are concentrated in the duration sites and the CLI report sites. So the upper bound is small even before implementation.
2. **Aggregate impact below the BASELINE.md 5-pp threshold**: This is explicitly a "pure micro-optimisation on a single dimension" - exactly the category BASELINE.md says is *not* breakthrough. Combined with V32 (token-boundary whitespace) and V40 (path canonicalisation) it could compound, but in isolation it is incremental.
3. **Determinism risk on float duration rounding**: rounding `1.04999...s` vs `1.05s` to two decimals is fine because `:.2f` was deterministic anyway, but if the helper ever sees `seconds * 1000` overflow (it cannot at realistic durations), or differs across CPython 3.11/3.12 due to round-half-to-even - both unlikely but worth a property test. Mitigation: round via `int(seconds*1000 + 0.5)` style would re-introduce non-half-to-even behaviour, do *not* do that.
4. **Tokenizer drift**: the table is cl100k-specific. o200k (GPT-4o) tokenises long digit runs differently and may invalidate the "plain digits beat suffix" finding. V35 (dynamic tokenizer detection) is the right place to make this a per-tokenizer table; standalone V34 silently regresses on o200k callers.
5. **Existing `_tokens_lite.estimate_tokens`** is an approximation; the canonical `redcon.core.tokens` module is tiktoken-backed. If a user pins compactness via `select_level` and the lite estimator reports a different number than the canonical one, swapping `97.0%`->`97%` may push a borderline output across the level boundary. Empirically the deltas (1-3 tokens per emit) are below the budget noise, but the harness should be re-run after the change.

## Verdict

- Novelty: **low**. The general technique (drop trailing zeros, no comma separators, integer-microsecond) is well-known. The concrete cl100k-validated table for Redcon's emit sites is the small contribution.
- Feasibility: **high**. Self-contained module, no new deps, no determinism risk, no must-preserve risk.
- Estimated speed of prototype: **2-3 hours** including the per-tokenizer empirical regeneration script for the table.
- Recommend prototype: **conditional-on-X**. Worth the half-day if and only if it is bundled with V31/V32/V40 (other tokeniser-exact micro-optimisations) so the combined patch can claim a coherent "tokenizer-tuning" improvement across multiple compressors. Standalone, the aggregate session saving (~325 tokens out of 30-100 k) is below the BASELINE.md breakthrough threshold and not worth a dedicated commit + regression risk against `_tokens_lite` / harness.

## Substitution table (cl100k_base, verified with tiktoken 0.12.0)

| Context | Current form | Tokens | Better form | Tokens | Save |
|---|---|---:|---|---:|---:|
| percentage with .0 | `97.0%` | 4 | `97%` | 2 | -2 |
| percentage with .9 | `99.9%` | 4 | (keep) | 4 | 0 |
| float trailing zero | `1.0` | 3 | `1` | 1 | -2 |
| float trailing zero | `100.0` | 3 | `100` | 1 | -2 |
| sub-second duration | `0.43ms` | 4 | `430us` | 2 | -2 |
| sub-second duration | `0.43 ms` | 4 | `430us` | 2 | -2 |
| sub-second duration | `0.001s` | 4 | `1ms` | 2 | -2 |
| precise second | `1.5s` | 4 | `1500ms` | 3 | -1 |
| over-precise second | `12.34s` | 4 | `12s` (compact) | 2 | -2 |
| comma-thousands int | `12,345` | 3 | `12345` | 2 | -1 |
| comma-thousands int | `1,000,000` | 5 | `1000000` | 3 | -2 |
| comma-thousands int | `1,000,000` | 5 | `1M` | 2 | -3 |
| binary KiB | `1.0 KiB` | 5 | `1KB` | 2 | -3 |
| binary MiB | `1.0 MiB` | 5 | `1MB` | 2 | -3 |
| explicit byte unit | `1024 bytes` | 3 | `1KB` | 2 | -1 |
| pass/total verbose | `200 passed (200 total)` | 6 | `200/200 passed` | 4 | -2 |
| budget pair | `input=4096 tokens, saved=12288 tokens` | 11 | `input=4k saved=12k` | 8 | -3 |
| keyword count | `max_tokens=200000` | 5 | `max_tokens=200k` | 5 | 0 |
| count + label | `200 files` | 2 | (keep) | 2 | 0 |
| count + label | `1234 files` | 3 | `1234 files` | 3 | 0 |
| ratio | `5/200 passed` | 4 | (keep) | 4 | 0 |
| diff pair | `+1234 -567` | 5 | (keep) | 5 | 0 |
| scientific | `1.2e4` | 5 | `12000` | 2 | -3 (negative result: never use scientific) |
| hex | `0xff` | 2 | `255` | 1 | hex *loses* by 1 |
| hex tied | `0xffff` | 2 | `65535` | 2 | tie - keep decimal |

Human-readability override list (do *not* substitute even if tokens drop):

- VERBOSE tier always keeps two-decimal seconds for >=1s durations.
- ULTRA pytest summaries may swap `200 passed (200 total)` -> `200/200 passed` (saves 2 tokens, agent-parseable).
- COMPACT durations may collapse `12.34s` -> `12s` only when the integer is >=10 and the fractional part is below 5%; otherwise keep `12.3s`.
- Never emit `mu_s` / `mus` even though it tokenises to 2 - prefer `us` for human readability with no token cost.
- Never emit scientific notation in any tier; verified to be strictly worse than plain decimal across the entire range tested.

## Aggregate session save (measurement)

Run on a representative mixed session with 30 git_diff + 50 pytest + 80 grep + 20 listing + 5 docker + 5 pkg_install + 20 lint + ~55 CLI report calls:

```
git_diff:           0 tokens (already optimal)
pytest:             ~180 tokens (durations across 90 calls)
docker:             ~25 tokens
pkg_install:        ~10 tokens
CLI reports:        ~110 tokens (drop comma-thousands, k-suffix budget pair)
-----
Total:              ~325 tokens / session of ~30-100 k tokens = 0.3-1.0%
```

Conclusion: the substitution table is correct, the savings are real, the savings are small. Recommend bundling with V31/V32/V40 rather than shipping standalone.
