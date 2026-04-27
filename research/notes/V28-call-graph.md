# V28: Call-graph-conditioned scoring - reachability from keyword-matching files

## Hypothesis

The current import-graph scorer (`redcon/scorers/import_graph.py`) propagates
relevance over module-import edges only: A imports B implies A and B are
related. A *call* graph is strictly finer-grained: A may import a registry
module and then dispatch into B at runtime, with no static `import B` edge
from A. The vector claims that swapping (or augmenting) the import graph
with a call graph extracted lightly via `ast.Call` resolution will surface
files that the import-graph scorer misses, particularly through plugin /
registry indirections.

Concrete prediction for the task **"compress git diff"** on Redcon's own
130 Python source files: building the AST-derived call graph and propagating
keyword-matching roots over it will (a) re-rank files in non-trivial bucket
boundaries (distance <=2 vs ==3 vs >3), (b) catch indirect callees through
the compressor registry, and (c) save tokens by promoting files actually
exercised by the task while demoting files that are only structurally
adjacent.

What I measured shows the opposite: the *call* graph extracted via stock
Python `ast` is a strict subset of the import graph for this codebase
(2 call-only edges vs 99 import-only edges, see Theoretical basis below).
Most of the proposed novelty therefore evaporates on Redcon's own surface.
The vector still has a defensible narrow form: use call-graph reachability
*as an additional signal alongside* the import graph, not as a replacement.

## Theoretical basis

### Setup

Let `G_I = (V, E_I)` be the import graph (edge `(u,v)` iff file u imports a
symbol from file v) and `G_C = (V, E_C)` the static call graph (edge `(u,v)`
iff there is at least one `ast.Call` node in u whose callee resolves
statically to a definition in v). For pure Python without metaprogramming,
the inclusion `E_C subseteq E_I` holds: any statically resolvable call
goes through a name that was either imported (giving an edge in E_I) or
defined locally (no inter-file edge). Inter-file calls that are *not*
in E_I require dynamic dispatch (registry, getattr, object method on an
externally-constructed instance), and these are exactly the cases stock
`ast` cannot resolve. So in the absence of type information the only call
edges that AST can claim that imports cannot are spurious (re-export
chains, name shadowing).

Empirically on `redcon/`:

```
total .py files                          : 130
edges in import graph                    : 367
edges in static call graph (stock ast)   : 270
edges in (E_C \ E_I)  (call-only edges)  :   2
edges in (E_I \ E_C)  (import-only)      :  99
```

The two call-only edges are `redcon/cmd/pipeline.py -> redcon/cmd/history.py`
and `redcon/mcp/server.py -> redcon/mcp/tools.py`, both artifacts of my
"name globally unique -> assume edge" heuristic, not real dispatch.

### Reachability from "compress git diff" roots

Roots = files where path or any defined symbol contains `diff` or `compress`.
That keyword filter is broad (the word "compress" matches every compressor
class) so it returned **45 of 130 files**.

BFS from those 45 roots over each graph (forward edges only, max distance
10):

```
                            <=2 hops   ==3   >3   unreachable
call graph (G_C):              105       1    0       24
import graph (G_I):            113       3    0       14
```

Files whose bucket (<=2 / ==3 / >3 / unreachable) differs between the two
graphs: **12 of 130 (9.2%)**. Of those 12, **11 are demotions**
(import-graph said reachable, call-graph says unreachable). Concretely:

```
redcon/__init__.py            call=unr  import=1   (lazy re-exports, no calls)
redcon/runtime/session.py     call=unr  import=2
redcon/runtime/runtime.py     call=unr  import=2
redcon/runtime/context.py     call=unr  import=2
redcon/agents/__init__.py     call=unr  import=2
redcon/agents/adapters.py     call=unr  import=3
redcon/mcp/server.py          call=unr  import=2   (note: server CALLS into tools but stock ast misses dispatched MCP handlers)
redcon/sdk/__init__.py        call=unr  import=2
redcon/symbols/tree_sitter.py call=unr  import=3
redcon/gateway/server.py      call=unr  import=2
redcon/gateway/config.py      call=unr  import=1
```

Only one bucket flip is a *promotion*: `redcon/cmd/history.py` is unreachable
in the import graph (the heuristic missed the `from .history import ...`
line) but reaches via my synthetic call edge - i.e. it is a tooling
artifact, not a real win for call-graph scoring.

### Token impact under the proposed boost/penalty

Suppose the new scorer adds `+0.5` to files at call-distance <=2 from a root
(matching the magnitude of `graph_imported_by_relevant_bonus = 0.5` in
`ScoreSettings`) and `-0.3` to files at call-distance >3, and that 12 files
swap places between "above the budget cut" and "below". With Redcon's
default packing budget of ~6000 tokens and an average content_preview of
~200 tokens per included file, a 12-file boundary churn moves at most
12 * 200 = 2400 tokens of content, *but only the marginal swap-in / swap-out
is real*. With 11 of 12 changes being false-negative demotions, the
expected token impact is **net zero to slightly negative**: we throw out
true-positive files (`redcon/runtime/runtime.py`, `redcon/mcp/server.py`)
that actually do participate in dispatching `redcon_run -> compress git
diff`. So the realistic delivered impact on this task is ~0 helpful tokens
saved and ~5 mis-demoted files, costing the agent a follow-up read each.

### Plugin / dynamic-dispatch surface in Redcon

Files matching `registry|plugin` in their path: 6
(`redcon/cmd/registry.py`, `redcon/plugins/{registry,builtins,api,examples,__init__}.py`).
Files containing one of `getattr(`, `globals()[`, `REGISTRY[`, `register(`,
`dispatch[`: **12 / 130 (9.2%)**. Crucially `redcon/cmd/registry.py` is
the dispatch point for `compress_command -> detect_compressor -> compressor
class`; it has **0 outgoing static call edges** in my analysis (1 import
edge to `types`). So the place where call-graph theory should pay off
(the registry that selects the git-diff compressor at runtime) is
*precisely* where stock-ast call-graph extraction returns nothing useful.
The plugin pattern has effectively erased the call edges into all 11
compressor modules. To recover them you would need either (a) constant-time
lookup of `register(...)` decorator usages, or (b) a string-key map that
links the compressor's `schema = "git_diff"` constant back to the dispatch
table - both are richer machinery than "ast.Call resolution" and they
duplicate work the existing `redcon/cmd/registry.py` already does
explicitly via `_REGISTRY: dict[str, type[BaseCompressor]]`.

### Back-of-envelope (>= 3 lines)

Let n = 130 files, m_I = 367 import edges, m_C = 270 call edges. Mean
out-degree: import 2.82, call 2.08. Reachability ratio at depth 2 from a
root set R of size 45:

  reach_I(2)/n approx 113/130 = 0.869,
  reach_C(2)/n approx 105/130 = 0.808,
  delta = 0.061.

Symmetric difference of bucket assignments: 12/130 = 0.092. Of that, the
fraction that are *strict promotions* (call adds info import didn't have):
1/12 = 0.083; fraction that are *demotions* (call lost what import had):
11/12 = 0.917. Expected information gain from substituting G_C for G_I:

  IG = 1/12 * (-1) * log(p_demo/p_prom) = neg-dominated.

The positive-information case requires dynamic-dispatch *resolution*, which
stock ast cannot do. To make the call graph informative on this codebase
you need roughly: detect `register(name)` decorator -> add edge from
registry.py to decorated function's file; detect `_REGISTRY[name]` lookups
-> require the string key to match a `schema =` constant elsewhere; build
the closure. This is a string-matching analyzer, not a call-graph
extractor, and its coverage is bounded by how disciplined plugin
authors are.

## Concrete proposal for Redcon

The honest version of V28 is *augmentation*, not replacement. New file:

- `redcon/scorers/call_graph.py` (~120 LOC) - builds a sparse static call
  graph using `ast.Call` plus a registry-aware shim that recognises the
  `_REGISTRY[...] = compressor_cls` pattern in `redcon/cmd/registry.py`
  and the `@register(...)` decorator in `redcon/plugins/api.py`. Output
  shape mirrors `ImportGraph` so the relevance scorer can union both.

- `redcon/scorers/relevance.py` (~15 LOC change) - if
  `cfg.enable_call_graph_signals` is True, build the call graph alongside
  the import graph and *union* their edges before propagating seed scores.
  Add separate breakdown keys (`call_graph_promoted`, `call_graph_demoted`)
  so determinism explanations stay legible.

- `redcon/scanners/incremental.py` - **already supports content-hash
  reuse**. The call graph can be cached on a per-file basis keyed on the
  existing `content_hash` field (line 232 of incremental.py). When
  `previous_entry.size_bytes == file_size and previous_entry.mtime_ns ==
  st_mtime_ns`, the file's outgoing call edges are byte-identical and can
  be reused. Cost on a warm scan with 0 changed files: O(1) hash lookups,
  near-zero. Cost on a cold scan: ~150 ms for 130 files (one ast.parse per
  file, the bulk of the cost is already paid by the existing symbol_names
  extraction in `_build_file_record`). I would store the per-file edge set
  in a new column `call_edges_json TEXT` on the `entries` SQLite table
  (incremental.py line 269-280), defaulting to NULL so old scan-index DBs
  remain forward-compatible.

API sketch:

```python
# redcon/scorers/call_graph.py
@dataclass(slots=True)
class CallGraph:
    outgoing: dict[str, set[str]]  # path -> paths it CALLS
    incoming: dict[str, set[str]]
    registry_edges: dict[str, set[str]]  # registry.py -> impl files

def build_call_graph(files: list[FileRecord], cache: dict | None = None) -> CallGraph:
    edges: dict[str, set[str]] = defaultdict(set)
    for record in files:
        cached = cache.get(record.content_hash) if cache else None
        if cached is not None:
            edges[record.path] = set(cached)
            continue
        try:
            tree = ast.parse(Path(record.absolute_path).read_text(errors="ignore"))
        except (SyntaxError, OSError):
            continue
        # 1. ast.Call resolution (Name + Attribute(Name, attr))
        # 2. Registry shim: scan top-level for "_REGISTRY[K] = V" and
        #    "@register(K)" patterns; emit edges from this file to V's file.
        edges[record.path] = _resolve_calls(tree, ...) | _resolve_registry(tree, ...)
        if cache is not None:
            cache[record.content_hash] = edges[record.path]
    incoming: dict[str, set[str]] = defaultdict(set)
    for s, ts in edges.items():
        for t in ts:
            incoming[t].add(s)
    return CallGraph(outgoing=dict(edges), incoming=dict(incoming),
                     registry_edges=...)
```

Then in `relevance.py` (~line 156, after the import-graph block):

```python
if cfg.enable_call_graph_signals and files:
    call_graph = build_call_graph(files, cache=incremental_cache)
    seed_paths_call = {p for p, s in heuristic_scores.items()
                       if s >= cfg.graph_seed_score_threshold}
    for record in files:
        # +0.4 if reachable within 2 hops over (E_I union E_C)
        # -0.2 if at distance >3 in BOTH graphs
        # (single-graph distance >3 is not penalty-worthy: the other graph
        # may carry the relevance.)
```

Note the union; this avoids the demotion bug shown in the empirical table
above.

## Estimated impact

- **Token reduction**: in the union form, expected delta is small. 1
  promotion case (registry-mediated dispatch onto compressor files when
  the task is "compress git diff") is *already* captured because all 11
  compressors are themselves keyword roots. So the registry shim moves
  zero compressors. The only files where the union helps over the import
  graph alone are ~3-5 files that import indirectly through `__init__.py`
  re-exports. **Estimated <=1 percentage point pack-quality improvement**
  on this task. Other tasks (e.g. "audit telemetry pricing") might benefit
  more if they hit a registry the import graph misses, but Redcon's own
  dispatch surface is small enough that the gain is bounded.
- **Latency**: cold +150 ms (one extra ast.parse pass; can be folded into
  the existing parse done by `_build_file_record` if we share the AST -
  saves ~80 ms). Warm 0 ms when content hashes unchanged (cache hit).
  Cold-start budget constraint (BASELINE.md line 63): a 150 ms regression
  on a 250 ms cold-start is +60%; this **violates the constraint** unless
  we share the AST with symbol extraction or run the call graph lazily on
  first scoring call only.
- **Affects**: `redcon/scorers/relevance.py`, new
  `redcon/scorers/call_graph.py`, `redcon/scanners/incremental.py` adds
  one column for cache, `redcon/config.py` adds
  `enable_call_graph_signals`, `call_graph_bonus`,
  `call_graph_distance_penalty`. No compressor changes, no cache key
  changes, no MCP surface changes.

## Implementation cost

- Lines of code: ~120 LOC for `call_graph.py`, ~15 LOC change in
  `relevance.py`, ~30 LOC for the SQLite migration in `incremental.py`,
  ~50 LOC of tests (golden call-graph for the redcon/cmd/compressors
  subtree). Total ~215 LOC. Smaller than V01 (rate-distortion), bigger
  than a one-day patch.
- New runtime deps: none. Stdlib `ast`, stdlib `sqlite3`. Determinism: ast
  parse is deterministic; bucket assignment is deterministic given a
  stable file ordering (already guaranteed by `records.sort` in
  incremental.py line 697). Same input -> same edges -> same bucket ->
  same score.
- Risks:
  - **False negatives on dynamic dispatch (the central risk).** Measured
    above: 9.2% of files use dispatch markers; `redcon/cmd/registry.py`
    statically resolves to 0 outgoing edges. The registry shim catches
    the explicit `_REGISTRY[name] = cls` pattern but not, e.g., the
    `redcon_run` MCP handler, which is dispatched by JSON-RPC method
    name. So even with the shim, ~50% of the dispatch surface stays
    invisible.
  - **Cold-start regression.** As noted above, a separate ast pass is
    a ~60% relative cold-start hit unless folded into existing parsing.
  - **Cache-key contract.** `incremental.py` line 169 fingerprints
    `(include_globs, ignore_globs, max_file_size_bytes, preview_chars,
    ignore_dirs, binary_extensions, internal_paths)`. Adding a call-graph
    column doesn't change the fingerprint; old indexes upgrade with
    NULL columns. Safe.
  - **Determinism under name collisions.** If two files define
    `compress(...)`, my analysis script counted that as ambiguous and
    dropped the edge. A real implementation must do the same and emit
    a deterministic explanation ("ambiguous_callee:compress"), or it
    would re-rank differently across runs depending on dict iteration.

## Disqualifiers / why this might be wrong

1. **Empirically the call graph is a subset of the import graph on this
   codebase.** 270 vs 367 edges; only 2 call-only edges and both are
   artifacts. The vector's premise ("call graph is finer than import
   graph") is true in the abstract but false in measure for Redcon
   without metaprogramming-aware analysis. The genuine wins require a
   registry shim, which is no longer "lightweight static analysis via
   ast" - it is a domain-specific pattern matcher whose maintenance cost
   tracks the codebase's plugin conventions.

2. **The 11 compressors are already roots by keyword.** For the chosen
   task ("compress git diff") every compressor file matches the keyword
   "compress" and is already a root with score >= seed threshold. There
   is no reachability bonus to be earned that the keyword scorer hasn't
   already paid. The vector's ideal example (registry chooses git diff
   compressor at runtime, the call graph promotes it) does not apply
   because the compressor was already promoted directly. A different
   task ("audit kubectl error parsing") would have a smaller root set
   and might benefit more, but on the asked-for task, no.

3. **It is partially already implemented as the import-graph scorer.**
   `redcon/scorers/import_graph.py` already does seed -> 1-hop boost.
   Extending the propagation depth to 2-3 hops is one constant change
   in `ScoreSettings`, no call graph required, and the empirical bucket
   data above shows that depth-3 already captures everything depth-2 of
   the call graph can give plus more. The vector reduces to "deepen the
   import-graph BFS", which is BASELINE.md territory and not novel.

## Verdict

- **Novelty: low**. The technique is textbook (Doxygen has done static
  call-graph extraction since the 1990s; pyan and similar tools predate
  this proposal). Worse, on Redcon's own surface the call graph carries
  *less* information than the import graph (E_C subset E_I, 2 vs 99
  asymmetric edges). The only genuinely new contribution would be the
  registry-shim, and that pattern is bespoke per plugin convention.
- **Feasibility: medium**. The mechanical implementation is ~200 LOC
  with no new deps and the incremental cache piggybacks cleanly on
  content_hash. But the cold-start regression unless AST is shared
  with `_build_file_record` is non-trivial, and the determinism of
  ambiguous-callee handling has to be designed carefully.
- **Estimated speed of prototype: 3-4 days**. 1 day for `call_graph.py`,
  1 day to share the AST pass with symbol extraction so cold-start
  doesn't regress, 1 day for incremental cache plumbing + migration,
  1 day for golden tests and the registry shim.
- **Recommend prototype: no**, in its asked-for form. The empirical
  measurement on Redcon's own code shows the call graph is a strict
  *subset* of the import graph (270 of 367 edges, 2 call-only) and
  re-ranks only 12/130 files mostly by *demoting* true positives. The
  vector's expected gain hinges on dynamic-dispatch resolution, which
  stock `ast` cannot do. Conditional yes if scoped to "registry-shim
  edges as an additive signal" and limited to repos with explicit
  `@register` plus string-key registries; even there, deepening
  import-graph BFS to 2-3 hops is a cheaper substitute for most of
  the claimed benefit.
