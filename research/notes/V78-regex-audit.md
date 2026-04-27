# V78: Pre-compiled regex globals audit - find re.compile misses

## Hypothesis

Across `redcon/`, the codebase is already disciplined about hoisting
patterns: 131 module-level `re.compile(...)` constants vs. only ~17
inline `re.<method>(literal_pattern, ...)` call sites. The regex hot path
is therefore *not* dominated by missed pre-compilations; CPython's
internal compile cache (`re._cache`, CACHE_MAX = 512 entries since 3.7)
absorbs almost all the steady-state cost. However, there is one
**systemic miss that is invisible to a naive grep**: every compressor's
`verify_must_preserve(text, patterns, original)` call (`base.py:60-73`)
runs `re.compile(pat, re.MULTILINE)` *inside* a per-pattern loop, on
every invocation. Patterns supplied via `compressor.must_preserve_patterns`
are sometimes static literals and sometimes dynamically built from path
lists / test names. The static slice is recompiled on every call, hits
`re._cache` only by accident, and pays a dispatch on every match. The
predicted impact of fixing the inline misses *plus* memoising
`verify_must_preserve` is **0.05-0.15 ms shaved off warm parse latency
per compressor invocation** (5-12% on grep at 1.32 ms warm), no token
delta. Adding a CI lint rule blocks future regressions.

## Theoretical basis

Per-call cost of `re.<method>(literal, s)` decomposes as:

```
   t_call = t_lookup + t_compile_or_hit + t_match
```

- `t_lookup`: dict lookup in `re._cache` keyed on `(type, pat, flags)`,
  ~150 ns on CPython 3.11 (uses an LRU-bounded dict).
- `t_compile_or_hit`: cache hit costs effectively zero. Cache miss
  costs `sre_parse.parse` + `sre_compile.compile`, **~10-50 us** for a
  small literal pattern (dominated by Python-level parse code).
- `t_match`: actual `_sre.SRE_Pattern.match/search`, ~5-50 ns/char.

For a hoisted module-level constant the per-call cost becomes:

```
   t_call_hoisted = t_match
```

so the saving per call is roughly `t_lookup + t_compile_or_hit`. With a
pure cache hit (the common case once warm) that's the ~150 ns lookup +
~50-100 ns Python-side dispatch detour through `re.match`'s thin
wrapper. With a cache miss it's ~10-50 us. CACHE_MAX is 512 entries,
purged on overflow; in a long-running MCP server with thousands of
distinct patterns (e.g. dynamically-built `must_preserve` patterns
embedding paths/names), the cache *can* be evicted, recreating misses.

Conservative back-of-envelope for redcon, 11 compressors, average
~5 must_preserve patterns per call:

```
   per-call inline-regex overhead     = N_inline * (lookup + dispatch)
                                      ~ 5 * 250 ns                  =  1.25 us
   verify_must_preserve recompile     = K_static * t_compile_hit
                                      ~ 3 * 200 ns + 2 * 12 us     ~ 24.6 us
   total inline cost per call         ~ 26 us  ~=  0.026 ms
```

Times 11 compressors, but each call pays for one compressor: per-call
saving is ~26 us. Against 1.32 ms warm grep that is **~2%**. Across the
whole quality-harness fuzz run (`ULTRA + COMPACT + VERBOSE` x 4
robustness scenarios = 12 calls per compressor x 11 compressors = 132
calls) the saving is ~3.4 ms. Modest. **The interesting tail is the
cache-miss case under sustained agent traffic with dynamic patterns:**
if `must_preserve_patterns` for grep contains 50 path-literals, every
fresh repo blows out `re._cache`, costing 50 * 12 us = 600 us per call.
Memoising compiled patterns inside the compressor instance moves that
to once-per-process.

## Concrete proposal for Redcon

Three independent, non-overlapping changes.

**Change 1: hoist the inline literals.** Mechanical rewrite.

Files and exact sites (audit complete):

| File | Line | Current | Hoist to |
|---|---|---|---|
| `redcon/cmd/compressors/kubectl_compressor.py` | 124 | `re.split(r"\s{2,}", ...)` | `_COL_SPLIT_RE = re.compile(r"\s{2,}")` |
| `redcon/compressors/symbols.py` | 680, 681, 682, 790, 791, 792 | three `re.sub` patterns, each duplicated in two functions | `_SIG_OPEN_PAREN_RE`, `_SIG_CLOSE_PAREN_RE`, `_SIG_COMMA_RE` (single set, reused) |
| `redcon/symbols/tree_sitter.py` | 546, 566, 574, 582 | `re.search`/`re.match` with literal patterns plus a redundant `import re` inside each function | hoist to module top, drop the local imports |
| `redcon/control_plane/server.py` | 80, 87, 94, 101, 108 | five `re.fullmatch` route patterns | one tuple `_ROUTES = ((compiled, handler), ...)` or five named globals |

Total: 17 sites, 4 files, ~30-line refactor.

**Change 2: cache-warm `verify_must_preserve`.** The function compiles
inside the loop. Keep the API but memoize:

```python
# redcon/cmd/compressors/base.py
import re, functools

@functools.lru_cache(maxsize=4096)
def _compile_preserve(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.MULTILINE)

def verify_must_preserve(
    text: str, patterns: tuple[str, ...], original: str
) -> bool:
    for pat in patterns:
        regex = _compile_preserve(pat)
        if regex.search(original) and not regex.search(text):
            return False
    return True
```

Determinism preserved (LRU eviction order is irrelevant - only the
compiled value matters, identical for same input). Memory is bounded by
maxsize. Beats `re._cache` because (a) we control eviction policy,
(b) it never competes with patterns from elsewhere in the program.

**Change 3: CI lint rule.** Add a single ruff/flake8 plugin or a
~20-line pre-commit script:

```python
# tools/lint_re_inline.py
import ast, sys, pathlib

INLINE = {"match", "search", "findall", "finditer", "fullmatch", "sub", "split"}

def offenders(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.func.attr in INLINE
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)):
            out.append((node.lineno, ast.unparse(node)))
    return out
```

Allowlist a handful of known one-off sites (control_plane router) via
a `# noqa: REINLINE` marker. Refuse new ones.

## Estimated impact

- **Token reduction**: 0 absolute pp. This is a perf vector, not a
  compression vector. Output bytes are unchanged.
- **Latency**:
  - Warm parse: -0.02 to -0.15 ms per compressor call on cl100k-typical
    fixtures, dominated by `verify_must_preserve` memoisation. Grep at
    1.32 ms warm goes to ~1.25-1.30 ms (~2-5%).
  - Cold start: neutral (the symbols.py / tree_sitter.py module-load
    paths gain 6 small `re.compile` calls, ~30 us total). The
    redundant `import re` inside 4 tree_sitter helpers is removed,
    which actually *saves* a dict lookup per first-call.
  - Sustained agent server: cache-miss cliff (50-pattern grep, fresh
    repo) goes from ~600 us per call to ~5 us. Real win lives here.
- **Affects**: all 11 compressors via shared `base.verify_must_preserve`;
  `redcon/compressors/symbols.py` (file-side symbol extraction);
  `redcon/symbols/tree_sitter.py` (repo-map import extraction);
  `redcon/control_plane/server.py` (irrelevant to agent path); cache
  layers untouched; quality harness untouched.

## Implementation cost

- **LOC**: ~30 lines moved, ~10 lines added (lru_cache wrapper +
  globals), ~25 lines for the lint script. Total ~65 lines.
- **New runtime deps**: none. `functools.lru_cache` is stdlib. The
  optional lint plugin is dev-only.
- **Risks to determinism**: zero. Compiled regex objects are
  referentially transparent for fixed pattern strings; cache eviction
  cannot change observable output.
- **Risks to robustness**: minimal. `lru_cache` has been bulletproof
  for >10 years. Bounded maxsize prevents unbounded memory growth on
  pathological dynamic-pattern callers.
- **Risks to must-preserve**: none - the verification logic is
  identical, only the compilation moves.

## Disqualifiers / why this might be wrong

1. **CPython's `re._cache` already does this.** The standard library
   caches the last 512 compiled patterns keyed on `(type, pattern, flags)`.
   For static literal patterns under 512 distinct strings, the win is
   ~150 ns per call - in the noise vs. 1+ ms parse times. Counter:
   the dynamic must_preserve patterns *can* exceed 512 in long-running
   servers with many repos; that case dominates the saving.
2. **V72 (SIMD regex) supersedes most of this.** If hyperscan-class
   matching lands, all per-line regex dispatch goes through one DFA
   anyway; pre-compilation savings collapse to noise. Counter: V72
   regresses cold-start by ~50 ms (BASELINE constraint #5) and is
   conditional. V78 is unconditional and tiny.
3. **Mechanical refactors risk readability damage.** Hoisting six
   `re.sub` calls in `_collapse_multiline_py_signatures` /
   `_condense_class_body` adds three globals at module top whose
   purpose is only clear at the call site. Counter: name them
   semantically (`_SIG_OPEN_PAREN_RE` etc.) and add a one-line
   comment.
4. **Cold-start budget.** Adding 6 module-level `re.compile` calls in
   `symbols.py` / `tree_sitter.py` runs at import. Each compile is
   ~10-50 us; 6 patterns total ~120 us added to cold start. BASELINE
   #5 forbids cold-start regressions but this is below measurement
   noise floor (cold-start budget is in the tens of ms).
5. **Hidden dynamic-pattern call sites I missed.** The grep counts
   `re.compile(rf"...{var}...")` as a literal because the leading
   `r-string` looks literal. A second sweep with AST analysis (rather
   than grep) is required before the lint rule lands.

## Verdict

- **Novelty**: low. Standard Python perf hygiene. The interesting bit
  is `verify_must_preserve` memoisation, which is mildly novel because
  it specifically targets the harness path used by every compressor.
- **Feasibility**: high. Mechanical, mostly mechanical refactor, no
  new deps, deterministic preservation trivially provable.
- **Estimated speed of prototype**: 1-2 hours including the lint rule.
- **Recommend prototype**: conditional-on-X, where X is "bundle with
  another perf vector to amortise the review cost". Standalone, the
  ~2-5% warm-parse win is below the BASELINE #5 breakthrough bar.
  Worth shipping as a hygiene PR alongside V72 (SIMD) or V79
  (compile-time parsers), where the lint scaffolding becomes
  load-bearing for the larger change. Do **not** ship as a standalone
  breakthrough claim.
