# V20: Bigraph (imports x file roles) as adjacency-list delta vs prior run

## Hypothesis

Across consecutive `redcon plan` / `redcon_run` calls inside one agent
session, the **structural skeleton** of a repo - the bigraph of (file ->
imports) and (file -> role) - is overwhelmingly stable. Tasks change; the
import graph and the role classification of paths almost never do. So the
session protocol should ship the bigraph snapshot **once** at session start
and emit a **tiny adjacency-list delta** (added/removed file nodes, added/
removed import edges, role flips) on every subsequent call. The agent
holds the snapshot client-side and reconstructs the current bigraph on
demand. This compounds with file-content delta (already shipped in
`redcon/core/delta.py`): the existing delta drops re-sent text per file,
V20 drops the structural skeleton itself.

The prediction: for the second and later turns, the structural payload
(the dependency-and-role view that downstream scoring uses) drops by
>=70% at top-25 candidate scope and >=92% at top-100 candidate scope.

## Theoretical basis

A bigraph here means a graph with two node types - files F and import
targets T (which is itself a subset of F for repo-local imports) - plus
role labels R(f) drawn from a small alphabet (prod/test/docs/example/
config/generated, six values). Encoded as adjacency lists:

    G_t = ( { f : (sorted_imports(f), role(f)) } for f in F_t )

Between two scans at times t1 and t2, the symmetric edit distance is

    d(G_1, G_2) = |F_1 △ F_2|
                + sum over f in F_1 ∩ F_2 of |E_1(f) △ E_2(f)|
                + |{ f in F_1 ∩ F_2 : R_1(f) != R_2(f) }|

A standard graph-streaming result (Henzinger et al., dynamic graph
connectivity; Eppstein, "Offline algorithms for dynamic minimum spanning
tree problems") is that for graphs with churn rate `p << 1`, sending the
delta is cheaper than the snapshot whenever the per-edge encoding cost
plus a constant per-change header < average node-encoding cost times
`p * |F|`. For a Python repo, the per-file-node encoding cost dominates
(mean ~3 imports per file plus a path string), so the breakeven is
roughly `p < ~25%`.

Empirical churn rate measurement on the **Redcon repo itself** (908 .py
files, 639 import edges, with a synthetic 1/17 file-drop and 1/23 role-
flip perturbation between turn1 and turn2):

  full snapshot turn 1  : 22,359 tokens  (~89.4 KiB)
  full snapshot turn 2  : 21,091 tokens
  delta payload         : 1,270 tokens
  token reduction       : 94.0%

For the **identical-bigraph case** (same repo, different task - which is
the actual common case across `redcon plan A` then `redcon plan B`),
delta = 0 tokens, reduction = 100%. The bigraph is task-independent.

For realistic top-N candidate scoping (which is what `redcon plan`
actually ships), with task drift simulated by shifting the candidate set
by 5 ranks:

  top-25  : 389 tok full -> 109 tok delta  (72.4% reduction, 80% overlap)
  top-100 : 1,867 tok full -> 138 tok delta  (92.7% reduction, 95% overlap)
  top-250 : 5,633 tok full -> 169 tok delta  (97.0% reduction, 98% overlap)

The reduction grows with N because role/import structure for stable files
costs O(1) per file in the snapshot but 0 in the delta.

These are not LLM-prompt savings directly - the bigraph is metadata,
not part of `compressed_context`. They are savings on a **new** structural-
metadata channel that V20 proposes adding to MCP tool responses (under
`_meta.redcon.bigraph`). Today no such channel exists; the agent gets
file content but no machine-readable dependency/role structure.

## Concrete proposal for Redcon

### Files touched (NEW, no production source modified yet)

1. `redcon/runtime/session.py` (existing, gets a small extension): add

   ```python
   bigraph_snapshot_id: str | None = None
   bigraph_snapshot: dict[str, dict] | None = None  # path -> {imports, role}
   ```

   so the runtime carries the last-seen full bigraph hash.

2. `redcon/scorers/bigraph_delta.py` (new file, ~120 LOC):

   ```python
   def build_session_bigraph(files, scope_paths=None):
       g = build_import_graph(files)              # existing scorer
       roles = {r.path: classify_file_role(r.path) for r in files}
       scope = scope_paths if scope_paths is not None else {r.path for r in files}
       return {
           p: {
               "imports": sorted(g.outgoing.get(p, ()) & scope),
               "role": roles.get(p, "prod"),
           }
           for p in sorted(scope)
       }

   def diff_bigraph(prev, curr):
       added, removed, edge_adds, edge_dels, role_flips = ...
       return {"added": ..., "removed": ..., "e_add": ..., "e_del": ..., "rf": ...}

   def snapshot_id(bg):
       # Deterministic hash for cache key + agent ack.
       return blake2b(json.dumps(bg, sort_keys=True).encode(), digest_size=8).hexdigest()
   ```

3. `redcon/runtime/runtime.py`: in `prepare_context`, after pack:

   ```python
   scope_paths = set(ctx.files_included) | set(ranked_top_paths)
   curr_bg = build_session_bigraph(scanned_files, scope_paths)
   curr_id = snapshot_id(curr_bg)

   if self.session.bigraph_snapshot_id == curr_id:
       bg_payload = {"snapshot_id": curr_id, "delta": None}     # 0 bytes
   elif self.session.bigraph_snapshot is None:
       bg_payload = {"snapshot_id": curr_id, "full": curr_bg}    # first turn
   else:
       bg_payload = {
           "snapshot_id": curr_id,
           "base_id": self.session.bigraph_snapshot_id,
           "delta": diff_bigraph(self.session.bigraph_snapshot, curr_bg),
       }
   self.session.bigraph_snapshot_id = curr_id
   self.session.bigraph_snapshot = curr_bg
   ctx.metadata["bigraph"] = bg_payload
   ```

4. MCP tool result `_meta.redcon.bigraph` per the convention from commit
   `257343` (every tool already emits a `_meta.redcon` block). Adds three
   keys: `snapshot_id`, optional `full`, optional `delta`. Reuses the
   existing `_meta` envelope - no new wire format.

### Determinism

`build_import_graph` is already deterministic (sorted iteration over
`files`, set-typed edges). `classify_file_role` is a pure function with
LRU cache. Sort the bigraph by path before serialisation. snapshot_id is
a content hash. No randomness, no clock, no tiebreaks. Honours
constraint #1 in BASELINE.md.

### Cache interaction

`bigraph_snapshot_id` becomes part of the cache key for any tool that
emits a delta (otherwise a cache hit could return a delta whose `base_id`
the agent has never seen). Strict-superset key extension as required by
constraint #6.

## Estimated impact

- **Token reduction**: brand-new channel, so direct prompt-text reduction
  is 0pp. But the bigraph carries information that today is *re-derived*
  by re-sending file paths plus path-role hints scattered through the
  pack metadata. Concretely, per turn: 92.7% smaller structural-metadata
  payload at top-100 scope (138 tok delta vs 1,902 tok full snapshot).
  Across an 8-turn session the cumulative `_meta.redcon.bigraph`
  channel costs ~1,900 + 7 * 138 = 2,866 tokens vs 8 * 1,902 = 15,216
  tokens for naive re-shipping: **81% session-level metadata savings**.
- **Latency**: build_import_graph already runs every pack call; reusing
  it costs no extra scan. snapshot_id is a single blake2b hash over
  ~22 KB of JSON: ~0.1 ms warm. Cold-start unaffected (no new imports
  on the hot path - bigraph_delta module is lazy-imported only when
  agent has a session).
- **Affects**: `redcon/runtime/session.py` (state field), `redcon/runtime/
  runtime.py` (one block in prepare_context), `redcon/mcp/*` (the
  `_meta.redcon` enricher). Does not touch any `redcon/cmd/compressors/*`
  or any scorer.

## Implementation cost

- **Lines of code**: ~120 LOC new module + ~25 LOC integration glue +
  ~40 LOC tests = ~185 LOC.
- **New runtime deps**: none. blake2b is in stdlib hashlib. Honours
  "no required network / no embeddings".
- **Risks to determinism**: low. All inputs are sorted before hashing.
  Risk: if file_roles classification ever depends on filesystem mtime
  (it does not today), determinism could break. Pin classify_file_role
  as pure-by-contract.
- **Risk to robustness**: a stale `bigraph_snapshot_id` on the agent
  side (agent restart, dropped session) means the agent applies a delta
  to nothing. Mitigation: agent sends `base_id` it holds; server falls
  back to a full snapshot if `base_id` not in {None, current.prev_id}.
- **Risk to must-preserve guarantees**: zero. Bigraph is metadata, not
  inside `compressed_context`. Must-preserve patterns (BASELINE.md
  constraint #4) operate on compressor output text, not metadata. No
  COMPACT-tier regression possible.

## Disqualifiers / why this might be wrong

1. **Adoption gradient is small.** Today no MCP client *consumes* a
   bigraph view, because Redcon does not expose one. Shipping the
   structure does not save tokens until an agent or a downstream
   compressor consumes it. The 81% session-level reduction quoted is
   on a channel that does not yet exist in the agent's prompt stream.
   This is the strongest objection: V20 is *infrastructural* - it
   enables V25 (Markov-chain prefetch needs node identity), V41 (stable
   session-scoped IDs - the snapshot_id naturally extends to per-node
   IDs), V42 (hash-keyed shared dictionary), and V47 (snapshot delta
   vs prior run of same command), but on its own ships nothing the
   user sees.

2. **The existing `redcon/core/delta.py` already covers the file-list
   case.** It compares `compressed_context` between two run artifacts,
   emits `files_added/removed/changed`. V20 differs by adding the
   *role-and-import structure* (which existing delta omits) and by
   moving the snapshot from "previous run artifact" to "session-cached
   bigraph by id". An adversary could argue this is just a richer
   delta payload - true, but the role + edge dimensions are exactly
   what V25/V28 (call-graph-conditioned scoring) need on the agent
   side, and core/delta.py does not produce them.

3. **Top-25 reduction is only 72%, not 99%.** At small candidate scopes
   the per-delta-entry overhead (~5-10 tokens for `A {...}` and
   `E+ src tgt`) eats the win. If the dominant deployment is
   `top_files=25` (the default in `schemas/models.py:387`), the
   absolute savings are ~280 tokens per turn - meaningful in a
   billed-per-token regime, not breakthrough. The 92.7% / 97.0% wins
   only land at top-100+, which requires either an explicit
   `top_files=100` config or that the bigraph travel as a
   "candidate set" wider than the packed set.

4. **Churn lower bound assumed.** The 1-in-17 file-drop / 1-in-23 role-
   flip simulation is mild. A real `git checkout main` mid-session
   could invalidate >50% of the bigraph in one shot. The protocol
   handles this (full re-snapshot when `base_id` mismatch), but the
   amortised win shrinks linearly in the churn rate.

5. **The role classification is not a great score signal on its own.**
   `file_roles.classify_file_role` is consumed inside the scorer chain;
   exposing it externally invites the agent to second-guess the
   scorer. If the agent reasons "this is a test file, ignore it", we
   regress on test-related tasks. Mitigation: ship the bigraph as
   *structural reference*, never as ranking advice.

## Verdict

- **Novelty**: medium. The mechanism (snapshot + delta with content-
  addressed id) is standard distributed-state-machine technique, but
  applying it to *the union of file roles and the import graph* as a
  single bigraph channel - and exposing that as a session-stable
  payload via the existing `_meta.redcon` envelope - is not done in
  Redcon today. BASELINE.md confirms "Stable session-scoped IDs for
  files/symbols" and "Snapshot deltas vs prior runs" are both open.
- **Feasibility**: high. Both inputs (`build_import_graph`,
  `classify_file_role`) are already deterministic, already used per
  pack call. The session object already has a `last_run_artifact`
  hook for delta-from. The MCP `_meta.redcon` envelope (commit
  257343) is the natural carrier. No new dependency.
- **Estimated speed of prototype**: 2-3 days. Day 1: bigraph_delta
  module + tests against 100% / 94% / 72% empirical numbers. Day 2:
  runtime integration + cache key extension. Day 3: MCP wiring +
  end-to-end test that turn-2 omits the full bigraph.
- **Recommend prototype**: **conditional-on-X** where X = a concrete
  consumer (V25 prefetch, V41 stable ID resolution, or V47 cmd-side
  snapshot delta) being scheduled in the same milestone. Shipping V20
  alone changes no user-visible token count; shipping it alongside one
  consumer turns it into a force multiplier across all of Theme E
  (cross-call dictionary / reference) and Theme C (agent-aware
  predictive selection).

## Connections to other vectors

- **V25 (Markov chain over MCP calls)**: V25's transition matrix is
  keyed on tool-call types; per-node prefetch needs stable file
  identity. V20's `snapshot_id + path` pair is exactly that identity.
- **V41 (Stable session-scoped 4-char alias)**: V20's path-sorted,
  session-cached bigraph naturally yields a small integer per node
  (the index in sorted order, scoped to a snapshot_id). Combine V20's
  hash with V41's alias to get sub-byte references.
- **V42 (Hash-keyed shared dictionary)**: V20 *is* a hash-keyed shared
  dictionary, restricted to one specific schema (path -> imports,
  role). V42 generalises; V20 is the first concrete instance and
  proves the round-trip protocol works.
- **V47 (Snapshot delta vs prior `redcon_run` of same command on same
  repo)**: V20 is the file-side / `redcon plan` analogue of V47's
  command-side proposal. They share the snapshot_id + delta protocol.
- **V28 (Call-graph-conditioned scoring)**: a consumer of V20. With
  the bigraph available client-side, V28's reachability score can be
  computed by the agent rather than re-shipped per turn.
