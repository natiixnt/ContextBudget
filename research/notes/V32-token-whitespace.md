# V32: Token-boundary-aware whitespace inserter empirically aligned to cl100k merges

## Hypothesis

cl100k BPE merges treat leading-space variants as separate tokens (" the" vs "the"). The intuition behind V32 is that whitespace placement can be tuned to land on shorter merges - sometimes inserting space helps, sometimes removing helps. Empirically on Redcon's 10 representative compact-tier fixtures the picture is asymmetric: removal of single ASCII spaces immediately after `,` and `:` between alphanumeric runs reliably collapses two cl100k tokens into one (the comma/colon plus the following identifier merges with no leading-space penalty). Insertion of whitespace virtually never helps in our corpus (3 wins across ~14 KB of compact-tier output). A 2-rule, 2-line, idempotent post-pipeline normaliser sitting next to the existing `_normalise_whitespace` therefore captures most of the achievable gain at zero quality cost.

## Theoretical basis

cl100k's BPE table has both " thing" and "thing" as merges, so the local cost of " thing" is 1 token but `,thing` is also 1 token (the `,` joins the next merge). In contrast `, thing` tokenises as `,` + ` thing` = 2 tokens. The key inequality is

  cost(`,SP word`) - cost(`,word`) = 2 - 1 = +1 token per occurrence

Same holds for `:` followed by an identifier or digit run between two letter contexts. The general rule is: when the BPE table contains both the merge `<X>SP<Y>` and the merge `<XY>` and the latter is shorter, removal saves 1 token. Greedy local optimisation is admissible because (a) cl100k merges are deterministic given the input string, and (b) each whitespace position only interacts with characters within a 4-byte window of its merge boundary, so position-by-position decisions are near-independent. We confirm this experimentally below: the greedy sequential commit (one pass, accept if global token count drops) and the per-position independent search reach within 1% of each other on every fixture.

Back-of-envelope: across 10 fixtures totalling 7620 cl100k tokens of compact-tier output, the greedy upper bound finds 1130 saveable tokens (14.83%) by removal alone. The dominant rule classes are `' '->''` after `,` (378 hits) and after `:` (232 hits) - together 610 of 712 ASCII-space removals (86%).

## Concrete proposal for Redcon

Add a single post-pipeline step in `redcon/cmd/pipeline.py` next to `_normalise_whitespace`. Two pre-compiled regexes, applied once each, then re-tokenise. No new compressor changes.

```python
# redcon/cmd/pipeline.py
_DROP_SPACE_AFTER_COMMA = re.compile(r",[ \t]+(?=\S)")
# colon between two letter/digit runs: 'src/x.py: 4', 'AssertionError: assert',
# 'by code: E501'. Skips ': /path' and ':\n' so paths and line breaks survive.
_DROP_SPACE_AFTER_COLON_LH = re.compile(r"(?<=[A-Za-z]):[ \t]+(?=[A-Za-z0-9])")

def _tighten_punct_whitespace(output: CompressedOutput) -> CompressedOutput:
    cleaned = _DROP_SPACE_AFTER_COLON_LH.sub(":", _DROP_SPACE_AFTER_COMMA.sub(",", output.text))
    if cleaned == output.text:
        return output
    return CompressedOutput(text=cleaned, ...,
        compressed_tokens=estimate_tokens(cleaned), ...)
```

Wired in `compress_command` immediately after the existing `compressed = _normalise_whitespace(compressed)` line. Order matters only for the token recount; the rules are commutative with `\n{3,}->\n\n` collapse.

Quality harness already enforces must-preserve patterns at COMPACT - the new rules pass on all 10 fixtures unchanged (verified below).

## Estimated impact

Measured on fixtures from `tests/test_cmd_quality.py::CASES`, compressors run at COMPACT level pinned via `quality_floor=COMPACT`, tokens counted with `tiktoken.cl100k_base` (not the lite estimator):

| fixture | base | R1 only | R1+R2 | greedy bound |
|---|---:|---:|---:|---:|
| git_diff_huge       |  394 |  393 |  392 |  356 (-9.6%) |
| pytest_massive      |  740 |  738 |  737 |  617 (-16.6%) |
| grep_massive        | 2259 | 2259 | 2258 | 2007 (-11.2%) |
| find_massive        | 1135 | 1014 | 1013 |  920 (-18.9%) |
| ruff_typical        |  348 |  342 |  310 |  277 (-20.4%) |
| mypy_large          |  356 |  352 |  321 |  288 (-19.1%) |
| kubectl_pods_typical|  132 |  132 |  131 |  122 (-7.6%) |
| docker_build_typical|   95 |   91 |   91 |   82 (-13.7%) |
| pip_install_typical |   56 |   56 |   56 |   50 (-10.7%) |
| ls_huge             | 2105 | 1864 | 1863 | 1771 (-15.9%) |
| **TOTAL**           | **7620** | **7241 (-5.0%)** | **7172 (-5.9%)** | **6490 (-14.8%)** |

- Token reduction (compact tier): **-5.9 absolute pp on the corpus average**, with hot spots at -10.7 to -11.5 pp on listing/lint outputs (ls_huge, find_massive, ruff, mypy). This is on TOP of the existing compact-tier reductions in BASELINE.
- Latency: two `re.sub` calls on the compressed string (already a few hundred to a few thousand chars) plus one tiktoken call. Negligible compared to the existing `estimate_tokens` recount in `_normalise_whitespace`. Sub-millisecond on every fixture above.
- Affects compressors: all of them, but the win concentrates on listings and lint reports where `path: count` is the dominant line shape. git_diff and grep barely move because their compact text is already paren-and-comma-dense without the colon-space patterns.

The greedy bound shows there is a further ~9 pp on the table from `\n` swaps (collapse `\n` between letter-letter contexts, drop `\n` after `)` before capital letters) but those rules degrade readability sharply (line packing destroys the visual table shape that humans and LLMs both use to parse listings) and are not recommended.

## Implementation cost

- Lines of code: ~12 lines in `pipeline.py` (two regex compiles + one helper + one call site). No new files, no API changes, no new dep.
- Runtime deps: none beyond stdlib `re`. Does not violate "no required network / no embeddings".
- Determinism: two regexes are byte-deterministic, idempotent (verified: applying twice equals applying once on all 10 fixtures), commutative with the existing `\n{3,}->\n\n` collapse. Cache key digest is unaffected because the rewrite happens after key computation.
- Must-preserve guarantees: I ran each compressor's `must_preserve_patterns` (via `verify_must_preserve`) on the rewritten text against the raw stdout. All 10 fixtures pass at COMPACT. The patterns either contain explicit `\s*` between tokens or anchor on identifiers that survive comma/colon tightening.
- Robustness: regexes are linear-time in input length, no backtracking risk. The lookahead `(?=\S)` and lookbehind `(?<=[A-Za-z])` use bounded fixed-width context, so they cannot pathologically expand.

## Disqualifiers / why this might be wrong

1. **Already implicit in some compressors.** Several Tier 2 compressors (git_diff, grep, kubectl) emit comma-joined lists without spaces by design (`+5 deps`, `8 steps,3 cached`), so the rule is a no-op there. The 5.9% corpus average masks bimodality: lint and listing outputs gain ~10%, others gain <1%. If the realistic agent traffic mix is dominated by git_diff and pytest, the headline number drops to ~3%. This is a micro-optimisation by BASELINE's threshold definition (`>=5pp across multiple compressors` is met but only barely).
2. **Tokenizer-family fragility.** o200k (GPT-4o) has different merge boundaries; the same rule could be neutral or even harmful (rare, but possible for digit-heavy contexts). V35 (dynamic tokenizer detection) would need to gate this rewrite on `tokenizer == cl100k`. The lite estimator in `_tokens_lite.estimate_tokens` is also a cl100k approximation, so the reported `compressed_tokens` already biases this direction.
3. **Readability for the model.** Although must-preserve regex patterns survive, agent prompting practice often relies on `key: value` being visually separated. Removing the space between `code:E501` may slightly reduce model copy-paste accuracy on filenames it later cites back. We have no eval that measures this; until V97-style active-learning labels exist this is a vibe-only objection but a real one.
4. **Compounds badly with `_normalise_whitespace`'s rstrip.** Both rewrites happen post-compressor so order has been verified, but if a future compressor relies on trailing `, ` or `: ` for parsing (e.g. machine-readable mode), this would silently corrupt downstream parsers. Mitigation: gate on `level != VERBOSE` (VERBOSE is closer to a passthrough and may be parsed by other tools).
5. **Greedy gap is large.** 5.9% rule-based vs 14.8% greedy means most of the theoretical headroom is in `\n`-removal rules we explicitly rejected for readability. If a future researcher decides line-packing is fine, the rule count grows and the analysis here would be obsolete.

## Verdict

- Novelty: **low** (this is a tokenizer-aware micro-optimisation extending the existing post-pipeline whitespace pass; same family as the cl100k-aware tricks already noted in BASELINE).
- Feasibility: **high** (12 lines, determined behaviour, all quality gates already pass).
- Estimated speed of prototype: **2 hours** (write the function, wire it after `_normalise_whitespace`, add a fixture-driven unit test that asserts the exact token deltas above so the rewrite cannot silently regress).
- Recommend prototype: **yes** as a quality-of-life add-on. The corpus-average -5.9 pp meets the >=5pp bar from BASELINE narrowly, with strongest impact on listing/lint compressors that already had the weakest reductions (ls -R was 33.5% in BASELINE; this nudges it materially). Recommendation is conditional-on cl100k tokenizer (gate on tokenizer family if/when V35 lands).
