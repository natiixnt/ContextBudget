# V36: Cross-tokenizer Rosetta - format that tokenises identically across cl100k / o200k / (llama-3 dropped)

## Hypothesis

Rather than maintaining a per-tokenizer dispatch table (V35), there exists a single
"Rosetta" format - one set of formatting choices for compact-tier output - whose
worst-case token count across the major tokenizer families (cl100k, o200k, ...) is
within ~2 percentage points of the per-tokenizer-optimal dispatch winner. If true,
the project should ship V36 (one format, one code path) and skip V35 (N formats,
runtime detection, dispatch logic, larger test surface).

The implicit prediction: BPE tokenizers trained on overlapping web/code corpora
share enough merges that "things that bloat in any one tokenizer" is a small set,
and avoiding them once works for all of them.

## Theoretical basis

**Claim.** For two BPE encodings E_1, E_2 trained on similar corpora with
vocabularies V_1, V_2 and a shared byte-level base alphabet, define for any string
s the per-tokenizer length L_i(s) = |E_i(s)|. For a fixed semantic content with
candidate variants V = {v_1, ..., v_K}, the per-tokenizer-best-loss is

    L*_i = min_v L_i(v)        (V35: per-tokenizer dispatch)

and the Rosetta loss is

    L*_R = min_v max_i L_i(v)  (V36: single format)

By construction L*_R >= max_i L*_i. The relevant gap is

    Delta = L*_R - max_i L*_i >= 0.

**Bound.** When the optimal variants v*_i = argmin L_i agree across i (single dominant
choice), Delta = 0. When they disagree, Delta is bounded by the inter-tokenizer
length-disagreement on individual format primitives. Each format choice contributes
an additive cost difference d_ij(p) = L_i(p_a) - L_i(p_b) for variants a/b of
primitive p; tokenizer disagreement requires sign(d_1j) != sign(d_2j) for at least
one primitive. The number of such "sign-flip" primitives upper-bounds Delta in tokens.

**Empirical estimate.** In our pairwise primitive probe (22 candidate pairs,
sect. *Concrete proposal*), only 3/22 pairs had sign-flip disagreement between
cl100k and o200k - all involving compound-English-phrase casing (snake_case vs
camelCase vs hyphen-joined vs lowercase-spaced), each disagreement worth 1 token.
Across realistic compressor outputs (50-100 tokens) these average to <0.5pp.
Adding gpt2 (r50k) as a third, more divergent BPE family raises Delta to ~1-3pp on
**average** tokens, but **0pp on max-across-tokenizers** (V36's actual objective).

## Concrete proposal for Redcon

### Files affected
- `redcon/cmd/compressors/_format_primitives.py` (new, ~80 LOC) - shared format helpers.
- `redcon/cmd/compressors/git_diff.py`, `grep_compressor.py`, `pytest_compressor.py`,
  `lint_compressor.py`, `listing_compressor.py` (find/ls): replace ad-hoc separators
  with primitives from `_format_primitives.py`.
- `redcon/cmd/quality.py`: add three-tokenizer scoring as an opt-in fixture
  (no production behaviour change).

### Sketch (pseudo-code)

```python
# redcon/cmd/compressors/_format_primitives.py
# Pareto-optimal across cl100k, o200k, gpt2 r50k.
# Each constant chosen because the alternative bloats >= 1 tokenizer.

KV_SEP = " "          # not "|" (rule 2), not " - " (rule 7)
LIST_SEP = " "        # not "," in code-token contexts (rule 8)
COUNT_MARKER = " "    # not "x" prefix (rule 9). 'foo 6' beats 'foo x6'
PATH_SEP = "/"        # POSIX standard. dot-paths save 1 cl/o token but break parseability.
GROUP_HEADER = ": "   # 'dir/: a b c' beats 'dir/{a,b,c}.py' (rule 5)
LINE_REF = "@"        # 'foo.py @42' ties cl/o, beats ',' on dense ranges
KEYWORD_FAIL = "FAIL"   # not "FAILED" (rule 3)
KEYWORD_PASS = "PASS"   # not "PASSED"
PHRASE_JOIN = " "       # 'no newline at eof' beats snake/dash/camel for 3+ words (rule 6)

def fmt_filecount(path, plus, minus):  # diff
    return f"{path}{KV_SEP}+{plus} -{minus}"

def fmt_grep_hit(path, lines):
    return f"{path} " + " ".join(f"{LINE_REF}{n}" for n in lines)

def fmt_lint_rule(code, name_phrase, count):
    # name_phrase is already lower-spaced (see normalize_rule_phrase)
    return f"{code} {name_phrase} {count}"
```

### Migration

1. Land `_format_primitives.py`.
2. Per compressor: replace `|`, ` - ` separator strings, `xN` count markers,
   `{a,b,c}` brace-globs with primitive constants. Existing must-preserve patterns
   continue to match on the path and counts (whitespace-tolerant). Determinism and
   robustness invariants preserved (no new regex; pure constant swaps).
3. `quality.py` gains optional `_score_three_tokenizers(text)` reporting cl100k,
   o200k, r50k counts in the quality report; gated behind `RDC_TRI_TOKEN=1` env so
   default test runs do not depend on r50k vocab availability.

## Estimated impact

### Token reduction (compact tier)

Measured on 5 representative compressor outputs, 4-6 variants each, scored on
cl100k_base, o200k_base, r50k_base (gpt2). Key numbers:

| Case      | V35 avg-tok | V36 avg-tok | V36 vs V35 (avg) | V35 max-tok | V36 max-tok | V36 vs V35 (max) |
|-----------|-------------|-------------|------------------|-------------|-------------|------------------|
| git_diff  | 54.33       | 56.33       | +3.68%           | 66          | 66          | +0.00%           |
| pytest    | 50.00       | 51.33       | +2.67%           | 58          | 58          | +0.00%           |
| grep      | 56.00       | 56.00       | +0.00%           | 66          | 66          | +0.00%           |
| find      | 77.33       | 77.33       | +0.00%           | 92          | 92          | +0.00%           |
| lint      | 34.00       | 34.00       | +0.00%           | 35          | 35          | +0.00%           |
| **TOTAL** | **271.67**  | **275.00**  | **+1.23%**       | **317**     | **317**     | **+0.00%**       |

**With only cl100k + o200k** (the realistic deployment surface; gpt2 is dead
weight for current LLMs): V36 vs V35 loss is **+0.00% on both avg and max** -
they pick the same variant in every single one of the 5 cases.

Comparing each case to its naive default (the verbose-friendly format common in
codebases):

| Case      | default-max | rosetta-max | save vs default |
|-----------|-------------|-------------|-----------------|
| git_diff  | 53          | 51          | +3.8%           |
| pytest    | 56          | 47          | +16.1%          |
| grep      | 56          | 52          | +7.1%           |
| find      | 95          | 71          | +25.3%          |
| lint      | 61          | 57          | +6.6%           |

So Rosetta **as a format-discipline** wins ~6-25% over a verbose default, while
matching V35 dispatch.

### Latency
- Cold start: 0 effect (no new imports, no new runtime path).
- Warm parse: 0 measurable effect; primitive constants compile to identical
  string concat patterns to the current per-compressor formats.

### Affected layers
- All compressors that currently emit human-friendly separators.
- Quality harness (additive only).
- No cache-key change; no MCP schema change; no scorer change.

## Implementation cost

- ~80 LOC for `_format_primitives.py`.
- ~150 LOC of search/replace across the 5 affected compressors (mechanical).
- ~40 LOC opt-in tri-tokenizer score in `quality.py`.
- Total: ~270 LOC.
- New runtime deps: none. r50k vocab ships with tiktoken already; the tri-tokenizer
  fixture is opt-in for tests only.
- Risk to determinism: zero (constants only).
- Risk to must-preserve: low. Patterns must be re-verified to ensure they tolerate
  ` ` where they previously matched `|` or `,`. `quality.py` already runs at all 3
  tiers, so the harness will catch any regression.
- Risk to robustness: zero (no new parsing).

## Disqualifiers / why this might be wrong

1. **Llama-3 was dropped, not measured.** Methodology asks for cl100k / o200k /
   llama-3 but neither huggingface tokenizers nor sentencepiece is available in
   the project venv. We substituted gpt2 r50k_base as a third, more BPE-divergent
   reference. If llama-3 (BPE-byte-level, 128k vocab, trained on a different
   corpus mix) disagrees more strongly on the snake_case/camelCase axis, V36
   loss could exceed 2pp average. Mitigation: the worst-case max metric was 0pp
   even with gpt2 included; sign-flip-bounded Delta argument suggests llama-3
   agrees with cl100k more than gpt2 does (newer corpus, similar code-heavy training).
2. **V35 may save more than measured on rare/long outputs.** Our 5 cases are
   short (35-95 token max). On a 1000-token compact-tier output, even 2pp matters.
   But for compressed output, by construction we are not at 1000 tokens - the
   compressor already cut us there.
3. **Only 5 cases.** Five compressors x 4-6 variants is 26 datapoints; a
   different set of compressors (sql EXPLAIN, k8s events, profiler output) might
   have stronger tokenizer-specific bloat patterns. The framework here generalises
   - the rules in `_format_primitives.py` would absorb new findings - but we
   should not claim the +1.23% number for compressors we did not test.
4. **"Already in baseline".** BASELINE.md says: *"indented continuation lines drop
   the 3-space prefix (saves ~1 token/line on cl100k); _normalise_whitespace
   collapses 3+ newlines"*. These are cl100k-specific rewrites - i.e. **V35 is
   partially shipped already**. V36's contribution is the *codification* of those
   choices into a tokenizer-agnostic rule set, plus the empirical justification
   for not adding tokenizer-detection logic.
5. **Path encoding rule has correctness side-effects.** `dot_paths` (rule 1) saves
   2 tokens per path on cl/o but breaks `:line` line-ref parsing and the
   robustness fixtures that grep `src/foo.py` literally. We explicitly rejected
   this rule in the proposed primitives table; readers might reasonably push back
   that the "Rosetta wins by 25.3%" find-case result was achieved by switching
   *within* a non-controversial primitive (brace-glob -> `dir/: ...` group), not
   by aggressive path rewriting. So the win is robust to the safety constraint.

## Verdict

- **Novelty: low.** The idea is "share a format across tokenizers" - obvious in
  hindsight, and partially shipped (cl100k-specific rewrites already exist). The
  contribution is the *measurement* showing V35 has near-zero advantage over V36.
- **Feasibility: high.** ~270 LOC, no new deps, no determinism risk, opt-in
  tri-tokenizer fixture.
- **Estimated speed of prototype: hours** (the experimental code in
  `/tmp/v36_*.py` already validates the primitives; turning them into the file is
  mechanical).
- **Recommend prototype: yes** - but as a refactor that *prevents future V35*,
  not as a token-reduction win. The token reduction is already captured by
  shipped cl100k-aware rewrites; V36 is the engineering hygiene that says
  "don't add a per-tokenizer dispatch path; the data shows it would gain you
  <2pp on average and 0pp on worst-case for ~3x the code-path complexity."

### Direct answer to the comparison question

> Comparison: V35 (per-tokenizer dispatch) wins on average. V36 (Rosetta) wins
> on simplicity. Compare expected loss at each.

| Metric                                   | V35 (dispatch) | V36 (Rosetta) | Loss   |
|------------------------------------------|----------------|---------------|--------|
| Avg tokens (cl100k+o200k only)           | 250.5          | 250.5         | 0.000% |
| Max tokens (cl100k+o200k only)           | 254            | 254           | 0.000% |
| Avg tokens (cl100k+o200k+gpt2 r50k)      | 271.67         | 275.00        | +1.23% |
| Max tokens (cl100k+o200k+gpt2 r50k)      | 317            | 317           | 0.000% |

> Conclusion: is the simpler V36 only-2pp worse than V35? Then ship V36 and skip V35.

**Yes.** Even with a deliberately more-divergent third tokenizer (gpt2 r50k)
included, V36's expected loss is +1.23% on average tokens and 0.00% on the
worst-case max. Restricted to the realistic deployment surface (cl100k + o200k),
V35 has **literally zero** token advantage in our 5 cases - it picks the same
variant for both tokenizers every time. **Ship V36, skip V35.**

### Pareto-optimal Rosetta rules (the actual deliverable)

Codified from the per-primitive probe; each rule has both alternatives measured
on cl/o/gpt2 and the worse alternative bloats at least one tokenizer:

1. **Path separator: keep `/`.** Dot-paths save 1-2 cl/o tokens but tie on gpt2
   and break parseability. Not worth it.
2. **Avoid `|` separators.** `path | n` is 7 gpt2 tokens; `path n` is 6.
   cl/o neutral. Always use space.
3. **No-punct keywords beat colon-arrow.** `FAIL path msg` (5/5/8) beats
   `FAILED path - msg` (6/6/10) on all three.
4. **Line refs: `@N` matches `:N` exactly across all three** (use `@` because it
   parses as a single non-path-eating token with no compressor regex collisions).
5. **Group header `dir/: a b c`** beats `dir/{a,b}.py` brace-glob (5/6/7 vs
   8/9/12).
6. **Multi-word phrases: lowercase-with-space beats all alternatives.**
   `line too long` (3/3/3) ties or beats `line_too_long` (4/4/5),
   `line-too-long` (3/3/5), `lineTooLong` (3/3/3). Lowercase-space is Pareto-optimal.
7. **No arrow `->` for KV.** `path -> v` (8/8/10) loses to `path v` (7/7/9).
8. **CSV `,` is NOT universally cheaper.** For lint-rule-style content,
   `E501 line too long 6` (7/7/6) beats `E501,line-too-long,6` (7/7/10).
9. **Count marker: bare digit beats `xN`.** `path 6` beats `path x6` by 1 gpt2 token,
   ties cl/o.

These rules are mechanical to apply, do not require runtime tokenizer detection,
and survive the V36 evaluation framework with worst-case loss = 0% vs the per-tokenizer
optimal dispatch.
