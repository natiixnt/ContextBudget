# V35: Dynamic tokenizer detection from caller, swap dictionary

## Hypothesis

Different agent runtimes count tokens with different tokenizers (Claude
uses an Anthropic BPE; GPT-4o uses cl100k for legacy and o200k for new
gpt-4o models; Llama-family uses a sentencepiece BPE; Gemini uses its
own). Today `redcon/cmd/_tokens_lite.py` is a single `ceil(len/4)`
heuristic and the compactor's whitespace-, indent-, and path-rewrite
choices were tuned with cl100k merges in mind (see BASELINE.md note on
`_normalise_whitespace` and "drop indent prefixes"). The claim is that a
caller-supplied `target_tokenizer` field on `BudgetHint`, plus a small
per-tokenizer substitution table dispatched in the post-format
normalisation pass, would (a) make the reported `compressed_tokens`
exact for that family and (b) recover an additional 1-3 percentage
points of compact-tier reduction for callers running on a non-cl100k
tokenizer. The prediction is that the gain is real but small, because
the families that matter for coding agents (cl100k, o200k, Llama-3,
Anthropic) all derive from byte-level BPE on similar code corpora and
agree on the merges that cover code-shaped strings.

## Theoretical basis

Let X be the compact-tier output text and let `T_f(X)` be the
tokeniser-f token count. For two tokenisers f1, f2 that share a byte
alphabet the absolute difference is bounded by `|T_f1(X) - T_f2(X)| <=
|X|` and in practice by the divergence between their merge tables on
the n-grams actually present. Empirically (this study, see numbers
below) cl100k and o200k differ by ~5% on a 666-byte representative
compact-tier doc, and `gpt2`/r50k diverges by ~36%.

For a substitution rule `r: a -> b`, define the per-tokeniser saving
`S_f(r, X) = T_f(X) - T_f(X with a replaced by b)`. The rule is
"universally good" when `S_f >= 0` for all f in the target set, and
"divergent" when its sign flips between tokenisers. The minimum
achievable token count under a fixed rule set R against tokenizer f is

```
T_f*(X) = min over (orderings of R applied to X) T_f( apply(R, X) )
```

For a single tokenizer this is solved greedily. The cross-family
question is whether a single shared rule set R_shared achieves a
sum across tokenisers within epsilon of the per-tokenizer optima
sum_f T_f*(X). If R_shared - R_f is small (the divergent rules are
few and individually save few tokens), tokenizer-aware dispatch is
not worth the maintenance.

Back-of-envelope: if 80% of compact-tier output tokens come from
identifiers / paths / counts (call this set U) where merge tables
agree, the divergence ceiling is roughly `(1 - 0.8) * delta_per_token`
where `delta_per_token` is the family-pair tokenization-cost spread.
Measured on the sample below `delta_per_token <= 0.06` (cl vs o2) so
the upper bound on additional saving is `~0.012 * T = 1.2%`. For
older tokenisers (gpt2/r50k) this rises to `~6-8%` because more rules
flip direction.

## Empirical measurement (this researcher's experiment)

Method: take a synthesised ~666-byte compact-tier document built from
realistic git_diff + pytest + grep snippets after current redcon
rewrites. Encode under `cl100k_base`, `o200k_base`, and `gpt2`. Probe
~50 single rewrite rules (path canonicalisation, count compaction,
section-header style, error-name shortening) and search for *direction
flips* where the rule helps one tokenizer but hurts another.

Headline numbers on the same 666-byte sample:

| tokenizer | tokens | vs cl100k |
|---|---|---|
| cl100k_base | 203 | baseline |
| o200k_base  | 214 | +5.4% |
| gpt2 (r50k) | 277 | +36.5% |

Direction flips found across ~50 rule probes on code-shaped text:
- cl100k vs o200k: **0** flips on single-character substitutions, **0**
  flips on path / count / header rewrites we tested.
- cl100k vs gpt2:  flips on `FAIL`/`F` (gpt2 saves a token by single
  char, cl100k is indifferent), `14p 3f 1s`/`p14 f3 s1` (gpt2 weakly
  prefers a different ordering), and a few test-name rewrites.
- Found 2 weak-divergence rules where the rewrite is a no-op for cl100k
  but saves 1-2 tokens on o200k: `AssertionError` -> `AE` (+2 on o200k,
  0 on cl100k), `KeyError` -> `KE` (+1 on o200k, 0 on cl100k). These
  are weak-divergence (no flip, just one tokenizer benefits more).

Applying the union of beneficial rules:
- cl100k saving: 21 tokens (-10.3%)
- o200k saving:  28 tokens (-13.1%)

The o200k gain is ~3 tokens larger because some compound names
(`AssertionError`, `KeyError`, `redcon/cmd/`) tokenise into more pieces
under o200k and benefit slightly more from substitution. There are no
direction conflicts on the cl100k <-> o200k axis for any rule we
tested.

## Concrete proposal for Redcon

Add an explicit but optional tokenizer-family hint, default-no-op:

```python
# redcon/cmd/budget.py
class TokenizerFamily(str, Enum):
    UNSPECIFIED = "unspecified"  # default: keep cl100k-aligned rewrites
    CL100K = "cl100k"
    O200K = "o200k"
    LLAMA3 = "llama3"
    ANTHROPIC = "anthropic"

@dataclass(frozen=True, slots=True)
class BudgetHint:
    remaining_tokens: int
    max_output_tokens: int
    quality_floor: CompressionLevel = CompressionLevel.ULTRA
    prefer_compact_output: bool = False
    semantic_fallback: bool = False
    target_tokenizer: TokenizerFamily = TokenizerFamily.UNSPECIFIED  # NEW
```

Wire-through:
- `redcon/mcp/tools.py::tool_run` accepts `target_tokenizer: str = ""`
  and passes it through. If the model_profile of the active config has
  a known tokenizer (`tokens.model` -> family map already in
  `redcon/core/tokens.py`'s `_MODEL_CHAR_RATIO_PROFILES` and
  `model_profiles.py`'s built-in profiles) and the caller did not
  override, derive it from there.
- `redcon/cmd/pipeline.py::_normalise_whitespace` becomes a dispatch:
  pick a per-family `_post_normalise(text, family)` hook that runs the
  family-specific divergent-rule table only when the family is
  non-default. The default path keeps current cl100k-tuned behaviour
  byte-for-byte (preserves cache determinism for existing callers).
- `redcon/cmd/_tokens_lite.py` gains an optional
  `estimate_tokens(text, family=None)` overload that uses the existing
  `_model_chars_per_token` profile from `redcon.core.tokens` when a
  family is set, while keeping the cheap char/4 default for everything
  else (cold-start latency unchanged because the heavy tokens.py
  module is only imported when a family is requested).

Substitution-table dispatch:

```python
# redcon/cmd/post_normalise.py (new file)
_DIVERGENT_RULES: dict[TokenizerFamily, tuple[tuple[str, str], ...]] = {
    TokenizerFamily.O200K: (
        ("AssertionError", "AE"),
        ("KeyError",       "KE"),
        # only rules where (a) measured saving on o200k > 0
        # and (b) saving on cl100k >= 0 (no regression) go here
    ),
    TokenizerFamily.LLAMA3: (
        # populate from offline measurement on llama-3 tokenizer
        # examples: shorten `tests/cmd/`, prefer single-char status
    ),
}

_SHARED_RULES: tuple[tuple[str, str], ...] = (
    # rules every family agrees on; this is what compressors already do
)

def post_normalise(text: str, family: TokenizerFamily) -> str:
    out = text
    for find, repl in _DIVERGENT_RULES.get(family, ()):
        out = out.replace(find, repl)
    return out
```

Cache-key impact: `target_tokenizer` becomes part of the cache key
(strict superset of current key). Two calls that differ only in
target tokenizer produce different bytes, so they must cache
separately. Today's callers pass UNSPECIFIED and the existing key is
preserved.

Quality-harness impact: `must_preserve_patterns` are unaffected because
the divergent rules picked above are pure abbreviations the harness can
add to its acceptable forms (or they apply only to error-name spans
that aren't `must_preserve` for the corresponding compressor).

## Estimated impact

- Token reduction (compact tier, on the synthesised representative
  doc):
  - cl100k caller: 0 pp (default branch preserved)
  - o200k caller:  ~1.4 pp absolute (3 tokens / 214) over today's
    cl100k-tuned output, going from 13.1% additional saving back to
    parity with cl100k caller. Not breakthrough; below the >=5 pp
    threshold the project sets for "breakthrough".
  - Llama-3 caller: predicted 2-4 pp absolute based on the gpt2 proxy
    (gpt2 differs from cl100k by ~36% baseline, so the per-family
    rules have more room to run). Real number requires an offline
    Llama-3 calibration pass.
- Latency: cold-start unaffected (UNSPECIFIED path imports nothing
  new). Warm-call adds one dict lookup + at most ~10 `str.replace`
  calls when a family is set; under 1 us per call.
- Affects: `redcon/cmd/budget.py` (BudgetHint field),
  `redcon/cmd/pipeline.py::_normalise_whitespace`,
  `redcon/cmd/_tokens_lite.py` (overload), MCP `tool_run` and
  `tool_quality_check` signatures, cache key (`build_cache_key`
  in `redcon/cmd/cache.py` must hash the family).

## Implementation cost

- Lines of code: ~120 new + ~40 changed across `budget.py`,
  `pipeline.py`, `cache.py`, `_tokens_lite.py`, `mcp/tools.py`. Plus
  a new `redcon/cmd/post_normalise.py` (~40 LOC).
- New runtime deps: none for the dispatch itself. The optional
  measurement pipeline that produces the `_DIVERGENT_RULES` table for
  Llama-3 / Anthropic needs a one-shot offline run with `transformers`
  or the official tokenizer files; the runtime path stays
  network-free.
- Risks to determinism: dispatch is a pure function of (family, text),
  so determinism is preserved. The set of families is closed (Enum)
  and the rule tables are static globals.
- Risks to must-preserve: low for the rules above; the abbreviations
  reduce a recognisable error name. But: a `pytest_compressor`
  must-preserve pattern that requires `\bAssertionError\b` would break
  when `target_tokenizer=O200K` rewrites it to `AE`. Two fixes:
  (1) move the substitution to a *post-pattern-check* phase, or (2)
  expand the must-preserve regex to accept the abbreviation. Option 1
  is cleaner: run the family-specific table strictly after the M8
  harness validates the canonical text. This requires the
  must-preserve check to happen on the pre-substitution text inside
  the compressor (already true today since the compressor returns
  pre-normalised text and `_normalise_whitespace` is a post pass).

## Disqualifiers / why this might be wrong

1. **Empirical ceiling is small.** Measured upper bound for the
   cl100k <-> o200k axis is ~1-2 pp absolute. Project's "breakthrough"
   bar is >=5 pp across multiple compressors. This vector by itself
   does not clear it. The real value is correctness of reported token
   counts (today the heuristic char/4 lies to o200k callers) more than
   raw reduction.
2. **Maintenance vs benefit.** Each new tokenizer family is an offline
   measurement pass + a static table that drifts when the upstream
   tokenizer is retrained. With 4 families that is 4 tables to keep
   honest under release pressure. The redcon constraint of "no
   embeddings, deterministic, local-first" pushes against shipping
   tokenizer files in-tree (LZMA-compressed o200k is ~1.5 MB; Llama-3
   spm is ~5 MB). The current `redcon.core.tokens` lazy-imports
   tiktoken when present and falls back to char-ratio profiles - the
   same pattern fits here, but this means the *count is exact only
   when the optional dep is installed*; the *rewrite* applies whether
   or not it is.
3. **Already absorbed by V31 / V36.** V31 ("multi-token-string
   substitution table per tokenizer family") explicitly contains the
   substitution table per family. V36 ("cross-tokenizer Rosetta")
   explicitly seeks output that tokenises identically across families.
   V35 is the dispatch glue between V31's tables and BudgetHint.
   Without V31 there is nothing to dispatch *to*. Without V36 there is
   no clear baseline against which to measure "did the dispatch help".
   Risk: this vector is purely plumbing; it cannot be evaluated
   independently of V31's table population work.
4. **Caller doesn't know.** MCP servers currently get `model` info via
   the host environment, but neither Claude Code nor Cursor pass a
   reliable tokenizer signal in the redcon_run schema today. Adding
   the field is cheap; getting callers to populate it requires
   coordinating with each agent host. The reasonable default
   (UNSPECIFIED -> cl100k-aligned, char/4 estimator) is what we
   already have, so the field starts unused in the field. That kills
   the headline gain in practice for ~12 months until adopters update.

## Verdict

- Novelty: low (the idea is BASELINE-named; the contribution is
  measurement + integration sketch, not a new technique)
- Feasibility: high (small, contained, reuses existing model_profiles
  plumbing in `redcon/core/tokens.py` and `model_profiles.py`)
- Estimated speed of prototype: ~2 days for the dispatch + cache key
  work, +1 day per tokenizer family for offline rule-table generation
- Recommend prototype: **conditional-on V31** (dispatch without rule
  tables saves nothing). If V31 is sequenced for prototype, fold V35
  in as the BudgetHint+cache plumbing and the o200k table; defer
  Llama-3 / Anthropic tables to a later phase. Independent priority
  is low because measured ceiling is below the project's breakthrough
  threshold.
