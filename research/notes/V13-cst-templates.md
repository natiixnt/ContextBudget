# V13: CST-level template extraction across files - emit template once with parameter slots inline

## Hypothesis

Many source files chosen for a Redcon pack share the *same CST shape* even though identifiers and string literals differ. Examples in `redcon/` itself: 12 different `must_preserve_patterns` methods that are byte-distinct but structurally identical (decorator, single arg, return-Tuple-of-regex), 11 `cmd/compressors/*_compressor.py` modules whose top-level shape is the same dataclass + 2-3 helper functions + a `compress()` entry, and ~75 module-level docstrings. If we mine the top-K structural sub-trees, emit each template definition exactly once at the head of the pack, and replace each instantiation with a 1-line `<ref:#k name="foo" args=[...]>` placeholder, we should recover the structural redundancy that current per-file compression cannot reach. Prediction: 3-5 absolute percentage points on file-side compact-tier reduction *only* on packs of >=15 same-language files; below that the template-definition prelude dominates.

## Theoretical basis

Let the pack be `P` files, each file containing top-level statements `S_i,1..S_i,m_i`. Define structural class `[s] = key(s)` ignoring identifier names and constants. Across a pack, frequency of class `c` is `f_c = |{(i,j): [S_i,j]=c}|`, and average raw token cost of an instance is `t_c`.

If we keep one canonical instance per class as the "template" (cost `t_c + h` where `h` is a header overhead, ~5 tokens for `### TEMPLATE k`) and replace each subsequent instance with a reference of cost `r` (~6 tokens for `<ref:#k file=foo.py:L23-45>`), savings for class `c` are:

```
saved(c) = f_c * t_c  -  ( (t_c + h) + f_c * r )
        = (f_c - 1) * t_c  -  h  -  f_c * r
        = f_c * (t_c - r) - (t_c + h)
```

Class wins when `f_c * (t_c - r) > t_c + h`, i.e. `f_c > (t_c + h) / (t_c - r)`. With `r=6, h=5`:

| `t_c` | break-even `f_c` |
|-------|------------------|
| 10    | 3.75             |
| 20    | 1.79             |
| 40    | 1.32             |
| 80    | 1.15             |
| 160   | 1.07             |

So small (<10-token) templates need >=4 occurrences; large (>=40-token) templates win at 2 occurrences. Total savings:
`Total = sum_c max(0, f_c (t_c - r) - (t_c + h))`.

Equivalently, this is a degenerate one-symbol-per-class **MDL / grammar-induction** scheme: replace every leaf of an explicit grammar production by a non-terminal reference, where productions are mined from the file corpus (Re-Pair, Sequitur, Lehman-Shelat-style).

## Concrete proposal for Redcon

I empirically mined `/Users/naithai/Desktop/amogus/praca/ContextBudget/redcon` (130 `*.py`, 0 parse errors) using `ast`. Method: structural hash of node type + child arity, ignoring identifier names and constants, depth-bounded at 6.

**Raw counts (no filter)** - distinct structural keys: 1369; total candidate top-level / class-method nodes: 2722. Top 20 templates aggregate to raw=33,916 tokens, savable=25,759 tokens (75.9% of just-those-tokens), but the top 4 of those are dominated by single-line imports and module docstrings - sources that are either irrelevant (imports already 2 tokens) or *already addressed by `_strip_docstrings_in_text` in `redcon/compressors/context_compressor.py:65`* in the COMPACT tier.

**Refined counts (>=12 tokens, no imports, no module docstrings, count >=3)**: 47 templates, total 372 nodes, raw 25,455 tokens, savable 19,791 tokens (77.7% of just-those-tokens, **5.10% of full repo source = 388,350 tokens**). Subtracting the 4,146 tokens attributable to non-module-position docstrings (also already strip-able) leaves **~13.2k savable / 388k = 3.4% of full source**.

Top refined templates with structural shapes (after filters):

| rank | cnt | files | avg_t | raw   | saved | %   | shape |
|------|-----|-------|-------|-------|-------|-----|-------|
| 1    | 104 | 20    | 21    | 2209  | 1559  | 70.6| `<assign>` (e.g. compiled regex globals) |
| 2    | 75  | 75    | 62    | 4663  | 4146  | 88.9| `Expr/Constant` - non-zero-idx module docstrings |
| 3    | 12  | 10    | 20    | 249   | 152   | 61.0| `def must_preserve_patterns(self) -> tuple[...]` |
| 4    | 8   | 8     | 42    | 338   | 243   | 71.9| `def summarizer_report_as_dict(report) -> ...` |
| 5    | 7   | 7     | 69    | 487   | 371   | 76.2| `def format_test_result(...)` |
| 6    | 6   | 5     | 33    | 201   | 127   | 63.2| `def _to_float(x)` |

Pack-level effect: avg 2.08 top-15 templates per file; a 20-file pack would see ~42 hits.

**API sketch.** New module `redcon/compressors/cst_template.py`:

```python
# called by context_compressor.compress_ranked_files BEFORE per-file compression
def emit_pack_templates(files: list[CompressedFile], language: str) -> tuple[str, dict[Path, str]]:
    """Mine cross-file CST shapes; return (prelude_text, per_file_rewrites).

    prelude_text: '### TEMPLATES\n#1: def must_preserve_patterns(self) -> tuple[...]:\n    return (<R>,)\n...'
    per_file_rewrites: {file_path: rewritten_text} where matched nodes are replaced
        by '# <ref:#k name=ATTR_FOO regexes=[r"^a", r"^b"]>'
    """
    if language != "python":
        return ("", {})
    bucket = _hash_top_level_nodes(files)            # struct_key -> [(path, node, seg)]
    eligible = [(k, v) for k, v in bucket.items() if _wins_breakeven(k, v)]
    prelude_lines = []
    rewrites: dict[Path, list[tuple[int, int, str]]] = defaultdict(list)
    for tmpl_id, (key, occs) in enumerate(eligible, 1):
        rep_path, rep_node, rep_seg = _canonical_member(occs)
        prelude_lines.append(f"#{tmpl_id} ({len(occs)} sites): {_render_with_holes(rep_node)}")
        for (p, node, seg) in occs:
            slots = _extract_identifier_slots(node)         # the parameter values
            rewrites[p].append((node.lineno, node.end_lineno,
                                f"# <ref:#{tmpl_id} {slots}>"))
    return ("\n".join(prelude_lines), _apply_rewrites(rewrites))
```

Hooked into `redcon/compressors/context_compressor.py::compress_ranked_files` after `select_symbol_aware_chunks` but before final token accounting; gated by:
- Pack contains >= 15 same-language files (otherwise prelude > savings).
- COMPACT or ULTRA tier (verbose stays lossless-ish).
- Settable `CompressionSettings.cst_template_enabled = False` default.

Determinism: `bucket` ordered by `(count desc, sum_tok desc, struct_key asc)`; `_canonical_member` picks lexically smallest path. Mine is `O(N_nodes)` at parse time; AST already cached for symbol extraction in `redcon/compressors/symbols.py`.

Quality: must-preserve patterns survive trivially because we keep the canonical instance verbatim in the prelude (it's the longest member), and the `<ref:#k>` line carries the parameter slots so the agent can reconstruct names. ULTRA-only mode could drop the slots and just keep template id.

## Estimated impact

- Token reduction on **redcon/-itself** packs: floor 0% (small packs), ceiling **5.1% absolute** in the limit of "pack everything", realistic **~2.5-3.5%** on a 20-file pack matching the natural `redcon/cmd/compressors/*_compressor.py` cluster (10 files, all sharing the same 5-method structural skeleton).
- On heterogeneous repos (mixed Python + JS + tests + configs): **<1%**. Templates only emerge when files are structurally similar.
- Latency cold: +20-50 ms per pack to compute structural keys for ~2000 nodes (uses already-parsed AST). Warm: same, no caching layer initially.
- Affects: `redcon/compressors/context_compressor.py` (entry point), introduces dependency on `redcon/compressors/symbols.py::ast.parse` artifacts, no scorer changes, no cache key change (operates post-scoring).

## Implementation cost

- ~250 LOC new `redcon/compressors/cst_template.py` plus ~20 LOC hook in `context_compressor.py` plus ~80 LOC tests.
- New deps: none. `ast` is stdlib. Stays within "no embeddings, no network" constraints.
- Risks:
  1. **Reversibility**: agent now has to dereference `<ref:#3>` to a template body. If the model treats it as opaque text, all the "structure" is lost on the receiver side. Quality harness only validates regex-level invariants, not "agent can still reason about file." Need an offline eval (e.g. `redcon/core/agent_simulation.py`) to confirm.
  2. **Determinism**: the canonical-member tiebreaker must be a strict total order over file paths. Easy.
  3. **Must-preserve**: `cmd/compressors/*_compressor.py::must_preserve_patterns` regex-tuples are themselves a templated structure. If we collapse 12 such methods into one template, we lose the *regex contents* of the other 11 unless we encode them as slots. The slots become essentially the whole body, so saving collapses. Need to verify the avg template instance has small slots relative to scaffold.
  4. **Language coverage**: only Python for v1. JS/TS/Go would need separate parsers or `ast_grep_py` (already a dep per `redcon/structural_search.py:64`).

## Disqualifiers / why this might be wrong

1. **Already partly done**: `_strip_docstrings_in_text` (in `redcon/compressors/context_compressor.py`) already removes ~4-9k of the headline savings (rows #2 and #4 in raw mining). Net opportunity above current COMPACT tier is **~13k / 388k = 3.4%** on redcon-itself, far below the BASELINE breakthrough threshold of 5pp. On a typical heterogeneous repo it would be even lower.
2. **The "interesting" templates aren't the structural ones**. After filtering imports and docstrings, my top templates are: 104 `<assign>` nodes (dominated by `_FOO_RE = re.compile(r"...")` patterns whose *value* is the entire information content - templating the LHS shape is meaningless), and 12 `must_preserve_patterns` methods whose *regex tuple body* is the actual content. The shared structure is just `def name(self) -> tuple: return (...)`, which is 5 tokens of scaffold. Slots = 95% of bytes.
3. **Indirection cost on the receiver**. Models tokenise `<ref:#3>` and `### TEMPLATES` blocks as ordinary text; they cannot literally substitute. The agent then either ignores the prelude (and loses signal) or asks for the file again (and we wasted budget). Without an evaluation harness on `redcon/core/agent_simulation.py` proving the agent still answers correctly, the savings are paper-only - exactly the trap that the BASELINE explicitly flags by listing rate-distortion and quality harness as gates.
4. **Symbol extraction supersedes templating**. `redcon/compressors/symbols.py::select_symbol_aware_chunks` already keeps only task-relevant symbols and strips signatures of irrelevant ones. If a 12-occurrence template is present in only 2 of the relevant files (because the others got pruned to 0 symbols), templating saves nothing.
5. **Cross-call dictionary is the right place** for this idea (BASELINE's "what is NOT done yet" item: cross-call dictionary, V41-V50). Templates that span multiple agent turns return amortised gains; one-shot pack templating fights the prelude cost on every pack.

## Verdict

- Novelty: **low-medium**. The core idea is grammar-based MDL compression of source code; well-known in academia (Sequitur, Re-Pair, Allamanis idiom mining). Application to AI coding context is mildly novel, but BASELINE already does AST-driven symbol extraction, docstring stripping, and slice selection - the CST-template layer adds another tier on top of those without compounding multiplicatively. The 5.1% theoretical ceiling on redcon-itself, and the 3.4% net-of-existing-strip ceiling, are below the breakthrough bar (>=5pp on multiple compressors).
- Feasibility: **medium**. Implementation is straightforward (stdlib ast, ~250 LOC). The hard part is the agent-side eval: proving the receiver can still answer the same questions about a templated pack. Without that, the change ships a quality regression of unknown size.
- Estimated speed of prototype: **~2 days** for the mining + rewriter + harness, **+ 1 week** for the agent-simulation evaluation that would actually justify shipping it.
- Recommend prototype: **conditional-on**: (a) the proposal pivots to *cross-call* templating (V42/V43 territory) where prelude cost amortises across many packs, AND (b) an `agent_simulation.py` harness is wired up to measure receiver-side regression. As stated (single-pack, file-side only), **no** - the savings ceiling on a representative pack is below 5pp and below the cost of building a quality harness for the receiver-side risk.
