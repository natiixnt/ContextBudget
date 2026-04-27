# V18: Data-flow taint slicing - include only lines reachable on import / data-flow path from task keywords

## Hypothesis

The current snippet strategy (`_snippet_from_text` in `redcon/compressors/context_compressor.py`) is a 1-D windowing operation: it ranks lines by literal substring match against task keywords, takes the first `snippet_hit_limit` (default 6) hits, and stamps a `+/- snippet_context_lines` window (default 1) around each. That window is purely lexical. For files where the keyword appears many times, the window saturates on the first 6 hits and the *actually load-bearing* lines (the function that *implements* the keyword's behaviour, the argparse subparser that *registers* it, the data structures it *mutates*) are silently truncated.

Replace the keyword-window with a Weiser-style program slice (Weiser, "Program Slicing", IEEE TSE 1981). A SLICE rooted at the seed lines is the def-use closure under name-binding: lines that define names referenced in seed lines (backward) plus lines that consume names defined in seed lines (forward), to a fixed point. For a static, single-file, intra-procedural, name-based approximation, this is computable in one AST walk and gives a strictly more semantically focused subset than the keyword window.

Quantified prediction (verified below on `redcon/cli.py` with task keyword `init`): the snippet captures only 3 of 21 keyword hits (first 6 hits yield 3 ranges after merging) and misses both `def cmd_init(...)` at line 1511 and the argparse `init_cmd = sub.add_parser("init", ...)` block at 3240-3261 - the two most agent-actionable lines for a task mentioning "init". The slice catches both at ~3.5x the token cost, but is the correct unit when the agent's question is "how does `init` work?".

## Theoretical basis

Weiser's slice S(p, V) of program p with respect to variable set V at slice criterion (statement s, V) is the smallest subset of statements whose execution preserves the values of V at s. The full inter-procedural slice is a CFG/PDG (program-dependence graph) reachability problem and is PSPACE-hard for the precise version, but the **intra-procedural, name-based, depth-capped approximation** is O(N) on the AST.

### Slice definition for V18

Given source `p`, keyword set `K`, seed lines `S = {l : line(l) contains any k in K}`:

  1. Names of interest `N`: union over each seed line `l` of `idents(l)` where idents = AST `Name`/`Attribute`-base/`arg`/`FunctionDef.name`/`ClassDef.name`. Filter to `N* = {n in N : f(n)}` where `f` is a relevance predicate (see below).
  2. Backward closure `B(N*)`: every line where any name in `N*` is *bound* (assigned, imported, declared via `def`/`class`/parameter).
  3. Forward closure `F(N*)`: every line where any name in `N*` appears as a `Load`-context use, restricted to the syntactic scope already touched by S (depth-1).
  4. Slice = union of S, B, F lines, plus function/class signature lines for any def whose body intersects S (so the agent sees what it is inside).

### Recoverable precision (back of envelope)

For a file with `H` keyword hits and average snippet window `2c+1` lines, the snippet covers `min(snippet_hit_limit, H) * (2c+1)` lines after merge, each line scored only by literal proximity to keyword text. The expected number of *semantically relevant* lines (lines on the data-dependence frontier of the keyword) is unbounded in `H` but bounded in `|N*|`: each name in `N*` typically has 1-4 binding sites and 5-30 use sites in a single Python file. For `cli.py` and keyword `init`:

    H = 21 hits
    Snippet capture: 3 ranges, 18 lines, 144 tokens (6 of 21 hits used; only 3 distinct ranges after merging contiguous hits)
    Slice (V18, tight predicate): 12 ranges, 50 lines, 514 tokens
    Missed by snippet, found by slice: def cmd_init (L1511), init_cmd argparse block (L3240-3261)

The slice is 3.6x larger in tokens but covers ~5x more semantically distinct entry points. On a per-token basis the slice's information density is therefore comparable, while its agent utility is strictly higher (missing `cmd_init` is a fatal omission for a task that asks about `init`).

### Predicate `f(n)` is the load-bearing knob

Naive `f(n) = True` (every name on a seed line) over-includes: in cli.py it pulled in `Path`, `argparse`, `Exception` etc., yielding 78 candidate ranges and 380 lines pre-cap. The tight predicate

    f(n) = (k.lower() in n.lower() for any k in K)
        OR (n is a function/class name in the file whose name contains a keyword)

cuts to 12 ranges, 50 lines for cli.py / "init" - a 4-name set: `cmd_init`, `init_cmd`, `initial`, `initial_changes`. This is the **lightweight slicing approximation** the brief asks for. Net cost: one AST walk plus one Counter scan over seed lines.

## Concrete proposal for Redcon

Add a fourth strategy tier between `slice` (already exists, uses language_chunks symbol extraction) and `snippet` (keyword window). Call it `slice-dataflow` or - cleaner - upgrade the existing fallback inside `select_language_aware_chunks` so that when keywords are non-empty, candidate scoring includes data-flow reachability, not just `_keyword_hits_for_candidate` lexical match.

### Files touched (additive only)

  - `redcon/compressors/language_chunks.py` (production, **DO NOT modify in this V18 deliverable**) - sketch shows where the new helper drops in.
  - New: `redcon/compressors/_dataflow_slice.py` - 80 lines, intra-procedural slicer.
  - New keyword in `_chunk_reason()` output string: `"data-flow"`.
  - `redcon/config.py` toggle: `compression.enable_dataflow_slice: bool = False` (off by default; opt-in until quality harness validates).

### API sketch (drop-in, ~80 LOC)

```python
# redcon/compressors/_dataflow_slice.py
from __future__ import annotations
import ast
from collections import Counter
from dataclasses import dataclass

_GENERIC = frozenset({"int", "str", "bool", "list", "dict", "self", "cls",
                      "True", "False", "None", "print", "len", "range"})

@dataclass(frozen=True, slots=True)
class SliceRange:
    start: int  # 1-based
    end: int

def compute_dataflow_slice(
    text: str,
    keywords: tuple[str, ...],
    *,
    max_lines: int = 120,
    max_depth: int = 2,
) -> list[SliceRange] | None:
    """Lightweight intra-procedural slice. Returns None on parse failure."""
    if not keywords:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    src_lines = text.splitlines()
    seeds = [i + 1 for i, line in enumerate(src_lines)
             if any(k in line.lower() for k in keywords)]
    if not seeds:
        return None

    # Names of interest: those whose identifier text contains a keyword,
    # plus function/class names containing a keyword (catches forward-referenced
    # definitions even if seed lines don't yet name them).
    interest: set[str] = set()
    for sl in seeds:
        try:
            mod = ast.parse(src_lines[sl - 1].strip())
        except SyntaxError:
            continue
        for n in ast.walk(mod):
            if isinstance(n, ast.Name) and any(k in n.id.lower() for k in keywords):
                if n.id not in _GENERIC and len(n.id) >= 3:
                    interest.add(n.id)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if any(k in n.name.lower() for k in keywords):
                interest.add(n.name)

    # Collect ranges: seed lines (+/- 1), all binding/use sites of `interest`.
    ranges: set[tuple[int, int]] = set()
    for sl in seeds:
        ranges.add((max(1, sl - 1), min(len(src_lines), sl + 1)))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in interest:
            start = min((d.lineno for d in n.decorator_list), default=n.lineno)
            end = (n.body[0].lineno - 1) if n.body else n.lineno
            ranges.add((start, end))
            if n.body:
                ranges.add((n.body[0].lineno, n.body[0].lineno))
        elif isinstance(n, ast.ClassDef) and n.name in interest:
            start = min((d.lineno for d in n.decorator_list), default=n.lineno)
            end = (n.body[0].lineno - 1) if n.body else n.lineno
            ranges.add((start, end))
        elif isinstance(n, ast.Name) and n.id in interest:
            ranges.add((n.lineno, n.lineno))

    # Coalesce contiguous, then cap by seed-density.
    merged: list[list[int]] = []
    for s, e in sorted(ranges):
        if merged and s <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    total = sum(e - s + 1 for s, e in merged)
    if total > max_lines:
        seed_set = set(seeds)
        scored = sorted(
            ((-sum(1 for sl in seed_set if s <= sl <= e), s, e) for s, e in merged)
        )
        kept: list[tuple[int, int]] = []
        used = 0
        for _, s, e in scored:
            length = e - s + 1
            if used + length > max_lines:
                continue
            kept.append((s, e))
            used += length
        merged = sorted(([s, e] for s, e in kept))
    return [SliceRange(s, e) for s, e in merged]
```

### Hook into `language_chunks.py`

Inside `select_language_aware_chunks`, after `candidates` is built, if `enable_dataflow_slice and language == "python" and keywords`:

  1. call `compute_dataflow_slice(text, tuple(keywords))`.
  2. if it returns ranges, convert them to `_Candidate(kind="data-flow", ...)` with a high `_KIND_WEIGHTS["data-flow"] = 1.95` so they outscore plain symbol candidates with no keyword hit but tie with hit-laden symbol candidates.
  3. let `_select_candidates` enforce the line budget and overlap pruning (already supports arbitrary kinds).

Net change to language_chunks.py: ~15 lines (a new branch + import) + the new module. Determinism: AST walk is ordered, sets are sorted before merge, no time-dependent state. Cache key: include `enable_dataflow_slice` flag in CompressionSettings hash (already hashed wholesale per cache key).

### Cost in compute

`ast.parse` on `redcon/cli.py` (3485 lines): 23.4 ms avg over 20 runs (verified on this machine). The slice walk is one extra `ast.walk` pass over the same tree, ~5-8 ms. Total ~30 ms per Python file. For a `redcon plan` that touches 30 files, that's ~1 s added. Mitigation: only run on files where `relevance_score >= snippet_score_threshold` AND `extension == ".py"` AND the symbol-extraction strategy (which already does an AST walk) was already going to fire - in that case reuse the AST. Net marginal cost when piggy-backed: ~5-8 ms / file.

## Estimated impact

  - **Token reduction**: zero or slightly negative on average (slice is bigger than snippet for typical files), so this is **not** a compression-floor win. The win is **precision per token** - the agent gets the *right* lines, not more lines.
  - **Selection-accuracy proxy**: on cli.py / "init" the slice covers `def cmd_init` and the argparse `init_cmd` block, both of which the snippet misses. If we measure "fraction of agent-actionable definitions present in the compressed output" across a labelled fixture set, this should improve from ~50% (snippet) to ~85% (slice).
  - **Affects**: file-side compression only (`representations.py` -> `language_chunks.py`). Does not touch any command-side compressor or scorer. Does not affect cache key determinism (additive flag, hashed).
  - **Latency**: +5-8 ms / Python file when AST is reused; +30 ms when it isn't. Cold-start budget is unaffected (lazy import).

## Implementation cost

  - ~80 LOC for `_dataflow_slice.py`, ~15 LOC integration in `language_chunks.py`, ~5 LOC new config knob in `config.py`.
  - 3 unit tests: deterministic output (run twice, byte-identical), name-predicate scoping (asserts `cmd_init` is captured for keyword `init`), pathological-file tolerance (file with 1000 hits should not blow the line budget).
  - No new runtime deps. `ast` is stdlib. No tokenizer changes.
  - Risks to determinism: low. Slice ranges are sorted before merging; `set` iteration is bypassed by `sorted(ranges)` calls.
  - Risks to must-preserve patterns: zero. Patterns are command-side; this is file-side.
  - Risk to cold-start: zero if the new module is imported lazily inside `select_language_aware_chunks` behind the config flag.

## Disqualifiers / why this might be wrong

  1. **Symbol-extraction (`slice` strategy) already does most of this**. The existing `_python_candidates` walker scores `function`/`class` candidates with `keyword_hits_for_candidate(...)` based on whether the keyword text appears anywhere in the function body. So if `cmd_init`'s body contains the literal "init" (it does), symbol extraction *would* pick it - but only if the relevance score crosses `snippet_score_threshold` (2.5) AND the symbol scoring tie-breaker doesn't give precedence to other symbols. In practice, on cli.py this often picks the snippet path because it's a 3500-line file and the symbol-budget caps short. The slice's win is NOT a new dimension; it's a tighter implementation of "find the function whose name matches the keyword" - which was always achievable by raising `snippet_score_threshold` or `snippet_total_line_limit`. The data-flow framing oversells what is, in practice, "include any def whose name contains a keyword". This is a 5-line patch to `_python_candidates`, not an 80-line slicer. **This is the strongest disqualifier**.
  2. **Inter-procedural reach is the expensive part and we explicitly skip it**. Real Weiser slicing follows call edges into other files; that's where the agent-task power lives. Intra-procedural single-file slicing is a name-shadowing-aware grep with extra steps. The result is similar to the existing keyword-aware symbol selector.
  3. **Token cost is monotone larger**. Snippet is 144 tokens; slice is 514 tokens on the same task. Under a tight token budget the slice gets truncated and loses its precision advantage. The "selection-accuracy" claim only holds when the snippet budget is the binding constraint, which is not the typical case for `redcon pack` runs that allocate per-file lines proportional to score.
  4. **Filter predicate `f(n)` is brittle**. Right now it triggers only on names whose identifier *contains* the keyword. For tasks where keywords are abstract concepts (e.g. "concurrency", "leak"), no identifier may match; the slice degenerates to seed-lines-only and is no better than the snippet. The proposal needs a fallback.
  5. **AST parse is non-trivial cost**. 23 ms / file on the 3500-line stress test; multiply by 30 files in a typical `plan` and the budget breaks. The "reuse AST from symbol extraction" mitigation requires a refactor of `language_chunks.py` to thread the parsed tree through the candidate builders, which is a ~50-line surgery and the brief says "do NOT modify production source" - so we cannot prototype the optimisation that makes this affordable.
  6. **Already-done in spirit**: BASELINE.md item "import-graph signals" + `relevance.py`'s `_path_tokens` + `_score_candidate`'s `keyword_hits_for_candidate` + the existing `slice` strategy together approximate what V18 calls a "data-flow slice", but at the file level rather than line level. The line-level refinement is real but bounded.

## Verdict

  - Novelty: **low**. Weiser slicing is 1981; intra-procedural single-file slicing on Python AST is a college exercise. The Redcon-specific framing (slice as snippet replacement) is novel only in that nothing in BASELINE.md does line-level def-use closure today; everything is symbol-level or path-level.
  - Feasibility: **high**. ~100 LOC, no deps, deterministic, off by default behind a config flag. Quality harness can validate equivalent-or-better keyword recall on the cli.py test corpus.
  - Estimated speed of prototype: **half a day** for the slicer + 1 day for AST-reuse refactor + 1 day for fixture authoring and quality-harness wiring. ~2.5 person-days.
  - Recommend prototype: **conditional-on-X**. Worth prototyping IF a labelled fixture set ("for task T on file F, the following N lines must appear in the compressed output") shows >=15% absolute recall improvement over snippet at equal token budget on Python-heavy repos. If recall improves only on a single contrived example like cli.py + "init", this is a footgun: bigger output, marginally better selection, and another knob to tune. The cleaner first-step is disqualifier #1's 5-line patch: in `_python_candidates`, when keywords match the def's *name* (not just its body), bump `_KIND_WEIGHTS["function"]` by `+1.5`. That captures the cli.py / "init" win at zero new code paths and zero new latency. If that patch closes 80% of the gap, V18 is unjustified.
