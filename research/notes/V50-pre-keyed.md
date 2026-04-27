# V50: Server-pushed pre-keyed cache - client requests by hash, gets full

## Hypothesis

Today every `redcon_compress` and `redcon_search` call ships the full
compressed text of K files (compress) or up to `max_results=50` matches
with their 200-byte snippets (search). A pure pull-model claim: in real
agent traces only a small minority of returned items are actually
"drilled into" by the agent. If the default response shape is replaced
by a **menu of content-addressed hashes** plus minimal selector data
(path, score, line/byte counts, role), and the agent has to call a
`redcon_resolve(hash)` follow-up to obtain bytes, then total
session-level token spend drops whenever the agent's drill rate
`d` (= fraction of hashes the agent actually resolves) is below the
break-even point.

Concretely, for a `redcon_compress` call returning K=10 files with
~500 tokens of content each (~5000 tokens of payload today), V50 ships
~30 tokens (10 hashes * 3 tokens, plus a tiny header). Each follow-up
`redcon_resolve` costs `R + 500` tokens where `R ~= 10` is the request
overhead. Break-even drill rate:

```
hashes_only + d*K*(content + R)  <  K*content
30 + d * 10 * 510                <  10 * 500
=> d  <  (5000 - 30) / 5100  =  0.974
```

That bound is loose. The interesting threshold is where V50 *beats* a
moderately compressed baseline (assume the current default already
saved 3x via tiering). With current 5000-token payload reflecting
COMPACT output and a target 50% session-level savings, V50 wins
whenever drill rate < ~0.5. If realistic `d` for agent runs is
<= 0.30 (claim from V21/V25/V27 priors plus the BASELINE
"agents don't read everything ranked" intuition), V50 saves
~50-65% on average per (compress|search) call pair.

## Theoretical basis

Content-addressable storage with deferred fetch is the git object
store / IPFS / Plan-9 venti model. For Redcon the relevant calculus is
**expected token cost under a Bernoulli drill model**:

Let:
- K = number of items in result (files or matches).
- s = mean per-item content size in tokens at COMPACT (default = ~500
  for compressed file, ~5 for a single grep match line).
- d = mean drill rate, i.e. P(agent resolves item i).
- R = request overhead per resolve call (tool name, args, framing
  tokens echoed back) ~= 10-20 tokens at cl100k.
- h = hash entry size in the menu, including path + counts. With
  content-addressed shorthand (e.g. 8 hex chars = 2 cl100k tokens) and
  a 2-token path-suffix dedup as in the diff compressor, `h ~= 5`.

Expected V50 cost per call:

```
E_V50  =  K*h  +  d * K * (s + R)
       =  K * (h + d*(s + R))
```

Baseline cost (current default):

```
E_BASE =  K * s
```

Break-even drill rate `d*`:

```
d* = (s - h) / (s + R)
```

Substitute s=500, h=5, R=15:

```
d* = (500 - 5) / (500 + 15) = 495/515 = 0.961
```

So for `redcon_compress` (large s) any drill rate below ~96% wins.
For `redcon_search` (per-match s ~= 5 line tokens), the calculus
flips:

```
d* = (5 - 5) / (5 + 15) = 0.0
```

Search returns lines that are already cheap; resolving them by hash is
**always a loss**. This is a load-bearing finding: V50 applies to
content-heavy tools (compress) and *not* to micro-payload tools
(search). For search, V50 should be applied at the **file-grouped**
level (one hash per file, agent drills into a file to see all matches
for it), not per line. With grouping, s_file = matches_per_file *
line_size + path_header ~= 5*5 + 5 = 30, giving d* = 25/45 = 0.56.
Better, but still narrow margin compared to compress.

Information-theoretic framing. The hash menu is a **prefix code** of
length log2(N) bits per entry where N is the number of distinct
content blobs the cache has ever held. SHA-256 truncated to 8 hex
chars = 32 bits handles 2^16 distinct blobs with negligible collision
risk at session scale. The agent's drill decision is a one-bit
selector per item; total feedback channel rate = K bits. This is the
minimum bits needed to describe an arbitrary subset, by a counting
argument.

The risk is round-trip count: each resolve is one MCP round-trip. If
the harness charges latency per call (not just tokens), the total
**latency cost** scales with `1 + d*K` instead of 1. For K=10 and
d=0.3, that's a 4x roundtrip multiplier. Matters less for token
budget, more for human-perceived response time.

## Concrete proposal for Redcon

Three additions, behind a feature flag, backwards compatible.

**1. Persistent content-addressed blob cache**

A small on-disk K-V at `.redcon/blobs/<sha8>/<full-sha>.bin` (sharded
by 2-char prefix to avoid huge directories). Lives next to the
existing `.redcon/cmd_runs/` log-pointer dir. Process-local in-memory
LRU for hot blobs, cap ~64MB. Eviction policy: LRU, size-bounded.
Determinism preserved because the key is `sha256(text)` of the
canonical compressed text - no clock, no mtime.

```python
# redcon/cache/blob_store.py  (NEW)
class BlobStore:
    def __init__(self, root: Path, mem_cap_bytes: int = 64*1024*1024): ...
    def put(self, content: str) -> str:
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        # write-through: in-memory LRU + disk
        ...
        return h
    def get(self, h: str) -> str | None: ...
    def stat(self, h: str) -> BlobStat | None: ...  # size, token estimate
```

Hash is the canonicalised compressed text post-`_normalise_whitespace`
so two calls producing identical bytes hit the same blob. This is the
same canonicalisation already used by the cmd-side cache key.

**2. New MCP tool: `redcon_resolve`**

```python
# redcon/mcp/tools.py
def tool_resolve(
    hashes: list[str],
    repo: str = ".",
) -> dict[str, Any]:
    """Resolve content-addressed blobs returned by other tools."""
    store = _get_blob_store(repo)
    out, missing = [], []
    for h in hashes[:32]:  # cap fan-out
        text = store.get(h)
        if text is None:
            missing.append(h)
            continue
        out.append({
            "hash": h,
            "content": text,
            "tokens": estimate_tokens(text),
        })
    return {
        "blobs": out,
        "missing": missing,
        "_meta": _meta_block(
            "redcon_resolve",
            resolved=len(out),
            missing=len(missing),
        ),
    }
```

Missing-hash handling: if an agent supplies a hash that's been
evicted (LRU pressure across long sessions), `missing` is non-empty
and the agent is expected to re-issue the source call. Cache eviction
rate stays low at session scale; over-fetch is the tail risk.

**3. New `byhash` mode on `redcon_compress` and `redcon_search`**

Add `mode` parameter. Default behaviour unchanged when
`mode="content"` (current). New `mode="byhash"` returns the menu.

```python
# redcon/mcp/tools.py::tool_compress (sketch of byhash path)
def tool_compress(
    path: str, task: str, repo: str = ".",
    max_tokens: int = 2000,
    mode: str = "content",  # "content" | "byhash"
    config_path: str | None = None,
) -> dict[str, Any]:
    ...
    if mode == "byhash":
        store = _get_blob_store(repo)
        h = store.put(match["text"])
        return {
            "path": match["path"],
            "strategy": match["strategy"],
            "original_tokens": match["original_tokens"],
            "compressed_tokens": match["compressed_tokens"],
            "content_hash": h[:16],         # 16-hex shorthand, full retained
            "preview": match["text"][:120], # 1-line teaser
            "resolve_with": {
                "tool": "redcon_resolve",
                "args": {"hashes": [h[:16]]},
            },
            "_meta": _meta_block("redcon_compress", mode="byhash",
                                 strategy=match["strategy"]),
        }
    # else: existing content path, unchanged
```

For `redcon_search`, the natural unit is a per-file group (per the
break-even derivation above). Pseudo:

```python
# redcon/mcp/tools.py::tool_search (byhash path)
if mode == "byhash":
    by_file: dict[str, list[Match]] = {}
    for m in matches:
        by_file.setdefault(m.path, []).append(m)
    items = []
    store = _get_blob_store(repo)
    for path, ms in by_file.items():
        text = "\n".join(f"{m.line}: {m.text}" for m in ms)
        h = store.put(text)
        items.append({
            "path": path,
            "match_count": len(ms),
            "content_hash": h[:16],
            "first_line": ms[0].line,
        })
    return {"pattern": pattern, "files": items, "_meta": ...}
```

**4. Pipeline switch**

`redcon/cmd/pipeline.py::compress_command` is *not* changed. The blob
store integration happens entirely at the MCP-tool layer, so CLI
behaviour and existing Compressor protocol are untouched. This keeps
the cmd-side cache key invariant and respects BASELINE constraint #6.

Files touched:

- `redcon/cache/blob_store.py` - new, ~100 LOC.
- `redcon/mcp/tools.py` - add `tool_resolve` (~40 LOC), add `mode`
  parameter to compress/search (~50 LOC combined).
- `redcon/mcp/server.py` - register `redcon_resolve` (~15 LOC).
- New CLI flag `redcon plan --mode=byhash` for the pack pipeline if
  we want consistency, but **keep default = content** until measured.

## Estimated impact

- **Token reduction**: depends entirely on drill rate `d`. At
  d=0.30 on `redcon_compress`-like calls returning 10 files,
  estimated session-level reduction ~65% on the compress portion of
  the spend (5000 -> ~1750 tokens for an average call sequence). At
  d=0.70 the saving is ~30%. At d=0.96 it breaks even. **No effect**
  on existing compressor reductions (those already shipped); this is
  a separate dimension of compression that compounds on top.
- **Latency**: cold +0% (no new imports until tool invocation),
  warm slightly *worse* on the agent side because each resolve is one
  more MCP round-trip. If round-trips are free, no concern; if not, a
  proxy `redcon_resolve_many` allows batched fan-out.
- **Affects**: `redcon_compress`, `redcon_search` only. Does not
  change cmd-side compressors (git_diff, pytest, etc.) - those return
  inline because they run as part of an agent's command tool, where
  the agent already committed to needing the output.
- Cache layer: adds a new disk artifact under `.redcon/blobs/`.
  `redcon_quality_check` extended to verify resolve(put(x)) == x for
  determinism.

## Implementation cost

- **~250 LOC** total: BlobStore (100), MCP tools (90), tests/fixtures
  (60).
- **No new runtime deps**. SHA-256 is stdlib; LRU via `functools` or
  a 30-line custom doubly-linked map. No network, no model.
- **Risks to determinism**: low. Hash key depends only on canonical
  content text. Disk artifact is content-addressed so rewriting the
  same blob is a no-op.
- **Risks to robustness**: moderate. If blob is evicted between
  source call and resolve, agent gets `missing` and must re-run. Need
  to size the LRU + on-disk cap conservatively, document the
  guarantee ("blobs persist for the session duration").
- **Risks to must-preserve guarantees**: zero. Resolve returns the
  exact bytes the source call produced; pattern-preservation contract
  is upheld by the source compressor, not the cache.

## Disqualifiers / why this might be wrong

1. **Drill rate may be high.** If real Claude Code / Cursor traces
   show the agent reads >60% of returned files (because the ranker is
   already aggressive about top-K), V50 *loses* tokens and adds
   round-trips. The pull model only wins if the ranker over-includes
   - which contradicts the goal of a tight ranker. There's an
   inherent tension: the better the file ranker, the worse V50
   performs. This is the load-bearing risk and needs trace measurement
   first. The five-trace methodology in the brief is exactly the
   right calibration step.
2. **Round-trip latency may dominate token savings.** Agent harnesses
   that bill per-call (or where each MCP round trip costs human
   wall-time) make `1 + d*K` round trips less attractive than `1`
   even when token math wins. Mitigation: `redcon_resolve_many`
   batched call, plus a heuristic emit-inline-if-small (`if
   total_tokens < threshold: mode="content"`).
3. **Already partially done by V42/V44.** V42 (hash-keyed shared
   dict) keeps the server-side cache, agent calls a resolve tool -
   identical to V50's resolve mechanism. V44 (deep-links) keeps
   `(file, line)` references with the agent re-executing. The novelty
   here is *only* the **default response shape change** (server
   no longer ships content by default). If V42 ships first as
   opt-out, V50 is just V42 with a flipped default - low novelty.
4. **Agent prompt-engineering induces over-resolve.** Faced with a
   menu of N hashes, an LLM agent may resolve all N out of risk
   aversion ("I might miss something"). This is the receiver-
   irrationality risk from the V09 self-instruction literature. The
   menu presentation must explicitly discourage blanket resolution
   ("only resolve files matching keyword X" prompts).
5. **Cache eviction across long sessions.** A 64MB cap holds maybe
   ~500 average compressed files. A heavy session can evict and
   force re-runs, which costs *more* than original (because the source
   call has to redo the rank+compress). Bounded by capping cache
   size and persisting on disk, but the semantics are non-trivial.
6. **MCP fan-out limits.** Some MCP harnesses limit a single response
   payload to e.g. 32 KB; resolving 30 blobs in one call may exceed
   that. Ship `redcon_resolve_many` with a hard cap (e.g. 8 hashes
   per call) and let the agent pipeline.

## Methodology - five typical agent trace estimates

The brief asks for a count. Lacking recorded traces, I sketch what we
should measure and provide priors derived from BASELINE + adjacent
research notes.

**Trace 1: bug-fix on a known module.** Agent calls
`redcon_rank(task)` -> picks top file -> `redcon_compress(path)` ->
edits. Expected drill on the **next** compress call (which lists 10
files in the same module): 1-2 of 10. d ~= 0.15. **V50 strongly wins.**

**Trace 2: cross-cutting refactor.** Agent ranks broadly, compresses
many files. Drill rate higher: 4-6 of 10. d ~= 0.50. **V50 marginally
wins** at compress, marginal-to-loss at search.

**Trace 3: greenfield "explore the codebase".** Agent overviews,
ranks, compresses everything in top-15. Drill: 10-12 of 15. d ~= 0.75.
**V50 loses** unless the menu replaces a single overview-level call
that the agent then prunes.

**Trace 4: targeted grep + read.** `redcon_search` returns 30 matches
across 12 files; agent opens 2 files in detail. d (per-file) ~= 0.17.
**V50 wins** at the file-grouped level.

**Trace 5: tight loop edit-test-edit.** Same files re-touched. With a
session cache (V42 territory) drill is high but most resolves hit
cache; this is mainly a V47/V42 interaction, V50 alone neutral.

Aggregated prior: weighted average d ~= 0.30-0.45. Per the math
above, V50 saves a non-trivial fraction of compress tokens but is a
**conditional win** that depends sharply on the trace mix.

**Token cost head-to-head (back-of-envelope):**

| call shape           | content mode | byhash mode (d=0.3) | byhash (d=0.7) |
|----------------------|--------------|---------------------|----------------|
| compress 10 files    | 5000         | 30 + 0.3*10*515 = 1575 | 30 + 0.7*10*515 = 3635 |
| search 30 matches    | ~250 raw     | 60 (file menu) + 0.3*5*45 = 128 | 60 + 0.7*5*45 = 218 |

Round-trip count per logical operation:

| call shape | content | byhash (d=0.3) | byhash (d=0.7) |
|------------|---------|----------------|----------------|
| compress 10 | 1 | 1 + 3 = 4 | 1 + 7 = 8 |
| search 30  | 1 | 1 + ~2 = 3 | 1 + ~4 = 5 |

Round-trips multiply 3-8x. This is the cost the brief flagged as a
risk and it shows up here clearly.

## Verdict

- **Novelty: medium.** Content-addressable defer-fetch is canonical
  (git, IPFS, venti); applying it as the *default* response shape
  for an agent-context tool is a meaningful framing shift but not a
  fundamental new technique. The differentiation versus V42/V44 is
  the *default flip*, not the mechanism.
- **Feasibility: high.** No new dependencies, ~250 LOC, no impact on
  existing compressor invariants. The only non-trivial concern is
  the disk cache lifecycle.
- **Estimated speed of prototype**: **3-5 days** for blob store +
  resolve tool + byhash mode on compress only, behind a feature flag.
  Add 3 more days for search and quality-harness coverage. Add 1-2
  weeks to instrument an agent-trace corpus and measure `d`
  empirically before flipping the default.
- **Recommend prototype: conditional-on** measuring drill rate on at
  least 5 recorded agent traces. If measured `d <= 0.40` on
  compress-shaped calls, build it as opt-in (`mode="byhash"`),
  publish the math, leave the default as `content`. **Do not flip
  the default until trace data shows d <= 0.25 across multiple
  agents.** The aggressive default flip the vector framing suggests
  is only justified at low drill rates, and we don't have that data.
  V50 is a strong second-stage move *after* V42 (the hash-keyed
  shared dict) ships as opt-in - V50 is then just the policy
  decision to make hash-mode the default, which is a configuration
  change rather than new code.
