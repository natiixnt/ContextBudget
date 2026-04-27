# V90: BNF formal grammar validation for output reversibility

## Hypothesis
Every Redcon compressor emits text that, although meant for an LLM, has an implicit, finite, regular-or-context-free structure. Today this structure is enforced only by `must_preserve_patterns` regexes (a soundness check on a few facts), by per-level token-reduction floors, and by determinism re-runs. None of these are a *grammar*; they cannot tell you that a fresh format change accidentally introduced an ambiguous line that downstream tooling (a VS Code extension parser, a delta-vs-prior-run differ, a session-dictionary dedup of V41) will choke on. The hypothesis: write a BNF for each compressor's compact-tier output, parse all 21 fixtures from `tests/test_cmd_quality.py::CASES` against it in CI, and treat any parse failure as a release-blocking quality regression. This buys (1) a machine-checked invariant ("downstream parsers will succeed"), (2) an executable spec that fuzzers (V81, V85, V89) can sample from, and (3) a partial reversibility witness: the parsed AST shows exactly which fields of the canonical type (`DiffResult`, `LintResult`, `KubeListResult`, ...) survived the format and which were discarded.

## Theoretical basis
The compact-tier formatters are total functions `f_lvl: Canonical -> str` where `Canonical` is the structured dataclass (`DiffResult`, `TestRunResult`, etc., defined in `redcon/cmd/types.py`). Compression is information-discarding by design - ULTRA truncates the path list to 8, COMPACT keeps only the first hunk per file. The relevant property is *partial reversibility*: there exists a parse function `g: str -> Canonical_lossy` such that `g(f(c))` agrees with `c` on a known projection `pi`. A BNF G defines exactly the language of `f`'s image and gives us `g` for free via any parser-generator. Concretely for `_format_compact` in `redcon/cmd/compressors/git_diff.py:259`:

```
diff       ::= summary NL file_block*
summary    ::= "diff: " uint " files, +" uint " -" uint
file_block ::= file_line ( NL hunk_line ( NL more_hunks )? )?
file_line  ::= STATUS SP PATH ( " (from " PATH ")" )? ":" SP "+" uint SP "-" uint
             | STATUS SP PATH SP "(binary)"
hunk_line  ::= "@@ -" uint "," uint " +" uint "," uint " @@" ( SP HEADER )?
more_hunks ::= "+" uint " more hunks"
STATUS     ::= "A" | "M" | "D" | "R" | "?"
PATH       ::= [^ \n]+
HEADER     ::= [^\n]+
uint       ::= [0-9]+
```

Soundness argument: if every fixture parses, then for any structurally identical (same status set, same path-character class, same hunk-count semantics) future input, `_format_compact` will continue to produce parseable output. Counter-examples are exactly the regression cases we want to catch (e.g. a path containing a literal space breaks PATH; the current code path-quotes nothing, so this is a real lurking bug). The grammar makes the bug a syntactic violation rather than a vibes-test.

A back-of-envelope on the value: 21 fixtures x ~3 levels = 63 (string, expected_grammar) pairs. At ~5 grammar productions per compressor x 11 compressors = ~55 productions total. Parsing a 200-line compact output against a 55-rule LL(1)-ish grammar is sub-millisecond (~10 us with Lark or hand-rolled). Total CI cost: <1 ms across the whole quality matrix. Coverage cost: the fraction of `f_lvl`'s output space *exercised* by 21 fixtures is small, but each fuzz-generated input that parses gives us another point of evidence that `g . f = pi`. Combined with V81 (property-based fuzz on must-preserve) and V85 (adversarial input), the grammar is the structural backbone shared across all three.

## Concrete proposal for Redcon
Files involved (proposed; no production source modified by this note - per the M0 contract):
- `redcon/cmd/grammar/` (new package).
  - `redcon/cmd/grammar/git_diff.lark` (and one `.lark` per compressor schema).
  - `redcon/cmd/grammar/__init__.py` exposes `GRAMMARS: dict[str, Lark]` keyed by `compressor.schema`.
  - `redcon/cmd/grammar/parse.py` has `parse_compact(schema, text) -> ParseTree | ParseError`.
- `tests/test_cmd_grammar.py` (new test module, parallel to `tests/test_cmd_quality.py`):
  - For every `(name, compressor, raw_stdout, _, argv)` in the existing `CASES` list, run the compressor at COMPACT and at VERBOSE and assert `parse_compact(c.schema, output.text)` does not raise.
  - For ULTRA, the grammar is a strict subset (`ULTRA_GRAMMARS`); not all schemas have one because ULTRA can collapse to a single sentence.
- `redcon/cmd/quality.py::QualityCheck`: add an optional `grammar_ok: bool` field, default True for backward compat, populated by `tests/test_cmd_grammar.py` via a separate harness function `run_grammar_check(compressor, raw, argv)` that reuses the level-pinning helper `_force_level_hint`.
- `pyproject.toml`: add `lark` as a *test-only* dep. Lark is pure-Python, no compile step, no network - keeps the "no required network, no embeddings" constraint.

Sketch of the harness call site:

```python
# tests/test_cmd_grammar.py
import lark
from redcon.cmd.grammar import GRAMMARS
from tests.test_cmd_quality import CASES  # reuse the 21-fixture matrix

@pytest.mark.parametrize("name,compressor,raw,_,argv", CASES, ids=[c[0] for c in CASES])
def test_compact_output_parses(name, compressor, raw, _, argv):
    out = compressor.compress(raw, b"", _ctx_compact(argv))
    parser = GRAMMARS.get(out.schema)
    if parser is None:
        pytest.skip(f"no grammar yet for {out.schema}")
    try:
        parser.parse(out.text)
    except lark.UnexpectedInput as e:
        pytest.fail(f"{name}/{out.schema} compact output not in grammar: {e}")
```

CI integration: add a `pytest tests/test_cmd_grammar.py -q` invocation alongside the existing `test_cmd_quality.py` run. No new GitHub Action needed - the existing pytest gate already enforces the quality harness; grammar would slot in identically.

## Estimated impact
- Token reduction: zero. This vector is documentation + a CI gate, not a compressor. (The vector explicitly tags itself as "DOCUMENTATION + SCHEMA improvement"; this is a faithful match.)
- Latency: cold parse adds ~5 ms (Lark grammar compile) on first import, amortised once per process. Warm: <1 ms across all 21 fixtures combined. No effect on the `redcon run` hot path since parsing only runs in tests.
- Affects which existing layers: `redcon/cmd/quality.py` (additive field), CI workflow (one extra pytest module), no behaviour change in `redcon/cmd/pipeline.py`, `redcon/cmd/cache.py`, `redcon/cmd/compressors/*`.

## Implementation cost
- Lines of code: ~30 LOC of Lark grammar per compressor x 11 compressors = ~330 LOC of grammars. ~80 LOC of harness in `tests/test_cmd_grammar.py`. ~40 LOC for `GRAMMARS` registry. Total: ~450 LOC, all in test/spec scope.
- New runtime deps: `lark` is test-only. It's pure-Python, MIT-licensed, no native extensions, no network. The product wheel is unchanged.
- Risks to determinism / robustness / must-preserve guarantees:
  1. Determinism is unaffected: parsers are deterministic by construction.
  2. Risk: a too-strict grammar refuses a *legitimate* future format change and becomes process drag. Mitigation: the grammar is defined alongside the formatter; PRs that change `_format_compact` must update the `.lark` file in the same change. This is the same coupling that already exists between `must_preserve_patterns` and the formatter.
  3. Risk: grammars drift behind reality and pass-by-default. Mitigation: every fixture in `CASES` parses on CI; a new compressor without a grammar `pytest.skip`s loudly so it cannot land silently.

## Disqualifiers / why this might be wrong
1. **Already partially done by must-preserve patterns.** The existing `must_preserve_patterns` (e.g. `git_diff.py:44-48`) is a *positive* check: "this regex must match somewhere". A grammar is a *closed* check: "every line must match some production". These are complementary, not redundant - but a project with finite engineering budget could argue must-preserve is enough. Counter: must-preserve doesn't catch *added* malformed lines; grammar does. The bug class "diff format suddenly emits a hunk-header on the same line as a file-header" is invisible to must-preserve and trivially caught by BNF.
2. **Reversibility is overclaimed.** The grammar gives `g: str -> ParseTree`, not `g: str -> DiffResult`. To get all the way back to the canonical type you'd need to lift parse-tree nodes into `DiffFile` etc. - another ~200 LOC per compressor. That's V19 (AST-diff) or a sub-ask of V47 (snapshot delta) territory, not V90 proper. So "prove invertibility up to discarded info" is a stretch goal; what we deliver here is parse-success, which is strictly weaker than reversibility.
3. **Coverage is shallow.** 21 fixtures don't span the format's full expressive range - they exercise the happy paths the developers thought of. Without integration with V81-style property-based generation the grammar is a snapshot test in disguise. The vector explicitly says to integrate with V82 (golden corpus); this is the right framing - V90 is the *spec* layer, V82 is the *witness* layer, and they're co-designed or neither is convincing.
4. **ULTRA tier has no useful grammar.** `_format_ultra` (`git_diff.py:245`) emits one comma-joined sentence with `+N more` truncation. The grammar would be `"diff: " uint " files, +" uint " -" uint " [" PATH ("," SP PATH)* ("," SP "+" uint SP "more")? "]"` - parseable but uninformative; the comma in PATH (rare but legal) breaks it. So V90's grammar coverage is COMPACT + VERBOSE; ULTRA is left to must-preserve and hand-written assertions, same as today.
5. **PATH ambiguity is real.** Real git paths can contain spaces and unicode. The current `_format_compact` does not quote paths. A BNF that says `PATH ::= [^ \n]+` will reject any space-containing path the moment a fixture for one shows up. The grammar will *expose* this latent bug rather than fix it - which is arguably the value, but it also means landing V90 forces a decision about path quoting that has been silently deferred. That's scope creep on a "low-novelty" deliverable.

## Verdict
- Novelty: **low**. This is the standard "write a grammar for your protocol" hygiene exercise from any IETF RFC. Nothing about Redcon makes the technique novel. Per the task brief, mark Novelty low and recommend tight integration with V82 (golden corpus, byte-for-byte differential testing) so the two together form a complete formal-spec layer: grammar = closed acceptance set, golden corpus = chosen witnesses inside it, fuzzer = exploration of edges.
- Feasibility: **high**. ~450 LOC, no runtime cost, no determinism risk, integrates with the existing parametrized `CASES` list with one decorator change.
- Estimated speed of prototype: **2-3 days**. One day to write 11 compact-tier `.lark` files (most are a dozen productions each), half a day to wire the harness, half a day to repair the path-quoting / multi-status edge cases the grammar will surface, half a day to align with V82 framing in `research/notes/`.
- Recommend prototype: **conditional-on-V82**. As an isolated effort the value is "machine-checked spec for downstream tools" - useful but not breakthrough. Bundled with V82 (golden corpus) and V81 (property-based fuzz), it becomes the structural backbone of Theme I. Build them as one M-step or not at all; building V90 alone delivers a CI gate that documents existing behaviour and catches one or two latent path-quoting bugs - real but small.
