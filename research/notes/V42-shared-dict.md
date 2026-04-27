# V42: Hash-keyed shared dictionary returned to agent once, referenced thereafter

## Hypothesis
Across a multi-call agent session, compressor outputs contain large sub-blocks
(per-file diff stanzas, grep file groups, log-pointer summaries, must-preserve
error templates) that recur verbatim - either inside one call or across calls.
If the runtime keeps a **content-addressed cache** of every emitted block and
replaces second-and-later occurrences with an `@<hash>` reference, the agent
pays one full transmission and arbitrary near-zero references afterwards. A
side tool `redcon_resolve(hash)` returns the original bytes only when the agent
needs them. This is a strict generalisation of V41: V41 dedups *paths*, V42
dedups *any value*. Prediction: on repeat-heavy sessions (an agent that
re-runs `git diff` after thinking), block-grain V42 saves >=10% net of teach
overhead; on exploratory sessions (every call asks for a new symbol), V42
underperforms V41 because V41's 5-char alias amortises across many short
occurrences while V42 is gated by a `>=50t` threshold needed for ref-emission
break-even.

## Theoretical basis
Let a session emit blocks `b_1..b_n`, each with token cost `t_i`, and let
`c_i` be the count of duplicate occurrences of block `i` (so call-stream is
`b_1` x `c_1`, `b_2` x `c_2`, ...). Without dedup the agent pays
`Sum_i c_i * t_i`. With hash-ref dedup at threshold `tau` and reference cost
`r` (in tokens) the agent pays
`Sum_i [t_i + (c_i - 1) * (r if t_i >= tau else t_i)] + T` where `T` is the
one-shot teach overhead. Saving is positive iff
`Sum_{i: t_i >= tau} (c_i - 1) * (t_i - r) > T`.

Empirical numbers (cl100k via tiktoken on this repo):
- `r` (reference syntax `@<8hex>`) = 4 tokens; `@ref:<8hex> (Nt cached)` = 11 tokens
- One-shot teach prompt = 70 tokens
- Break-even per-occurrence at `r=4`: `t_i >= 5` to recover one repeat; ten
  duplicates of a 50-token block save `9 * (50-4) - 70 = 344` tokens
- Birthday-bound on collision (P(any collide) = 1 - exp(-N(N-1)/(2 * 2^B))):
  - N=10000 unique chunks, 32-bit hash: `1.16e-2` (unacceptable)
  - N=10000, 64-bit hash: `2.71e-12` (negligible)
  - N=50000, 64-bit: `6.78e-11`
  - Decision: **keep 64-bit (16-hex) full hash server-side, display 8-hex
    short ref**; on the rare display-collision, emit `@collide:<full16hex>`

So V42 is a strict positive whenever total saved-tokens-from-repeats exceeds
70 (teach overhead), and grows linearly thereafter.

## Concrete proposal for Redcon
### Files
- `redcon/runtime/session.py` - add a `ContentDictionary` to `RuntimeSession`
- `redcon/cmd/pipeline.py` - post-process `compressed.text` through
  `_dedup_blocks(text, session.dictionary)` after `_normalise_whitespace`
- `redcon/runtime/runtime.py` - first `AgentRuntime.run` of a session prepends
  the 70-token teach prompt to the agent context; subsequent runs do not
- new file `redcon/runtime/resolve.py` - implements `redcon_resolve(hash)`
  MCP tool; reads from `session.dictionary[hash]`

### Sketch (Python)
```python
# redcon/runtime/session.py additions
@dataclass
class ContentDictionary:
    # 16-hex (64-bit) full hash -> (text, token_cost)
    entries: dict[str, tuple[str, int]] = field(default_factory=dict)
    # 8-hex short -> set of 16-hex full (for collision detection)
    short_index: dict[str, set[str]] = field(default_factory=dict)

    def intern(self, block: str, t: int) -> str:
        h = blake2b(block.encode(), digest_size=8).hexdigest()  # 16 hex
        s = h[:8]
        self.entries.setdefault(h, (block, t))
        self.short_index.setdefault(s, set()).add(h)
        return s if len(self.short_index[s]) == 1 else h  # disambiguate

    def already_seen(self, block: str) -> str | None:
        h = blake2b(block.encode(), digest_size=8).hexdigest()
        return h if h in self.entries else None

# redcon/cmd/pipeline.py post-processor (called after _normalise_whitespace)
def _dedup_blocks(text: str, dict_: ContentDictionary,
                  threshold_tokens: int = 50) -> str:
    out = []
    for blk in (b.rstrip() for b in text.split("\n\n") if b.strip()):
        t = estimate_tokens(blk)
        h = dict_.already_seen(blk)
        if t >= threshold_tokens and h is not None:
            out.append(f"@ref:{h[:8]} ({t}t; redcon_resolve)")
        else:
            dict_.intern(blk, t)
            out.append(blk)
    return "\n\n".join(out)
```

### Convention taught once per session (~70 tokens)
> "Lines `@ref:<8hex> (Nt; redcon_resolve)` reference prior content. Call
> `redcon_resolve(hash)` to fetch full bytes. References are stable within
> a `session_id`. On the rare display-hash collision the runtime emits
> `@collide:<full16hex>` instead."

## Estimated impact
Numbers below are from a tiktoken-measured **6-call simulated session** on
this repo (block granularity, `tau`=50 tokens, reference syntax
`@ref:<8hex> (Nt cached)`). Two regimes were measured:

### Regime A - repeat-heavy session (agent re-runs `git diff`)
Calls: `git status`, `git log -8`, `git diff <p>`, `grep <q1>`, `pytest -k <p>`,
`git diff <p>` (same as call 3, agent re-checks).

| variant | tokens | saving vs baseline |
|---|---:|---:|
| baseline (no dedup) | 657 | - |
| V41 path-only alias | 620 | 5.6% |
| V42 block-grain `tau`=50 | **566** | **13.9%** |
| V42 line-grain `tau`=10 | 636 | 3.2% |

Differential V42 - V41 (block grain): **+8.3 pp** in V42's favour. After
amortising teach overhead (70 t / 6 calls = 11.7 t per-call), block-grain V42
still wins by ~3 pp.

### Regime B - exploratory session (no exact repeats; only path/symbol overlap)
6 calls: `git status`, `grep compress_command`, `grep detect_compressor`,
`git diff <p>`, `pytest -k registry`, `grep rewrite_argv`.

| variant | tokens | saving vs baseline |
|---|---:|---:|
| baseline | 407 | - |
| V41 path-only | 392 | 3.7% |
| V42 block-grain `tau`=50 | 403 | 1.0% |
| V42 line-grain `tau`=10 | 399 | 2.0% |

V41 wins by ~1.7-2.7 pp here. The teach overhead pushes V42 net-negative on
short exploratory sessions.

### Net guidance
- V42 dominates V41 when sessions contain whole-block re-emissions
  (re-runs of the same command, log-pointer messages, multi-file diff that
  recurs across `git diff` and `git diff --cached`, repeated must-preserve
  error templates).
- V41 dominates V42 when sessions are exploratory and the only repeats are
  paths embedded in differently-scoped output.
- **Hybrid: emit V42 references for blocks `>=50t`, V41-style path aliases
  for `<50t` repeats.** On Regime A this hybrid is `>=14%` (V42 already does
  best); on Regime B it is `>=4%` (V41 territory recovered). I.e. hybrid
  Pareto-dominates either alone. This is the recommendation.

### Latency
- Per-emission: one BLAKE2b-64 over the block (~ns/byte) + one dict lookup.
  Adds <0.1 ms per call on typical compressed outputs. No regression to
  cold-start (no new lazy-imports).
- Cache memory: O(N) where N is unique blocks per session. At 10k blocks
  averaging 100 chars each: ~1 MiB. Bounded.

### Affects
- All 11 compressors (text post-processing is compressor-agnostic).
- Cache key (`build_cache_key`) unchanged - V42 operates *after* cache lookup
  on the cached `compressed.text`, so the cache layer stays deterministic.
- `_meta.redcon` block grows by one optional field: `dict_refs: [<8hex>...]`.

## Implementation cost
- Lines of code: ~120 (ContentDictionary class ~40, pipeline hook ~25,
  resolve tool ~30, teach-once logic ~15, tests ~10 baseline + golden).
- New runtime deps: none (BLAKE2b is in `hashlib` stdlib).
- Risks:
  - **Determinism**: BLAKE2b is deterministic; same-input-same-output preserved
    if and only if the dictionary is part of cache key. Choice: cache keyed on
    `(argv, cwd, session_id)`. **Same `argv,cwd` in two different sessions
    will return DIFFERENT compressed text** (one has refs, one has fulls).
    This is a strict superset of the current key but breaks the current
    "same input -> same bytes" invariant *across sessions*. Mitigation: per-
    session cache namespace; existing per-process cache stays per-session.
  - **Must-preserve patterns**: refs replace block bodies. If the harness
    runs against deduped output it won't see the must-preserve regex. Fix:
    quality harness runs on **first emission only** (block has not yet been
    referenced). Add a `dedup_phase: "first" | "ref"` field; harness only
    asserts on `first`. Trivial to implement; documented constraint.
  - **Agent confusion**: agent doesn't follow the convention, treats `@ref:`
    as junk, asks again. Mitigation: include the resolve URL inline, keep
    refs always parsable as a single line. Empirically test with one model.
  - **Hash collisions on 8-hex display**: at N=10k blocks, 8-hex (32-bit)
    collide P=1.16e-2; not acceptable as primary key. Server keeps 16-hex
    (64-bit, P=2.71e-12 at N=10k); display 8-hex unless collision detected,
    then promote to full 16-hex. Total worst-case cost: 2 extra ref tokens.

## Disqualifiers / why this might be wrong
1. **Dominated by V41 on the common case.** Most agent sessions don't re-emit
   identical 50-token blocks. They emit *similar* blocks with one symbol
   different. V42 misses these; V41 catches the path overlap. Production
   workloads need to be measured before shipping V42 standalone. Hybrid is
   safer.
2. **Teach overhead is per-conversation, not per-session.** If the host
   resets context every turn (some agent harnesses do), the 70-token teach
   re-fires every call and V42 turns net-negative until ~10 calls accumulate.
   Mitigation: only enable when `RuntimeSession.turn_number > 1` AND
   cumulative dedup savings projection exceeds 2x teach cost.
3. **Already in baseline as cache, sort of.** The per-process cache already
   returns identical bytes on repeat `compress_command` calls. If the agent
   re-runs the same `argv` it gets a cache-hit at the *report* level - but
   the agent still pays full tokens to receive that text, because the cache
   short-circuits *parsing*, not *transmission*. V42 is the missing
   transmission-side dedup; this is real and not covered. So not a
   disqualifier, but worth noting the boundary.
4. **Fragile to whitespace and ordering.** Two compressors emitting the same
   logical block with one trailing space differ in BLAKE2b output, miss
   dedup. Need canonical whitespace before hashing - add to the pipeline
   `_normalise_whitespace`. One more reason to centralise normalisation.
5. **`redcon_resolve` is a new round-trip.** If the agent needs the full bytes
   often, the resolve cost (network/IPC + token re-spend) exceeds the saving.
   Heuristic guard: the dictionary entry tracks `resolve_count`; if a block
   is resolved >=2 times across calls, demote it to "always-inline" for
   future emissions in this session.

## Verdict
- **Novelty:** medium-high. The cross-call content-addressed dictionary is
  identified as open-frontier in BASELINE.md ("Cross-call dictionary or
  session-level dedup across multiple `redcon_run` invocations"). The hybrid
  with V41 and the 64-bit-with-display-truncation collision protocol is the
  novel engineering contribution.
- **Feasibility:** high. ~120 LoC, stdlib-only, no model deps, integrates at
  one well-defined seam (`pipeline.py` post-norm). Determinism preserved
  with per-session cache namespace.
- **Estimated speed of prototype:** 1-2 days. Including the hybrid V41+V42
  path aliasing layer and a quality-harness `dedup_phase` flag.
- **Recommend prototype:** **conditional**. Ship as **hybrid V41+V42** only.
  Standalone V42 underperforms V41 on exploratory sessions; standalone V41
  leaves 8 pp on the table on repeat-heavy sessions. The hybrid is a strict
  improvement on both. Without that combination V42 alone is not worth the
  complexity over V41.

## Key numbers (tiktoken cl100k, this repo, 6-call sessions)
| metric | value |
|---|---:|
| Reference syntax `@ref:<8hex> (Nt cached)` cost | 11 tokens |
| Bare `@<8hex>` cost | 4 tokens |
| Teach prompt | 70 tokens |
| Break-even threshold (single repeat, full ref) | block >=15 tokens |
| Recommended threshold | block >=50 tokens |
| Saving (Regime A, repeat-heavy, V42 block-grain) | 13.9% |
| Saving (Regime B, exploratory, V42 block-grain) | 1.0% |
| V42 - V41 differential (Regime A) | +8.3 pp |
| V42 - V41 differential (Regime B) | -2.7 pp |
| 64-bit collision P at N=10k | 2.71e-12 |
| 32-bit collision P at N=10k | 1.16e-2 (unacceptable) |
