# V62: Advanced linter compressor with rule-frequency table + first-occurrence-only

## Hypothesis
Real-world lint output is heavily Zipfian: a small number of rules dominate
violation counts (E501, F401, no-any-return, RuboCop's
`Style/StringLiterals`, etc.). The current `lint_compressor` already prints a
top-8 per-code histogram, but at COMPACT it still emits **one line per file**
plus a count, and at VERBOSE it emits **one line per issue**. For a 200-issue
run where 80% are the same rule, this is wasteful: the 160 duplicate-rule
lines carry no marginal signal beyond `(file, line)`. The claim is that
emitting a full rule-frequency table plus exactly one canonical exemplar
per rule (with full message), followed by a compact `(path:line)` index of
the remainder, preserves every fact an agent needs to act ("which file, which
line, which rule") while collapsing the long tail of repeated messages.
Predicted COMPACT-tier reduction on a 200-violation Zipfian fixture:
~+20-30 absolute points beyond today's compact format.

## Theoretical basis
Treat the issue stream as a sequence of tuples `(rule, path, line, msg)`.
Empirically `msg` is a deterministic function of `rule` plus a small set of
slot values (line length, identifier name). For a fixed rule R with
multiplicity n_R, the cross-entropy of the message column conditioned on rule
is near zero: `H(msg | rule) << H(msg)`. So once the rule R is known, every
re-emission of `msg` is redundant under Shannon's source coding theorem.

Back-of-envelope. Assume Zipf exponent s=1 over K=10 rules and N=200
issues. Per-issue raw line ~= 18 tokens (path 5, line 2, code 2,
message 9). Today's COMPACT emits:
```
header (~10) + by-code line (~24) + per-file lines (30 files * ~6) ~= 215 tokens.
```
V62 COMPACT emits:
```
header (~10)
+ rule table:   K * (~8 tokens) = 80
+ exemplars:    K * (~18 tokens) = 180
+ tail index:   (N - K) * (~5 tokens for "path:line(R)") = ~950
```
Naive that's *worse*. The win comes from making the tail-index opt-in by tier:
- ULTRA: rule table only (~90 tokens vs 215 today vs 18*N=3600 raw) = 97.5% reduction.
- COMPACT: rule table + 1 exemplar + first-3-per-rule sample (~290 tokens) vs raw 3600 = 91.9%.
- VERBOSE: rule table + exemplar + full `(path:line)` index per rule (~1200 tokens) vs raw 3600 = 66.7% (today's verbose is ~0%).

The break-even point with today's COMPACT format is N >= ~80 issues with
top-rule share >= ~50%. Below that, fall through to today's format.

## Concrete proposal for Redcon
File: `redcon/cmd/compressors/lint_compressor.py` (extend, do not replace).

Add three new helpers and re-route `_format_compact` / `_format_ultra`:

```python
def _by_rule(issues) -> dict[str, list[LintIssue]]:
    g: dict[str, list[LintIssue]] = {}
    for i in issues:
        g.setdefault(i.code or "(no-code)", []).append(i)
    return g

def _is_zipfian(by_rule, total, k_top=3, share=0.5) -> bool:
    # cheap deterministic gate: top-k_top rules cover >= share of total
    top = sorted((len(v) for v in by_rule.values()), reverse=True)[:k_top]
    return total >= 80 and sum(top) / max(total, 1) >= share

def _format_rule_table(by_rule, level):
    lines = ["by rule:"]
    for code, items in sorted(by_rule.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        first = items[0]
        # one canonical exemplar per rule, dedented
        lines.append(f"  {code} x{len(items)}  e.g. {first.path}:L{first.line} {first.message}")
        if level == CompressionLevel.COMPACT and len(items) > 1:
            sample = items[1:4]  # up to 3 more
            lines.append("    also: " + ", ".join(f"{x.path}:L{x.line}" for x in sample))
            if len(items) > 4:
                lines.append(f"    +{len(items) - 4} more")
        elif level == CompressionLevel.VERBOSE:
            for x in items[1:]:
                lines.append(f"    {x.path}:L{x.line}")
    return "\n".join(lines)
```

Rewire `_format_compact` to detect Zipfian shape; if true, emit header +
rule table; otherwise fall through to existing format. Rewire
`_format_ultra` to optionally append the rule table when there's room
(top 5 codes only). VERBOSE gets the full per-rule path:line index
instead of the current "everything inline" dump - this is a *side*
improvement: today's verbose is barely compressed.

Update `must_preserve_patterns` so the rule codes that are present in the
output survive the gate. Today the harness preserves top-30 file paths;
add the dominant rule codes (top 8 by count) to the preservation set when
Zipfian path is taken, since the agent's lossless fact set shifts from
"which files" to "which rules".

Tools supported: today the regex covers `mypy` and `ruff`. The rule-table
form is tool-agnostic - it operates on `LintIssue.code`, which is already
populated for both. To extend to `eslint`/`rubocop`/`pylint`/`golangci-lint`
add corresponding line regexes (eslint: `path  line:col  severity  message  rule-id`;
rubocop: `path:line:col: SEVERITY: code: message`; pylint: `path:line:col: code: message (rule-name)`).
The frequency-table format applies unchanged.

## Estimated impact
- Token reduction (200-issue Zipfian, top rule = 80% share):
  - ULTRA: 97.5% (was ~94% on this shape).
  - COMPACT: ~85-92% (was ~40-60%). **+25-30 absolute points.**
  - VERBOSE: ~65% (was ~0-5%). **+60 points** (this is the surprise win).
- Token reduction (100-issue uniform across 50 rules, no Zipf):
  - falls through to existing format, no change. Gate avoids regression.
- Latency: parse cost identical. Format cost: one extra dict iteration
  over `by_rule`. <100 microseconds for N <= 5000. Cold-start: zero
  change (no new imports).
- Affects: `lint_compressor.py` only. Cache key unchanged. Quality
  harness gains stricter must-preserve set (rule codes), so existing
  fixtures may need a regenerated golden for the new compact form.

## Implementation cost
- ~80 lines (helpers + rewiring + new format paths).
- ~30 lines new tests in `tests/test_cmd_tier2.py` for the Zipfian gate
  and a fixture with N=200 issues, top rule 80% share.
- No new runtime deps. No network. No embeddings.
- Risks:
  - **Determinism**: tied-count rules need a stable tie-break (added:
    sort by `(-count, code)`). Same for ties within a rule's exemplar
    selection (use first issue in encounter order, which is already
    deterministic from input).
  - **Must-preserve regression**: today the harness preserves top-30
    *file paths*. New format emits files only inside the per-rule
    sample/index. For a Zipfian input with one rule covering all
    files, all files are still mentioned; for the worst case (1 file
    has 1 issue per 30 distinct rules) the rule table still names
    every file in its exemplar, so the preservation set is satisfied.
    Need a unit test for this corner case.
  - **Robustness**: `(no-code)` bucket handles mypy lines without a
    `[code]` suffix (these exist - syntax errors, plugin errors).
    Test with malformed inputs to confirm the bucket path doesn't
    explode.

## Disqualifiers / why this might be wrong
1. **The agent actually wants every occurrence.** When the task is
   "fix all E501", the agent needs every `(path, line)` to plan
   edits. Mitigation already wired in: COMPACT keeps a 3-sample plus
   the count, VERBOSE keeps the full index. But if the task router
   pins COMPACT, the agent will round-trip with `--verbose` or grep,
   wasting the savings. A "follow-up flag" hint in the output
   (`# 47 occurrences not shown - rerun with verbose for full list`)
   helps but is not a guarantee.
2. **Real lint output is less Zipfian than assumed.** On a pristine
   codebase the residual issues are often heterogeneous (one of each
   rule). The 80-issue / 50%-share gate is conservative but the
   curve might be wrong - need empirical distribution from a real
   monorepo run before locking thresholds.
3. **Already partly implemented.** The current `_format_compact`
   already emits an 8-rule histogram. V62 is an extension (frequency
   table with exemplar + tail index), not a new idea. Novelty is in
   the per-tier index granularity and the Zipfian gate, not the
   histogram itself. If reviewers count the histogram as
   "frequency table" the delta is smaller than claimed.
4. **Footer count loss.** The mypy/ruff `Found N errors` footer is
   parsed today but the V62 format must keep emitting it explicitly,
   else agents that grep `Found ... errors` (a common CI pattern)
   get a false "clean" signal. Easy fix; just listing it as a
   correctness obligation.
5. **`must_preserve` shape change** breaks downstream invariants if
   any caller is asserting "every file with an issue appears
   verbatim." The MCP tool `redcon_quality_check` returns
   `must_preserve_ok`; flipping the preservation contract from "files"
   to "rules" is a semantic change to that field even if the boolean
   still flips green.

## Verdict
- Novelty: medium (extension of existing histogram, but the per-tier
  index granularity and Zipfian gate are new).
- Feasibility: high (single file, no deps, deterministic, bounded
  blast radius).
- Estimated speed of prototype: 4-6 hours including the 200-issue
  fixture and new must-preserve corner-case tests.
- Recommend prototype: yes, conditional on first measuring the
  rule-share distribution on 3-4 real lint runs (one mypy on a
  large Python repo, one ruff on a typical project, one eslint on a
  JS repo) to confirm the Zipfian assumption holds before locking
  the gate threshold.
