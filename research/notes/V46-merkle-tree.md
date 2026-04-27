# V46: Merkle-tree path summarisation - send root, agent expands selectively

## Hypothesis

For tree-structured tool outputs (`redcon_overview`, `redcon_repo_map`,
`find`, `ls -R`, `tree`), the agent rarely consumes the whole tree on
the first call. It almost always then drills into 1-3 specific
subdirectories (e.g. "show me `redcon/cmd/`"). Today
`redcon_overview` returns up to 15 directory summaries with up to 5
file names each in a single payload (~200-600 cl100k tokens for a
medium repo). We claim that emitting only **(a)** a Merkle root hash
of the full directory tree plus **(b)** the first level of children
(each annotated with `(name, file_count, subtree_hash, subtree_size)`)
costs ~30-80 tokens on the first call, and a follow-up
`redcon_expand(node_hash)` returns the next level for ~50-150 tokens.
Across a typical session in which the agent investigates 2-3 specific
subtrees, the total is ~30 + 3 * 100 ~= ~330 tokens vs ~600 for the
current flat dump - a roughly 30-45% reduction on overview-class
output. The Merkle hash is load-bearing because it lets the agent
cite a subtree by hash across calls and lets the server prove "this
subtree has not changed since you last fetched it" via hash equality
- a primitive nothing else in the codebase currently provides.

## Theoretical basis

Setting. Let `T` be a rooted ordered tree with `n` nodes representing
the directory structure. Each leaf is a file, each internal node a
directory. For a node `v` write `|v|` for the size of the subtree
rooted at `v`, and `c(v)` for the number of immediate children.

**Merkle hash function.** Define
```
h(v) = H( name(v) || meta(v) || h(c_1) || h(c_2) || ... || h(c_k) )
```
where `H` is a 256-bit collision-resistant hash (e.g. BLAKE2b-256 or
SHA-256), `meta(v)` is `(file_count, total_byte_size, mtime_bucket)`
of the subtree, and the children are sorted lexicographically by
`name`. Truncating to 64 bits and base32-encoding gives a 13-char
identifier; for our scale (`n` <= 10^5 directory nodes per repo) the
birthday bound `P(collision) <= n^2 / 2^64 = 10^10 / 1.8*10^19 ~=
5*10^-10` is acceptable for an integrity tag (not a cryptographic
commitment).

**Token cost model.** Let `c` be the average cl100k token cost of
emitting one tree level including its hash labels. Empirically a
per-row line `("redcon/cmd", 13, "h:f3a9b2", 14_213b)` weighs ~10
tokens on cl100k (path 2-4, count 1, hash literal 4-5, byte size 1,
delimiters 2). With `b` = 12 average branching factor at the root,
the root payload weighs `c_root ~= 12 * 10 = 120` tokens. Each
expansion costs the same order: `c_expand ~= 8-12 * 10 = 80-120`.

**Agent behaviour model.** Assume the agent's "interesting subtree"
distribution is approximately Zipfian with exponent `s` over the `b`
top-level children (this is empirically true for code repos:
`src/`, `tests/`, `docs/` dominate). Probability the agent expands
exactly `k` distinct subtrees in a session:
```
P(K=k) = C(b,k) * p_seen^k * (1-p_seen)^(b-k)        (binomial approx)
E[K]   = b * p_seen
```
For an agent with an average task touching `~3` modules out of `b=12`,
`E[K] ~= 3` matches the brief.

**Cost comparison over a session.**
```
Current flat:        c_flat ~= b * 5 * 5 = 25*b ~= 300 tokens up-front
                     (15 dirs * 5 file names * ~5 tokens each)
Merkle root:         c_root + k * c_expand
                   = 120 + 3 * 100 = 420 tokens
```
At first glance this *loses*. The win appears when we exploit two
properties Merkle structure makes cheap:

1. **Cross-call memoisation.** Two consecutive overviews on the same
   repo (no FS change) return identical root hashes; the agent
   short-circuits. Saves `c_flat` bytes per repeat call. A 10-call
   session with 6 overview repeats saves `6 * 300 = 1800` tokens.
2. **Subtree-as-reference primitive.** Once the agent has
   `redcon/cmd:h:f3a9b2`, every future tool call that references that
   subtree (search, compress, repo_map) can be addressed by hash
   instead of path string. Path `"redcon/cmd"` is 4-5 tokens; hash
   `"h:f3a9b2"` is 4-5 tokens. Tie. The win is *structural*: hash
   equality across responses lets the agent assert "no need to
   refresh this view".

So the headline reduction is conditional on session behaviour:
- Short, one-shot session, agent fans out to all subtrees: V46 *loses*
  by 100-200 tokens.
- Iterative session with repeats and with most expansions reused
  (cache hit on subtree hash), V46 wins 30-50%.

**Information-theoretic framing.** The flat overview encodes the
tree at expected entropy `H(T)` regardless of which paths the agent
will read. Merkle + lazy expansion is closer to the conditional
entropy `H(T | path_subset) <= H(T)` with equality only when the
agent reads everything. Bound:
```
E[bits transmitted] = H(root) + sum_k P(expand v_k) * H(subtree_k | root)
                   <= H(T)        (chain rule, equality when
                                   P(expand) = 1 for all v_k)
```
The strictness of the inequality is the V46 win - in expectation
strict whenever some `P(expand v) < 1`.

## Concrete proposal for Redcon

Five additions, none touching production source on this researcher
pass; all sketched here for a future implementation pass.

**1. New module `redcon/scanners/tree_hash.py` (~120 LOC)**

Builds a Merkle DAG from the result of `engine.plan` (`ranked_files`)
or from a directory walk. Pure stdlib (`hashlib.blake2b`,
`pathlib`).

```python
@dataclass(frozen=True, slots=True)
class MerkleNode:
    name: str
    is_dir: bool
    file_count: int
    total_bytes: int
    digest: str          # 13-char base32 of blake2b-64
    children: tuple["MerkleNode", ...] = ()

def build_tree(entries: list[dict], root: str) -> MerkleNode:
    # entries: ranked_files from engine.plan
    # group by directory parts, recurse, hash bottom-up
    ...

def to_root_payload(node: MerkleNode, depth: int = 1) -> dict:
    # emit only first `depth` levels; deeper subtrees collapsed to
    # (name, file_count, digest, total_bytes)
    ...
```

**2. New MCP tool `redcon_expand` (`redcon/mcp/tools.py`)**

```python
def tool_expand(
    node_hash: str,
    repo: str = ".",
    task: str | None = None,
    depth: int = 1,
) -> dict[str, Any]:
    """Drill into a previously emitted Merkle subtree by hash."""
    node = _tree_index_lookup(repo, node_hash)
    if node is None:
        return {"error": f"unknown node hash {node_hash}; root may be stale",
                "kind": "stale_hash"}
    return {
        "node_hash": node_hash,
        "name": node.name,
        "children": to_root_payload(node, depth=depth)["children"],
        "_meta": _meta_block("redcon_expand", node_hash=node_hash),
    }
```

The `_tree_index_lookup` is a TTL-bounded per-repo cache keyed on the
root hash; entries live as long as the rank cache (15 min) so the
hash is always resolvable for the same session. On miss the agent
gets `kind: "stale_hash"` and re-fetches `redcon_overview`.

**3. Modify `redcon/mcp/tools.py::tool_overview`**

Change the return shape from "flat list of 15 dirs * 5 files each" to
"root hash + first-level children". Default `depth=1` keeps the
payload small; opt-in `depth=2` reproduces today's output for
backwards compatibility.

```python
def tool_overview(task, repo=".", depth=1, ...) -> dict:
    ranked = ... # current ranking pipeline unchanged
    tree   = build_tree(ranked, repo)
    payload = to_root_payload(tree, depth=depth)
    _tree_index_register(repo, tree)
    return {
        "task": task,
        "repo": repo,
        "root_hash": tree.digest,
        "children": payload["children"],
        "depth": depth,
        "expand_with": "redcon_expand(node_hash=...)",
        "_meta": _meta_block("redcon_overview", root_hash=tree.digest, depth=depth),
    }
```

**4. Subtree hashing applied to `redcon_repo_map`**

The existing tool already emits per-file signatures. Add a
`subtree_hash` field per group so a follow-up `redcon_expand` can
return the signatures only for that subtree, instead of dumping the
whole repo map every time.

**5. Cache key extension**

Cache keys for tools that operate on a path argument can accept
`hash:<digest>` as a synonym. Lookup table maps hash -> resolved
path within the per-process tree index. This is a strict superset of
the existing key (BASELINE constraint #6 honoured).

**Pseudo-code for the bottom-up hash**

```python
def _hash_dir(name: str, children: list[MerkleNode]) -> MerkleNode:
    h = blake2b(digest_size=8)
    h.update(name.encode("utf-8"))
    h.update(b"\x00")
    for c in sorted(children, key=lambda x: x.name):
        h.update(c.digest.encode("ascii"))
    fc = sum(c.file_count for c in children) + sum(1 for c in children if not c.is_dir)
    tb = sum(c.total_bytes for c in children)
    return MerkleNode(
        name=name, is_dir=True, file_count=fc, total_bytes=tb,
        digest="h:" + base32_lower(h.digest())[:11],
        children=tuple(children),
    )
```

Determinism: BLAKE2b is deterministic; sorted children make the hash
order-invariant; `mtime_bucket` rounded to the day eliminates 99% of
spurious churn while still detecting real changes. (BASELINE #1
preserved.)

## Estimated impact

- **Token reduction**:
  - Single overview call, depth=1: ~120 tokens vs ~300 today
    (60% drop, but only 12 children survive vs 75 file names).
  - Session with 3 expansions: ~120 + 3 * 100 = ~420 tokens vs
    ~600 expected if the agent had today required separate calls
    (today the agent gets 5 file names per dir, so 3 expansions
    are *implicit* and cost zero extra). Net **regression of
    ~120 tokens** if the agent always reads everything.
  - Session with overview-repeat (3 calls in a 10-call agent
    session) and 2 expansions: ~120 + 2*100 + 2*8(repeat-as-hash)
    = ~336 tokens vs `3*300 + 2*0 = 900` tokens today. Net **~63%
    reduction** in this regime.
  - Honest summary: V46 is a **win on long iterative sessions, a
    small loss on one-shot fan-out sessions**. The breakthrough bar
    (>=5 absolute pp on a compressor) is not reliably cleared.
- **Latency**: +1 round-trip per drill-down (the brief flagged this).
  Mitigation by shipping `depth=1` immediately and only requiring
  `redcon_expand` for deeper levels; for the common 2-level case the
  agent pays one extra round-trip per investigated subtree, costing
  ~20-40 ms per `redcon_expand` (in-memory tree index, no FS walk).
- **Affects**: `redcon/mcp/tools.py::tool_overview`,
  `redcon/mcp/tools.py::tool_repo_map` (optional), new
  `redcon/scanners/tree_hash.py`, new `tool_expand`, new in-process
  tree index. Cache layer untouched (BASELINE #6).

## Implementation cost

- **LOC**: ~250 total. Tree builder ~120, `tool_expand` ~40, tree
  index ~30, schema additions ~20, tests/fixtures ~40.
- **New runtime deps**: none. `hashlib.blake2b` is stdlib.
  Honours "no required network / no embeddings".
- **Risks to determinism**: low. Hash inputs are sorted; `mtime`
  bucketing is the only fuzzy term and can be omitted (substituting
  size-only) for full determinism in a test setting.
- **Risks to robustness**: a stale `node_hash` after the FS changes
  must return a typed error so the agent re-fetches the root. Tested
  via `kind: "stale_hash"` round-trip.
- **Risks to must-preserve**: this is overview-class output, not a
  command compressor. There is no `must_preserve_patterns` invariant
  to violate; the M8 quality harness does not apply.
- **Risks to cold-start**: the tree builder runs only when
  `redcon_overview` is called. Cold-start of `redcon` itself is not
  affected (lazy-imported scanner module). BASELINE #5 honoured.

## Disqualifiers / why this might be wrong

1. **The brief's premise is unverified.** "Agent investigates 2-3
   specific subdirs per session" is asserted, not measured. If real
   agent traces show the agent reading all 12 top-level dirs in 80%
   of sessions, V46 strictly loses tokens vs today's flat dump.
   Without an instrumented `_RANK_CACHE` consumer log we cannot tell.
   This is a measurable question; it's not measured.
2. **Today's overview is already small.** 15 dirs * 5 file names *
   ~5 tokens = ~375 tokens, not the "directory map dump" the brief
   implies. The current `tool_overview` is *already* a summary; it
   does not emit an `ls -R`. The headline win in the brief over-states
   the baseline.
3. **Hashes are not free.** A 13-char base32 hash costs ~4-6 cl100k
   tokens (literal `h:f3a9b2c4d5e` tokenises across 4-5 BPE pieces;
   high-entropy strings tokenise badly). For 12 root children that
   adds ~50-70 tokens of pure-overhead bookkeeping that brings no
   information the agent uses 80% of the time. V41 (4-char alias) and
   V42 (server-side dictionary) attack this directly and probably
   compose better. V46 alone lights up only when the *integrity*
   property of the hash is consumed by the agent - which today no
   agent does.
4. **The "subtree hasn't changed" claim is fragile.** Touching any
   file's mtime (e.g. a build that touches `__pycache__/`) cascades
   the hash up to the root unless `__pycache__/` is filtered. We'd
   need a curated "ignore" set, which is exactly the same surface
   `redcon_search`'s `ignore_dirs` already maintains. Sharing it is
   trivial but worth noting.
5. **Latency floor on small repos.** A repo with 50 files takes ~5 ms
   to hash today; the round-trip cost of `redcon_expand` (one MCP
   call) is 10-50 ms. The break-even repo size where Merkle pays for
   itself is roughly 5000+ files. Under that, today's flat dump is
   strictly faster *and* smaller.
6. **Composition with V41/V42 dilutes the unique value.** If V41
   ships 4-char file aliases and V42 ships a hash-keyed shared
   dictionary, V46's hash-based subtree identity becomes redundant -
   the dictionary already gives "previously seen, fetch by reference".
   V46 then collapses to "lazy expansion of overview", which is a
   one-knob change to `tool_overview` (just emit `depth=1` by
   default) and doesn't need a Merkle structure at all. The Merkle
   property earns its complexity only if cryptographic-strength
   invariance across calls is consumed somewhere.
7. **Not the right axis for breakthrough.** BASELINE defines the bar
   as ">=5pp compact-tier reduction on multiple compressors, OR >=20%
   cold-start cut, OR a new compounding dimension". V46 is overview-
   only, doesn't touch compressors, doesn't change cold-start, and
   compounds only modestly (with V41/V42). It belongs in the "good
   ergonomic improvement" bucket rather than breakthrough.

## Verdict

- **Novelty**: medium. Merkle-tree-as-tree-API is a well-known
  pattern (IPFS, git itself, certificate transparency); applying it
  to MCP tool outputs as a means of lazy expansion + cross-call
  consistency is mildly novel because no other agent harness ships
  a deterministic per-repo overview today. The information-theoretic
  framing (chain-rule entropy decomposition) is correct and not
  shopworn in this domain.
- **Feasibility**: high. Pure-stdlib, ~250 LOC, no new deps, no
  determinism risk, no must-preserve risk. Plumbs cleanly into
  existing `tool_overview` and the per-process rank cache.
- **Estimated speed of prototype**: 1-2 days for the tree builder +
  `redcon_expand` tool + a fixture-based test against a snapshot
  repo. Another 1-2 days to integrate `repo_map`. ~3-4 days total
  to a flag-gated PR.
- **Recommend prototype**: **conditional-on** measuring two things
  first:
  (a) actual `tool_overview` payload sizes on real agent traces -
      if median is already <200 tokens, V46 has nothing to compress;
  (b) actual fan-out behaviour - how many distinct subtrees does an
      agent investigate per task? If `E[K] >= 8` of 12 top-level
      dirs, V46 loses; if `E[K] <= 4`, V46 wins meaningfully.
  Without those numbers V46 is plausible-looking ergonomics, not
  defensible compression. **Do NOT** ship blind.
