# V19: AST-diff representation for code-mod tasks - preserve only edit operations on the tree

## Hypothesis

For agent tasks that are code-mods (rename a symbol, replace an API call, insert a parameter), the load-bearing information in `git diff` is *which AST edits* were applied, not which textual lines moved. Today `redcon/cmd/compressors/git_diff.py` already drops hunk bodies at COMPACT (97.0% reduction), so a strict reduction win over COMPACT is unlikely. The actual claim is narrower: **at COMPACT tier, AST-diff preserves the semantic edit (`fetch_user -> load_user x6`) that COMPACT discards**, and **at VERBOSE tier, AST-diff replaces the verbose +/- block dump with a smaller edit script for pure code-mod commits**. Prediction: a new `ast_diff` sub-tier sits between COMPACT and VERBOSE, fires on a narrow trigger (all changed files share one supported language AND aggregate changed-line count below threshold), recovers semantic content COMPACT lost, and on rename-shaped diffs reduces tokens by **>=90%** vs raw while *retaining* what COMPACT throws away. On non-code-mod diffs (intra-function logic refactors, mixed configs, docs, large body rewrites) it loses to COMPACT and must fall back.

## Theoretical basis

Treat a diff as describing a transformation `T: AST_old -> AST_new`. Three candidate encodings:

1. **Raw unified diff** - line edit script over the *text serialisation* of the AST. Cost grows with the number of textually changed lines `L` and per-line average byte cost `b`. Tokens `~ b * L`.
2. **Tree edit distance script** (Zhang-Shasha 1989, Chawathe et al. 1996, GumTree 2014) - sequence of `Insert(node, parent, idx)`, `Delete(node)`, `Update(node, label)`, `Move(node, parent', idx')` operations. Cost grows with the number of *node-level* edits `E`, where for a pure rename `E = 1` (one Update on a symbol-table entry) and `b * L` corresponds to `O(occurrences * line_width)`.
3. **Symbol-rewrite script** - degenerate tree-diff where we recognise the macroscopic operation (Rename, ReplaceCall, InsertParam) and emit one op per macro. For a rename of one identifier with `k` occurrences, encoding cost is `O(1)` regardless of `k`.

Back-of-envelope. Let a refactor change `n` top-level statements out of `N`, with each changed statement averaging `s` tokens when unparsed. Raw diff approximates `2 * n * s + O(n)` (added + removed copies + headers). AST-edit-script using full unparse of changed statements approximates `n * s + O(n)`, i.e. **at most a 2x win** when every changed top-level statement is rewritten. Symbol-rewrite script for a rename with `k` call sites gives `O(1) << k * s`, an unbounded win.

For the existing `_format_compact`, cost is `O(F)` (one line per file plus first-hunk header), independent of `s` or `n`. Therefore:

- AST-edit-script (full-unparse) beats raw by ~2x but loses to compact by `>1` order of magnitude.
- Symbol-rewrite script wins against everything *only* on the homogeneous-rename regime.

The information-theoretic floor `H(T)` of a "rename `f` to `g` everywhere" is `2 * log_2(|symbols|)` bits, i.e. ~30 bits ~ 4 cl100k tokens. Both raw and existing COMPACT vastly overshoot this floor on rename-shaped diffs; AST-diff approaches it.

## Concrete proposal for Redcon

Add a new tier between COMPACT and VERBOSE inside `redcon/cmd/compressors/git_diff.py`, gated by a heuristic. **It is not a replacement for COMPACT**; it fires only when all three triggers hold:

1. Every changed file in the diff has a supported-language extension (`.py` for v1; `.ts/.tsx/.js` and `.go` for later via tree-sitter, already a soft dep per `redcon/structural_search.py`).
2. Aggregate changed-line count `<=` threshold (default 250). Above this the unparsed bodies blow up faster than raw.
3. The caller pinned the level to `COMPACT_PLUS` (a new optional level) OR the default selector chose `COMPACT` AND `ctx.hint.prefer_semantic=True`. Default behaviour preserves current outputs.

On match, the compressor parses each changed file's pre/post source (already accessible: the diff shows `--- a/...` and `+++ b/...`; we resolve via `git show <oid>:path` or read the working tree for one side). It emits an edit script with three op kinds, in priority order:

```
RENAME_NAME   <old> -> <new>  (xN sites)        # ast.Name frequency-balance match
REPLACE_CALL  <old.qual> -> <new.qual>  (xN)    # Call.func qualname change
SIGNATURE     <qualname>  (a:int) -> (a:int, b:str=None)
FN_BODY       <qualname>  +stmt:..., -stmt:..., =N unchanged
```

Pseudocode (50 LOC; lives in `redcon/cmd/compressors/git_diff_ast.py`, called from `git_diff.py::_format` when `level == COMPACT_PLUS`):

```python
def ast_edit_script(a_src: str, b_src: str) -> str:
    a_freqs = _name_freqs(ast.parse(a_src))
    b_freqs = _name_freqs(ast.parse(b_src))
    ops = _detect_renames(a_freqs, b_freqs)        # frequency-balance pairing
    # peel renames out, then run a stmt-level diff over what remains:
    a_norm = _apply_renames(a_src, ops)            # rename a_src -> b's identifiers
    if _ast_equal(a_norm, b_src):
        return "\n".join(ops)                       # pure rename - we are done
    a_funcs = _collect_funcs(ast.parse(a_norm))
    b_funcs = _collect_funcs(ast.parse(b_src))
    for q in sorted(a_funcs.keys() & b_funcs.keys()):
        if a_funcs[q].sig != b_funcs[q].sig:
            ops.append(f"SIGNATURE {q}  {a_funcs[q].sig} -> {b_funcs[q].sig}")
        if a_funcs[q].body_dump != b_funcs[q].body_dump:
            ops.append(f"FN_BODY {q}")
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                a=a_funcs[q].stmts, b=b_funcs[q].stmts).get_opcodes():
                if tag in ("delete", "replace"):
                    ops += [f"  - {s}" for s in a_funcs[q].stmts[i1:i2]]
                if tag in ("insert", "replace"):
                    ops += [f"  + {s}" for s in b_funcs[q].stmts[j1:j2]]
    for q in sorted(b_funcs.keys() - a_funcs.keys()):
        ops.append(f"FN_ADD {q}{b_funcs[q].sig}")
    for q in sorted(a_funcs.keys() - b_funcs.keys()):
        ops.append(f"FN_DEL {q}{a_funcs[q].sig}")
    return "\n".join(ops)
```

`must_preserve_patterns` extends with `r"\bRENAME_NAME\b|\bSIGNATURE\b|\bFN_(ADD|DEL|BODY)\b"` so the quality harness verifies the marker survives. Determinism: dict iteration replaced with `sorted(...)` keys; `_detect_renames` uses lex-sorted (count, name) ordering. Cache key is unaffected (argv canonicalisation handles this). Fallback: if `ast.parse` raises on either side (working-tree may be mid-edit), the compressor returns `_format_compact(result)` and notes `ast_diff_skipped: parse_error`.

## Estimated impact

Measured empirically against two real datapoints in this repo (cl100k tokens via `redcon.cmd._tokens_lite.estimate_tokens`):

**Datapoint A: real commit `15484f0` ("perf(cmd): prefix-gate parsers...") - three-file Python refactor, intra-function control-flow rewrite, 83 ins / 60 del.**

| Encoding                              | Tokens | Reduction vs raw |
|---------------------------------------|--------|------------------|
| Raw `git diff 15484f0^..15484f0`      | 1946   | -                |
| Existing COMPACT (`_format_compact`)  | 103    | **94.7%**        |
| Existing ULTRA  (`_format_ultra`)     | 37     | 98.1%            |
| `ast.dump()` + difflib unified diff   | 7223   | **-271%** (worse)|
| Hand-rolled FN-level edit script (full unparse of changed top-level stmts) | 1442 | 25.9% |

The intra-function refactor changes the *interior of one for-loop* in three different functions. No top-level statement is added or removed; the unparsed compound statement that contains the change is large (~50 tokens each). AST-edit-script ends up 14x larger than what COMPACT already ships. **AST-diff loses on this representative refactor.**

**Datapoint B: synthetic but realistic pure-rename (one symbol, six call sites in one file).**

| Encoding                                  | Tokens | Reduction vs raw |
|-------------------------------------------|--------|------------------|
| Raw `git diff --no-index`                 | 270    | -                |
| Existing COMPACT (drops hunk bodies)      | 19     | 93.0%            |
| Symbol-rewrite AST edit (`RENAME_NAME fetch_user -> load_user (x6)`) | **10** | **96.3%** |

On the rename, AST-edit beats COMPACT by 9 absolute points (47% relative further reduction beyond COMPACT) **and** preserves semantic content COMPACT discards: the agent sees `fetch_user -> load_user x6`, whereas COMPACT only says "+10 -7 in synth_after.py".

**Heuristic firing rate on this repo.** Of the last 50 commits: 31 are pure-Python (62%), but only **8 are pure-Python AND under 200 changed lines** (16%). Of those 8, my qualitative read of `git show --stat` and the commit messages says ~2-3 are rename-shaped (e.g. lazy-loader rewrites). Realistic firing rate: **single-digit percent** of all `git diff` calls. The conditional reduction *when fired on a rename* is large; the unconditional expected reduction over the whole compressor is ~0.

- Token reduction expected: **+0 abs pp on the average git_diff call** (heuristic almost never fires) but **conditional +3 to +9 abs pp at COMPACT** when it does, AND a quality dimension (semantic content the agent can act on) that COMPACT structurally cannot provide.
- Latency: cold `+15-30 ms` to import `ast` (already imported elsewhere in redcon) and parse two source bodies per changed file. Warm: same. Acceptable.
- Affects: `redcon/cmd/compressors/git_diff.py` only; no scorer or cache changes.

## Implementation cost

- ~200 LOC new module `redcon/cmd/compressors/git_diff_ast.py`, ~30 LOC hook in `git_diff.py::_format`, ~120 LOC tests in `tests/test_cmd_git_diff_ast.py` covering: pure-rename, rename + body-edit, parse-error fallback, mixed-language fallback, large-diff fallback, determinism (run twice byte-equal), must-preserve harness with the new patterns.
- New deps: none for Python (`ast` is stdlib, `difflib` is stdlib). For TS/JS/Go we'd need `ast-grep-py` (already an optional dep per `redcon/structural_search.py`) or `tree_sitter` (also already optional in `redcon/symbols/` per the untracked dir in git status). Stays within "no embeddings, no required network".
- Risks:
  1. Reading pre-image source. The diff arrives as text; `git show <commit>:path` requires git access, which is available for `git diff <rev>` invocations but not for `git diff --no-index`. Need to handle both - for `--no-index` the two paths are right there in the argv; for `git diff` we shell out to `git show <oid>:path` (one extra subprocess per file). This violates "no further subprocesses inside compressor" implicitly assumed by the current pipeline. Cost is bounded but is a new pattern.
  2. Determinism is fine if `_detect_renames` uses a strict total order. Frequency-balance pairing has ties (two removed names with same count); break ties on lex order.
  3. Must-preserve: the existing pattern `r"\bdiff --git\b"` no longer holds for AST-diff output. Need a sub-schema or tier-conditional preserve check, otherwise the harness flags AST-diff as a regression on every commit.

## Disqualifiers / why this might be wrong

1. **The dominant case is "intra-function refactor", not rename.** Datapoint A is the realistic shape: every changed top-level statement is fully unparsed in the edit script, and the unparse is ~50 tokens per compound stmt. Net of the rename heuristic, AST-diff is **14x larger than COMPACT** on the representative commit in this repo. Without ML-grade tree-edit-distance + delta-encoding the inner statement (Chawathe / GumTree level), the simple "list of changed top-level stmts" loses badly.
2. **Heuristic firing rate is single-digit percent.** Only 8/50 recent commits even meet "pure-language under 200 lines"; the further filter "looks like a code-mod, not a logic refactor" probably halves that. The compressor cannot distinguish those upfront cheaply - we'd have to run AST-diff *first* and measure its output size against COMPACT. That's a "compute both, pick smaller" pattern, double the cost on every diff. The BASELINE cold-start budget is already tight (-62% just shaved); doubling diff parsing for a 1-in-10 win is hard to justify.
3. **COMPACT already wins on tokens, AST-diff only adds a quality dimension.** Reductions are at the saturation point already (94.7%); the BASELINE breakthrough threshold is "+5 abs pp across multiple compressors" or a new dimension that *compounds*. AST-diff doesn't compound - it replaces COMPACT's output, fighting the same byte budget. The quality dimension (semantic edit visible to the agent) is real but not measurable inside the existing must-preserve harness; it needs a receiver-side eval analogous to V13's open question.
4. **Cross-language is hard.** Python is doable in ~200 LOC; TypeScript needs `tree-sitter-typescript` and a hand-coded edit-op extractor (CST != AST, more node kinds). Go needs `go/ast` via `go run ...`, breaking determinism unless we ship a static binary. The "all files share a language" gate works only for Python-only repos in practice; the moment a JS file is in the diff, we fall back. Most modern codebases are polyglot.
5. **GumTree / TreeSitter-diff exists already.** This is a published technique (Falleri et al., ASE 2014). Building a lossy variant for an LLM context is a clean re-application but not novel research. Anything beyond the tier-integration work duplicates existing tools the user could pipe in via `git difftool`.
6. **Already partly done at the symbol layer.** `redcon/scorers/import_graph.py` and the symbol extraction in `redcon/compressors/symbols.py` already give the agent the *post-state* signature of every changed function. The agent can compute the diff itself by comparing against its prior-turn snapshot, which is exactly what V47 (snapshot delta) targets - cheaper and language-agnostic. AST-diff fights for ground V47 already covers from a different angle.

## Verdict

- Novelty: **low-medium**. Tree edit distance is 1996 (Chawathe), GumTree 2014, and the recipe of unparsing changed nodes for an LLM is the obvious adaptation. The narrow contribution is the *heuristic* that decides when to use it, plus the rename-balance fast path.
- Feasibility: **medium**. ~200 LOC for the Python path is a couple of days. The pre-image source acquisition (extra `git show` subprocess) is a minor pattern break but tolerable. Cross-language is a multi-week project per language.
- Estimated speed of prototype: **2-3 days** for Python-only, gated, with tests; **+1-2 weeks** to add TS via tree-sitter; **~4 weeks** to add Go cleanly without a CGO dependency.
- Recommend prototype: **conditional-on**: (a) the prototype is scoped to *only* the rename and replace-call detector (the regime where it wins by >5 abs pp), and (b) the trigger is opt-in via `redcon_run` argv `--ast-diff` rather than auto-firing on any matching diff. Without (b), the expected-value computation says we add 200 LOC, +15-30 ms latency, and a new failure mode for a feature that fires on <10% of diffs and is dominated by COMPACT on most of those. As stated (auto-tier integration), **no** - the breakthrough criterion in BASELINE is unmet on the average diff, and the average diff is what matters for a `redcon run git diff` workload. The breakthrough surface in this repo is V47 (snapshot delta) or V41-V50 (cross-call dictionaries), where the same kind of "what changed since last time" intelligence amortises across the whole session, not one diff at a time.
