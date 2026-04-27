# V14: Type-driven literal collapsing - replace verbose value literals with `:T` markers when T determines the value structure

## Hypothesis

Many literal values in production source code are *type tokens in
disguise*: their existence and shape carry information, but the exact
bits do not, given the surrounding type context. e.g. in
`prefer_compact_output: bool = False` the agent already knows the field
is a `bool`; the literal `False` is a default-value choice, not a
load-bearing fact for tasks that do not tune defaults. Replacing such
literals with `:T` markers when the file's relevance score is below a
threshold should shave tokens at no comprehension cost on most tasks.

This vector predicts a measurable, repo-wide compaction (claim: 2-5%
reduction in source-file tokens fed to the agent in `redcon plan`/
`redcon pack` for low-relevance files) while preserving all method
signatures, all type annotations, and all docstrings. The hypothesis is
falsifiable: cl100k tokenizes single-digit ints, `True`/`False`/`None`
in 1 token each, and the proposed marker `:int` is also 1 token, so the
*best case is break-even on those literals* and the variant only wins
on multi-token literals (floats with decimals, enum dotted paths,
strings, tuples, dicts).

## Theoretical basis

### 1. Token-cost arithmetic for cl100k

I measured the marker cost directly with `tiktoken.get_encoding("cl100k_base")`:

    ":int"   -> 1 token            "0", "1", "30", "3"     -> 1 token each
    ":str"   -> 1 token            "True", "False", "None" -> 1 token each
    ":T"     -> 1 token            "0.15"   -> 3 tokens
    ":list"  -> 1 token            "1.0"    -> 3 tokens
    ":float" -> 2 tokens           "'exponential'"  -> 3 tokens
    ":bool"  -> 2 tokens           "(1, 2, 3)"      -> 9 tokens
    ":Enum"  -> 2 tokens           "{'a': 1, 'b': 2}" -> 12 tokens
    ":dict"  -> 2 tokens           "CompressionLevel.ULTRA" -> 5 tokens
    ":tuple" -> 2 tokens

Token-saving condition for literal L of type T: `tok(L) > tok(":"+T)`.
Counts above show:

  - **Loss**: `False`/`True`/`None`/single-digit ints replaced by
    `:bool`/`:int`. Replacing 1-token `False` with 2-token `:bool`
    spends an extra token per literal.
  - **Break-even**: any single-digit `int` -> `:int` (1->1).
  - **Win**: multi-digit ints with non-tokenizer-friendly decimals
    (3 tokens for `0.15`) -> `:float` (2 tokens), enum dotted paths
    (5 tokens for `CompressionLevel.ULTRA`) -> `:Enum` (2 tokens),
    string literals, tuple/list/dict literals.

### 2. Repo-level literal share

Scanning all 130 `*.py` files under `redcon/` with a regex for
`NAME(:TYPE)? = LITERAL` (where LITERAL is a number/bool/None/string
or a one-level tuple/list/dict) and tokenising each captured literal
with cl100k:

    files scanned                     : 130
    total cl100k tokens (production)  : 316,968
    constant-assignment lines matched : 1,204
    tokens locked in those literals   : 3,314
    literal-token share of repo       : 1.05 %

A maximally-aggressive collapse (every matched literal replaced with
a 2-token marker) gives an upper bound saving of:

    saving_max = 3314 - 2 * 1204 = 906 tokens
    repo-wide reduction          = 0.29 %

If we use the *best* marker (1-token `:T`) for every literal:

    saving_max = 3314 - 1 * 1204 = 2110 tokens
    repo-wide reduction          = 0.67 %

These are **upper bounds**. Real saving is below them because half the
literals are already 1-token `False`/`True`/`None`/single-digit ints
where the marker is the same length or longer.

### 3. Per-file derivation: budget.py

Manually applied type-collapse to
`/Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/budget.py`
(96 lines, 3,392 chars, 792 cl100k tokens), preserving all docstrings,
method signatures, and parameter type annotations. The collapsed
variant kept every `def`/`@dataclass`/`class` signature byte-identical;
only the right-hand-side defaults and the `_LEVEL_RANK` dict values
were replaced.

Result:

    original tokens (cl100k): 792
    collapsed tokens (cl100k): 813
    delta                    : +21 (-2.65 % "savings", i.e. inflation)

The collapse made the file *worse*. Per-line breakdown of the swaps:

    11 ->  8  | quality_floor: CompressionLevel = CompressionLevel.ULTRA  -> :Enum   (-3)
     8 ->  9  | prefer_compact_output: bool = False                       -> :bool   (+1)
     7 ->  8  | semantic_fallback: bool = False                           -> :bool   (+1)
     7 ->  7  | _VERBOSE_RATIO = 1.0                                      -> :float  (+0)
     8 ->  8  | _COMPACT_RATIO = 0.15                                     -> :float  (+0)
     9 ->  9  | _BUDGET_SHARE = 0.30                                      -> :float  (+0)
    10 -> 10  | CompressionLevel.ULTRA: 0,                                -> :int    (+0)
     9 ->  9  | CompressionLevel.COMPACT: 1,                              -> :int    (+0)
     9 ->  9  | CompressionLevel.VERBOSE: 2,                              -> :int    (+0)

Net swap savings on the literal sites alone: -1 token. The remaining
+22-token inflation comes from the type annotations I had to *add* to
formerly-untyped module constants (`_VERBOSE_RATIO: float = :float`)
to make `:T` parsable as a typed-default form. Without those
annotations the collapsed file is a syntax-noisy hybrid.

If we drop the requirement to add new annotations and just rewrite
in-place (`_VERBOSE_RATIO = :float`) we still net 0 to -1 tokens on
this file - the file is too small and the ratios already have
1-token-friendly forms.

### 4. Why repo-wide reduction is bounded by ~0.3-0.7 %

The repo's literal-token share is ~1 %. Even if we recovered 100 %
of those tokens (impossible: markers always cost >= 1 token), the cap
is 1 %. Realistic capture is ~30-60 % of that pool because half the
literals are already minimally encoded by cl100k. **Floor: this
technique has a hard ceiling at < 1 % repo-wide.**

By contrast, the file-side packer in `redcon/scorers/` is currently
deciding *which whole files to include*, with per-decision swings of
hundreds to thousands of tokens. A 0.3 % shave on whatever it picks is
in the noise.

## Concrete proposal for Redcon

The honest proposal is **do not implement V14 as a compressor**.
Below is the minimal stub that would deliver the technique if a
specific use case justified it; included so future researchers can
verify by re-running.

### A. Hypothetical: `redcon/scorers/type_collapse.py` (new, ~120 lines)

API:

```python
def collapse_low_relevance_file(
    src: str,
    relevance: float,
    *,
    threshold: float = 0.20,
    preserve_signatures: bool = True,
) -> str:
    """
    Rewrite NAME = LITERAL lines into NAME = :T form when relevance < threshold.

    Pre-conditions:
        - Source must parse with `ast.parse` (verifies signatures intact).
        - Only assignments at module-level or class body are touched;
          assignments inside `def` bodies are left alone (they often
          encode load-bearing constants for the algorithm).
    """
    if relevance >= threshold:
        return src
    tree = ast.parse(src)
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign) and _is_module_or_class_scope(node, tree):
            lit = _extract_literal_value(node)
            if lit is None:
                continue
            marker = _marker_for(lit, getattr(node, "annotation", None))
            if _tok_cost(marker) >= _tok_cost(ast.unparse(lit)):
                continue   # do not regress
            edits.append((lit.col_offset, lit.end_col_offset, marker))
    return _apply_edits(src, edits)


def _marker_for(lit: ast.expr, ann: ast.expr | None) -> str:
    if ann is not None:
        return f":{ast.unparse(ann)}"
    return ":" + type(ast.literal_eval(lit)).__name__   # bool, int, float, str, list, tuple, dict
```

Pipeline integration would happen in `redcon/engine.py` after
relevance scoring but before token budgeting. The threshold `0.20`
mirrors the lower-quartile of file-relevance scores in the existing
heuristic; would need calibration.

### B. What the agent must understand

Because `:T` is not Python syntax, the rewrite produces strings that
will not parse with `ast.parse`. This is acceptable *only* if the
output stream is clearly demarcated as agent-context (not source) and
the file is never re-imported. Concretely: the agent prompt would
need to tell the model "tokens of the form `:int`, `:bool`, `:Enum`,
... are placeholders for type-determined default values; if you need
the actual value, request the file at full fidelity". Failure mode:
the agent reads `_BUDGET_SHARE = :float` and proposes a code change
that pastes that literal back as source, breaking the program.

This is similar to how snippet/symbol extraction already produces
non-runnable views, so the framing is not novel - but every existing
view is at least syntactically valid Python (truncated, summarised,
or symbol-extracted, never with synthesised non-Python tokens). V14
breaks that invariant and that is its biggest cost.

### C. Method-signature preservation

`def select_level(raw_tokens: int, hint: BudgetHint) -> CompressionLevel:`
must stay byte-identical. The AST walk above only touches `Assign` and
`AnnAssign` nodes; `FunctionDef.args.defaults` is **not** touched in
the proposal. If we did touch them (e.g. `quality_floor:
CompressionLevel = :Enum` in a function signature) we would mislead
the agent about call semantics. Explicitly out of scope.

## Estimated impact

- **Token reduction (file-side, low-relevance subset)**: < 1 % of
  total source-file tokens, given the 1.05 % literal-token share of
  the codebase and the marker-cost arithmetic above.
- **Token reduction (any individual file like `budget.py`)**: -2.65 %
  to 0 %. The technique is *neutral-to-negative* on small files
  because docstring tokens dominate and `:T` markers cost as much as
  cl100k-friendly literals.
- **Token reduction (command-side)**: zero. V14 operates on source
  files, not on command output, which is where Redcon's headline
  reductions (97 % git diff, 73 % pytest) live.
- **Latency**: a per-file `ast.parse` + `ast.walk` adds ~1-3 ms per
  file. For a 200-file pack run that is ~400 ms cold, ~zero warm
  (results cached in plan run). Marginal.
- **Affects**: only `redcon/engine.py`'s file-side pipeline. Does not
  touch `redcon/cmd/` (compressors, pipeline, cache) at all.

## Implementation cost

- ~120 lines for `type_collapse.py` (AST walk, marker lookup,
  edit-apply).
- ~40 lines wiring into `engine.py` after relevance scoring.
- ~40 lines test: roundtrip `ast.parse(src)` succeeds on inputs;
  collapsed output is *deterministic* (same input -> same output);
  signatures byte-identical.
- New runtime deps: none. `ast` is stdlib.
- Risks to determinism: low (AST traversal is deterministic; ordering
  enforced by source position).
- Risks to robustness: medium. `ast.parse` failing on weird syntax
  must short-circuit to identity. Already standard practice in
  `redcon.scorers.import_graph` per BASELINE.
- Risk to "must-preserve" guarantees: not applicable - V14 is
  file-side, not command-side, so the quality harness does not gate
  it. But a parallel guarantee should be: every method signature in
  the input is present byte-identical in the output. Easy to check
  with a regex over `def `/`class ` lines.

## Disqualifiers / why this might be wrong

1. **The arithmetic kills it on small literals**. cl100k already
   tokenises `True`, `False`, `None`, single-digit ints, and short
   identifiers in 1 token each. A 1-token `:int` marker is the best
   case; against a 1-token literal, it is exactly break-even. Net
   savings come only from multi-token literals (decimals, dotted
   enum paths, strings, dict/tuple/list literals), which form less
   than half of the literal pool in this codebase.

2. **Repo-wide cap is < 1 %**. Even an oracle collapser that
   correctly identifies 100 % of type-determined literals and uses
   the cheapest marker for each is bounded by the 1.05 % literal
   share of total source tokens. That is below the noise floor of
   any other file-side technique.

3. **Already done in disguise**. The file-side pipeline already has
   four compression modes per file (`full`, `snippet`, `symbol`,
   `summary`). Symbol-extraction already drops function bodies
   wholesale on low-relevance files; for those files V14's
   collapsing acts on at most a handful of module-level constants,
   and the symbol view already costs much less than the
   type-collapsed view. So V14 is dominated by an existing tier.

4. **Breaks the "valid-Python-views" invariant**. Every other view
   currently produced by Redcon is parseable as Python (or a clean
   subset thereof). V14 introduces synthetic tokens (`:int`,
   `:Enum`) that are not Python and require an agent-side legend.
   That is a non-trivial UX cost for a sub-1 % token win.

5. **Task-conditioning is the killer**. The vector statement
   correctly identifies that `:int` is wrong when the task is "tune
   the timeout". But a relevance scorer cannot reliably distinguish
   "agent wants the *value* of `_BUDGET_SHARE`" from "agent wants
   the *structure* of `select_level`". Misclassification corrupts
   correctness for sub-1 % token gain - a strictly losing trade.

6. **Method signatures contain literal defaults too**. The proposal
   excludes them explicitly to preserve call semantics, but that
   excludes a large portion of the literals in real Python code
   (e.g. `_normalise_whitespace(text: str, *, max_blank: int = 2)`).
   What is left after excluding signatures is mostly module-level
   constants and `_LEVEL_RANK`-style ranking dicts, which are *the
   most semantically load-bearing* literals in the codebase
   (knobs that operators tune), exactly the ones a relevance scorer
   should *not* be hiding.

7. **cl100k already prefers the existing form**. Several of the
   literals in `budget.py` (`0.15`, `0.30`, `1.0`, `False`,
   `CompressionLevel.ULTRA`) tokenise to between 1 and 5 tokens.
   `:Enum` saves 3 tokens on the enum case but costs +1 on every
   bool. The aggregate on the file is +21 tokens. This is not a
   tuning problem; the merge table prefers the original form on
   typical Python literals.

## Verdict

- Novelty: low. The "literal-as-type-token" framing is folklore in
  type-system papers (refinement types, dependent types) and the
  practical execution here is a syntactic substitution table.
- Feasibility: high (the AST walk is trivial), but the cost-benefit
  is upside-down.
- Estimated speed of prototype: 4-6 hours for a working type-collapse
  pass with tests; the prototype would confirm the negative result
  measured here in an additional 2-3 hours of corpus tokenisation.
- Recommend prototype: **no**.

  The empirical data is clear: cl100k merges already make most
  Python literals 1-2 tokens, so the marker form `:T` is at best
  break-even and at worst inflationary. The repo-wide ceiling is
  < 1 % token reduction with non-trivial correctness risk
  (synthetic tokens, signature-preservation invariants, agent
  confusion when the value matters). Existing file-side tiers
  (symbol-extraction, summary) already dominate this technique on
  low-relevance files, and the headline gains in Redcon live on the
  command-side pipeline where V14 has zero applicability.

  The negative result itself is the contribution: future researchers
  proposing tokenizer-aware structural rewrites of source should
  start by running the marker-cost arithmetic against cl100k. If
  the marker is not strictly shorter than the median literal it
  replaces, the technique cannot win.

## Reproduction artifact

The exact measurement script lives at `/tmp/v14_collapse.py`
(throwaway, not committed). To regenerate:

    /Users/naithai/Desktop/amogus/praca/ContextBudget/.venv/bin/python /tmp/v14_collapse.py

Reports `original_tokens=792`, `collapsed_tokens=813`,
`literal-token share of repo=1.05 %`, `estimated reduction across
repo=0.29 %`. Production source was not modified.
