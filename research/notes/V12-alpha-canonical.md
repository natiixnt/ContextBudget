# V12: Semantic-equivalence canonical form (alpha-rename, sort imports) before tokenisation

## Hypothesis
Code that Redcon ships in `snippet` / `symbol-extract` mode often spends tokens on long descriptive identifiers (`effective_hint_for_rewrite`, `log_path_display`, `command_cache_key_digest_value`) that the agent does not need to read literally to follow the function's structure. If, before tokenisation, we apply a semantic-equivalence-preserving canonical form -  alpha-rename of file-local private identifiers to short tokens (`a`, `b`, `c`...) and a sort+dedup pass on the import block - the cl100k token count drops measurably without breaking the agent's structural understanding. The technique is conditional: it must only fire on identifiers that are `(i)` file-private, `(ii)` not in the task keyword set, and `(iii)` reachable only by file-local references in the snippet being shipped. Public APIs and identifiers the agent is searching for stay verbatim.

The prediction: 6-8% raw token reduction on a typical Python pipeline file when locals inside a single function are renamed; 25-30% reduction on a messy unsorted import block; ~3-5% net on a whole file once the per-file alias side-table is included; and meaningful compounding *only* if the side-table cost is amortised across many references in the same shipped snippet (>= ~3 occurrences per renamed identifier).

## Theoretical basis
A cl100k-encoded snake_case identifier of length `L` consumes roughly `tok(L) ~= ceil(L / 5.5)` BPE tokens (empirically: `log_path` = 8 chars / 2 tokens, `effective_hint_for_rewrite` = 26 chars / 4 tokens). Renaming an identifier from `L` to 1 character reduces its per-occurrence cost from `tok(L)` to 1 token (single-letter ASCII inside a Python identifier is almost always a single BPE merge target).

For an identifier appearing `k` times in the snippet:

```
saving(rename) = k * (tok(L) - 1)              [tokens]
overhead(side_table_entry) = tok(L) + tok(short) + tok("=") + tok(",")
                          ~= tok(L) + 3        [tokens]

net(rename) = k * (tok(L) - 1)  -  (tok(L) + 3)
            = (tok(L) - 1) * (k - 1) - 4
```

Break-even occurs at `k = 1 + 4 / (tok(L) - 1)`. For `L = 26` (`tok = 4`), `k_break = 1 + 4/3 ~= 2.33`, so any identifier referenced >= 3 times nets a saving. For `L = 8` (`tok = 2`), `k_break = 1 + 4/1 = 5`. This bounds the technique sharply: long names with many references win, short names with few references lose.

Imports are a special case: the side-table is empty (semantics of `from X import a, b` are a Python language feature, not a Redcon convention) and dedup of duplicated `from X import` lines and merging of split aliases is pure free win. cl100k merges `import` and module-path tokens efficiently; dedup of a single 8-token line is a clean 8-token saving.

Empirical measurement (this researcher's experiment, see "Estimated impact" below) puts `_spill_to_log` at 41/675 = 6.1% local-rename, `compress_command` at 50/654 = 7.6%, and the messy-import block at 44/164 = 26.8%. These match the back-of-envelope.

## Concrete proposal for Redcon
A new opt-in compressor stage `redcon/compressors/symbols.py::canonicalize_snippet` that runs *after* the existing `select_symbol_aware_chunks` and *before* the snippet is handed to the tokenizer for budget counting. New module: `redcon/compressors/canonical.py` (the existing `symbols.py` is already 995 lines and does symbol selection; do not overload it). API sketch:

```python
# redcon/compressors/canonical.py
from dataclasses import dataclass

@dataclass(frozen=True)
class CanonicalForm:
    text: str                       # rewritten source
    alias_table: tuple[tuple[str, str], ...]  # (short, original) deterministic order
    keyword_preserved: tuple[str, ...]        # idents kept verbatim because matched task

def canonicalize(
    source: str,
    *,
    keywords: frozenset[str],
    public_names: frozenset[str],   # pulled from __all__, exports, import graph
    enable_imports: bool = True,
    enable_locals: bool = True,
    min_refs_for_rename: int = 3,   # break-even from theoretical_basis
) -> CanonicalForm:
    """Apply alpha-rename to file-private locals and sort+dedup imports.
    Deterministic: same input + same keywords -> byte-identical output."""

    tree = ast.parse(source)
    if enable_imports:
        tree = _sort_dedup_imports(tree)
    if enable_locals:
        local_idents = _collect_file_private(tree, public_names, keywords)
        # Only rename idents with >= min_refs_for_rename occurrences (theory).
        rename_targets = {n for n, count in local_idents.items()
                          if count >= min_refs_for_rename}
        tree = _alpha_rename(tree, rename_targets)
    text = ast.unparse(tree)
    alias_table = tuple(sorted(_alias_map(tree).items()))
    return CanonicalForm(text, alias_table, tuple(sorted(keywords & local_idents.keys())))
```

The alias table is emitted *only* when the snippet is shipped at `VERBOSE`/`COMPACT` and the table size is amortised positive (i.e., per-ident `k >= k_break`). At `ULTRA` we drop the alias table entirely (per BASELINE constraint 4: ULTRA may drop facts). Wiring point in `redcon/cmd/pipeline.py` is *not* affected; this is a file-side compressor, not a command-side one. The relevant call site is in the file-packing path (`redcon/stages/workflow.py::run_pack_stage` -> per-file compressor dispatch).

Pseudo-code for the keyword-preservation gate (the elephant the V12 brief flags):

```python
def _should_rename(name: str, keywords: frozenset[str], public: frozenset[str]) -> bool:
    if name in public:                         # never rename public APIs
        return False
    if not name.startswith("_") and len(name) <= 4:  # short already, no win
        return False
    if name.lower() in keywords:               # task is asking about this name
        return False
    if any(kw in name.lower() for kw in keywords if len(kw) >= 4):
        return False                           # substring match of task keyword
    return True
```

## Estimated impact

### Measurements (cl100k, tiktoken `cl100k_base`)

| Test | Original | Renamed | Delta | % |
|---|---|---|---|---|
| `_spill_to_log` locals only (no side table) | 675 | 634 | -41 | -6.1% |
| `compress_command` locals only (no side table) | 654 | 604 | -50 | -7.6% |
| Both functions combined | 1329 | 1238 | -91 | -6.8% |
| Synthetic messy 19-line import block | 164 | 120 | -44 | -26.8% |
| `_spill_to_log` net (rename + side-table 66 tok) | 675 | 700 | **+25** | **+3.7% LOSS** |
| `compress_command` net (rename + side-table 71 tok) | 654 | 675 | **+21** | **+3.2% LOSS** |

Source: `/tmp/v12_alpha_rename.py` (throwaway), measured against
`/Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/pipeline.py`
verbatim function bodies for `_spill_to_log` (lines 226-305) and
`compress_command` (lines 66-171).

### Ident-cost evidence on cl100k

- `log_path` (8 chars) -> 2 tokens; `log_path_display` (16 chars) -> 3 tokens
- `effective_hint_for_rewrite` (26 chars) -> 4 tokens
- `cache_key`, `compressor`, `raw_tokens` -> 2 tokens each
- `compressed`, `request` -> 1 token each (already cl100k-merged)

cl100k is *good* at snake_case. Many "long" Python identifiers are already 1-2 tokens. This is why the local-rename win is small (6-8%) rather than the 30-40% the proposal might suggest at first read.

### Repo surface area
Walking `redcon/`: 130 .py files, 541 private top-level definitions vs 516 public (51.2% private fraction). The technique applies cleanly to roughly half of all top-level symbols by count, and a higher fraction of *function-local* names (which are essentially all private).

### Net assessment
- **Token reduction (compact tier, file-side snippet):** **+1 to +3 absolute pp** when side-table is amortised across many references; **negative** on small isolated functions where `k < k_break`.
- **Token reduction on import-only rewrite:** **+15 to +25 pp** on the import block in isolation. As fraction of a typical 200-700 token file snippet: **+1 to +3 pp**.
- **Latency:** new AST parse + unparse per file is non-trivial (low-tens of ms on a 300-line file). Cold-start unaffected (lazy import). Warm parse adds maybe 5-15 ms/file.
- **Affects:** file-side `redcon plan` / `redcon pack` snippet output. Does not affect command-side compressors at all (those don't emit code identifiers). Does not interact with the cache key (canonical form runs *after* file selection, but its output should be hashed into the snippet content key so identical task -> identical output).

## Implementation cost
- **LOC:** ~250 net (200 in `compressors/canonical.py`, 30 wiring in `stages/workflow.py`, 20 in tests).
- **New deps:** none. Uses stdlib `ast`. (`ast.unparse` is stdlib since 3.9; project supports 3.14.) Does not violate "no embeddings, no required network" constraints.
- **Risks:**
  - **Determinism:** `ast.unparse` is determinstic but its output formatting differs from the original (re-flows whitespace, re-quotes strings). Need a golden-file regression suite. The existing `must_preserve_patterns` regex check (`redcon/cmd/quality.py`) does not cover file-side snippets; would need parallel infrastructure.
  - **Comprehension hostility:** if the task keyword extractor misses a synonym (agent is hunting `cache_key` but task says "key lookup"), rename strips information the agent needed. Mitigation: include alias-table as a footer at COMPACT; drop at ULTRA only.
  - **Reversibility:** if the agent quotes a renamed local back to the user, the user sees `a` instead of `log_dir`. The alias table fixes this for an LLM that reads it; a human reader has to consult it.
  - **Linter / refactor pressure:** a future researcher running `select_symbol_aware_chunks` on already-canonicalised code will mis-classify scoped scores (the keyword-hits weight in `_make_candidate` will see no hits because `effective_hint` -> `h0`). Order of operations must be: canonicalise *after* symbol selection, never before.
  - **Multi-line signature collision:** `_strip_py_annotations` in `symbols.py` (lines 510-562) already does a similar AST round-trip. Two AST round-trips per file is a minor perf concern; can fuse them.

## Disqualifiers / why this might be wrong

1. **Net-negative on isolated functions.** Measured: `+3.2%` to `+3.7%` *worse* once the alias table is emitted. The technique only wins when the snippet is large enough that several renamed idents each appear >= 3 times. The sweet spot is symbol-extracted *full files* of 300-1000 lines, not the 30-line stubs Redcon often ships. A representative pre-existing 30-line stub may LOSE tokens.
2. **cl100k is already efficient at snake_case.** `log_path` is 2 tokens, not 4. Most Python locals are already 1-2 tokens. The headline 6-8% measurement looks good but compounds to maybe 1-3 pp at the file/run level - well below the BASELINE.md "breakthrough = >=5 pp across multiple compressors" bar.
3. **It does not compose with V31/V37 (multi-token substitution / greedy rephrasing).** Those operate on text spans; canonicalisation rewrites the AST. Running both means the substitution table sees mostly single-letter idents that don't match its dictionary entries. The tokenizer-exact theme dominates this one.
4. **Imports sort+dedup is the only unambiguous win, and `isort` already does it.** A user who runs `isort` pre-commit will have already paid this cost; Redcon would get a 0% gain on those files. Code that *isn't* sorted is usually a sign of a one-author script - low-value to ship anyway.
5. **Hostile to grep-style debugging.** When the agent's *next* call is `grep effective_hint` against the same repo, the renamed snippet is now a red herring: the term doesn't appear in what the agent just read. This is a session-level coupling failure mode that BASELINE does not currently track.
6. **Already partially shipped in disguise.** `redcon/compressors/symbols.py::_strip_py_annotations` is a semantic-equivalence rewrite that strips type annotations from signatures (saves tokens, preserves call interface). V12's locals-rename is the same idea pushed further into the body, with worse cost-benefit because annotations have higher per-occurrence cost than locals.

## Verdict
- **Novelty:** medium. (Imports sort+dedup is trivial; locals-rename is novel for Redcon but the principle is folklore. The keyword-conditional gate is the genuinely new bit.)
- **Feasibility:** high. Pure stdlib, deterministic, fits cleanly as an opt-in compressor stage.
- **Estimated speed of prototype:** 1-2 days for canonical.py + golden tests + workflow wiring.
- **Recommend prototype:** **conditional-on:** (a) restricting the technique to *whole-file* shipped snippets >= ~400 raw tokens, where amortisation works; (b) including imports-sort even when rename is disabled, since that's the reliable win; (c) coupling with the task keyword extractor so renamed identifiers are guaranteed not to collide with what the agent will look up next. Without all three, the measured -3% net on small samples will dominate any aggregate gain.

The honest takeaway for V12 specifically: **imports-sort+dedup is worth shipping standalone (~1-3 pp file-side win, near-zero risk). Alpha-rename of locals is below BASELINE's breakthrough threshold and only marginally above zero net once you pay for the side table. Ship the imports half, table the rename half pending a session-level dedup story (Theme E) that could host the alias map cross-call instead of per-file.**
