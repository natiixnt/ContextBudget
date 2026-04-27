# V49: Persistent symbol cards once per session, then only diffs against them

## Hypothesis
Within a single agent session, the same handful of "hot" symbols (the function or class the agent is editing, plus its 2-5 nearest neighbours in the call graph) get re-emitted in nearly every tool call: `redcon_search` returns the symbol's snippet, `redcon_run` of `pytest` quotes its name in failure tracebacks, `redcon_compress` includes its body in the file-level pack, a follow-up `redcon_search` pulls it again. Each emission re-pays the full cost of (signature + docstring + caller list + tests). The claim: if the runtime emits a single ~70-token "card" the first time a symbol surfaces, then on every subsequent surface emits only `(card f:c3)` plus a short delta when something changed, total per-symbol cost across N surfaces drops from N x 70 to 70 + (N-1) x 6. For typical N=4 surfaces of one symbol, that is 76 tokens vs 280 - a 73% reduction *on the symbol-card slice*. This is V41's path-alias idea lifted to the symbol scope: an alias is a card with no body; a card is an alias plus a one-time payload.

## Theoretical basis
Let p_k be the probability a symbol s_k is referenced in any one of T tool calls in a session. Total expected emissions of s_k is E[N_k] = T * p_k. Without cards the per-symbol token cost is C_full(s_k) * T * p_k. With cards it is C_full(s_k) + C_ref * (T * p_k - 1) (assuming the card is emitted on first surface and referenced thereafter, no diff). The crossover (cards win) is at T * p_k > 1 + (C_ref / C_full) -- i.e. as soon as the symbol is touched twice and C_ref << C_full, cards pay off.

Concretely, with C_full = 70 (signature + 1-line doc + 3 callers + 3 callees + last-commit hash + test pointer) and C_ref = 6 (the literal token count of the string `(card f:c3)` under cl100k: `(`, `card`, ` f`, `:`, `c`, `3`, `)` = 7 BPE tokens, conservatively round to 6 for the inline form `[c3]`), the marginal saving per extra surface is 70 - 6 = 64 tokens. Across 4 surfaces of one symbol: cost goes from 4 * 70 = 280 to 70 + 3 * 6 = 88, a 192-token absolute saving on that symbol (68.6% reduction). Aggregated over a 30-turn session in which 8 hot symbols each surface 4 times, the expected reduction is 8 * 192 = 1,536 tokens -- roughly the size of one whole MCP response.

When the symbol *changes* between surfaces (e.g. agent rewrote it), the card emits a delta. Bound the delta cost by the unified-diff length of (sig_old, sig_new) plus optional doc-line diff. For the empirical distribution of in-session edits seen in BASELINE.md's 73.8% pytest reduction work (commit 257343 area), most edits touch the body, not the signature -- so the sig diff is empty and only the "last commit info" line changes. That is a ~3-token delta: `(card f:c3 r:b)` where `b` is a 1-char rev-tag. Even at the worst case of full re-emission, cards degrade gracefully to V0 cost.

Under a Zipf model of symbol-touch frequency in a session (head heavy: top-3 symbols account for ~60% of all symbol references in agent traces), cards target the head where they pay off most. Tail symbols (touched once) cost +6 tokens for the alias declaration over the no-card baseline; this is the regression we need to keep small. Break-even is at p_k * T = 1 + 6/70 = 1.086, so any symbol referenced twice in-session is already net-positive.

## Concrete proposal for Redcon

Two files do the work (sketches only, do NOT modify production):

`redcon/runtime/session.py` -- extend `RuntimeSession` with a card registry.

```python
@dataclass
class SymbolCard:
    card_id: str                # "c3", session-monotonic
    symbol_key: str             # SHA-1 prefix of (file_path, fq_name)
    fq_name: str                # "redcon.cmd.pipeline.compress_command"
    signature: str              # "compress_command(command, *, cwd='.', hint=None, ...)"
    doc1: str                   # one-line docstring extract, <=80 chars
    callers: tuple[str, ...]    # up to 3 fq names
    callees: tuple[str, ...]    # up to 3 fq names
    test_pointer: str | None    # "tests/cmd/test_pipeline.py::test_basic"
    last_rev: str               # 7-char git short hash at first surface
    surfaces: int = 0           # times referenced this session

# RuntimeSession gains:
# cards_by_key: dict[str, SymbolCard] = field(default_factory=dict)
# next_card_n: int = 0

def emit_or_ref(self, card: SymbolCard, *, now_rev: str) -> str:
    existing = self.cards_by_key.get(card.symbol_key)
    if existing is None:
        self.next_card_n += 1
        card.card_id = f"c{self.next_card_n}"
        card.surfaces = 1
        self.cards_by_key[card.symbol_key] = card
        return _format_full_card(card)         # ~70 tokens
    existing.surfaces += 1
    if existing.last_rev == now_rev:
        return f"[{existing.card_id}]"          # ~3 tokens raw, 6 cl100k
    return _format_delta(existing, now_rev)    # ~10-20 tokens
```

`redcon/compressors/symbols.py` -- the existing symbol extractor already produces enough signal (signature, range, score). Add a thin `build_card(extraction, file_path, session)` that calls `session.emit_or_ref(...)`. The first call returns the full card body inline; subsequent calls return the bracketed reference, which the agent learns to expand by calling a new `redcon_card_expand(card_id)` MCP tool when (and only when) it actually needs the body.

The compressor surface contract: when `RuntimeSession` is present in the request context (passed through `CompressorContext`), symbol-extraction output replaces in-file repeats of the same symbol with `[c3]` markers. When no session is present (CLI one-shot, V40-and-below callers), behaviour is identical to today -- this preserves determinism per cache key (the session is part of the key when active; absent when not).

`_meta.redcon` block (introduced in commit 257343) gains a `cards` list: `[{"id": "c3", "fq": "...", "surfaces": 4}]`, so the agent can introspect what is in the registry without parsing prose.

## Estimated impact
- Token reduction:
  - On the symbol-card slice itself: 64 tokens saved per extra surface, ~70% reduction at N=4 surfaces.
  - Whole-session: with 8 hot symbols x 4 surfaces avg, ~1,536 tokens saved per 30-turn session. On a session that totalled ~20 k input tokens (representative agent loop), this is ~7.7 percentage points off the session-aggregate -- crosses BASELINE.md's >=5 pp breakthrough threshold for a *new compounding dimension*.
  - Per-compressor reductions (file-side `redcon_compress` symbol extraction, MCP `redcon_search`): around 10-15 pp on 2nd-and-later emissions of the same symbol; 0 pp on first emission (correctly).
- Latency: cold path unchanged (session starts empty, first surface is identical to today). Warm path: +1 dict lookup per symbol surface, sub-microsecond. No regression on the BASELINE.md cold-start budget (no new imports on the hot path; SymbolCard dataclass is in already-imported `redcon/runtime/session.py`).
- Affects: `redcon/runtime/session.py` (registry), `redcon/compressors/symbols.py` (emit), `redcon/cmd/pipeline.py` (thread session through CompressorContext), `redcon/mcp/*` (new `redcon_card_expand` tool, `_meta.redcon.cards`), the per-process cache key (must include `session_id` when session-aware mode is active so a cache hit doesn't return content with stale card IDs).

## Implementation cost
- Lines of code: ~180 net new (60 in `session.py` for SymbolCard + emit_or_ref + format helpers, 50 in `symbols.py` for build_card glue, 40 in pipeline/MCP wiring, 30 for the `redcon_card_expand` tool stub).
- New runtime deps: none. Uses stdlib `dataclasses`, existing `hashlib` for symbol_key, existing tree-sitter-driven extractor in `redcon/symbols/tree_sitter.py` for callers/callees.
- Risks to determinism: the registry mutates within a session, so the cache key must include `session_id` *or* `(card_count, symbol_key_set)` so two runs of the same command at different points in a session don't collide. This is the same constraint V41 must already solve. Strict superset of the existing key (BASELINE.md constraint #6): preserved -- when no session, key is unchanged.
- Risks to must-preserve: `[c3]` is a *reference*, not a fact. The "must_preserve_patterns" regex tuples in `redcon/cmd/quality.py` operate on raw command output, not on rewritten symbol cards, so the existing harness is unaffected. But a new harness rule is needed: at COMPACT, the union of (cards-in-registry) and (cards-referenced-but-not-expanded-this-turn) must satisfy the original must-preserve patterns. This is doable but not free -- about 40 LoC of harness extension.
- Risks to robustness: if the agent forgets a card mid-session (its own context window evicted the prior turn), `[c3]` becomes meaningless. Mitigation: the `_meta.redcon.cards` block lets the agent rebuild a tiny lookup table cheaply. Also: cap registry at 32 cards, LRU-evict; on eviction, next surface re-emits a full card with a *new* id (never reuse `c3` for a different symbol).

## Disqualifiers / why this might be wrong
1. **Card content is already cheap because the symbol extractor is already good.** BASELINE.md notes the file-side pipeline already does symbol-extraction with scoring. If the agent only sees one symbol per file per call (because the scorer keeps the relevant one and drops siblings), the cross-call repeat rate is much lower than the 4x model assumes -- breakeven slips from p_k*T=1.09 to maybe p_k*T=1.5, and the head of the Zipf still wins but the tail eats the gain. Empirical session traces are needed; without them this is a hypothesis, not a measurement.
2. **Agents don't reliably treat references as references.** Many LLMs, when given `[c3]`, will still try to "explain" it inline, defeating the saving. This is the same failure mode V41 (path aliases) has to defend against. A self-instructing prefix in the system prompt ("`[cN]` means a previously-emitted symbol card; do not expand it unless you call redcon_card_expand") helps but adds ~25 prompt tokens once per session, which the math already bakes in -- but only marginally.
3. **It's overlapping with V41/V42/V43.** V41 does file-path aliases, V42 does hash-keyed shared dicts, V43 does RAG-style hot store. V49 is plausibly *a special case of V43 with a typed payload schema*, and the right move might be to skip a separate V49 implementation and instead let V43's generic K-V handle symbols (with a small formatter). If V43 lands first, V49 collapses to 30 LoC of "register a SymbolCard schema with the generic store" rather than its own subsystem -- which is fine but means V49's standalone Novelty drops from medium to low. This is the most likely trap.

## Verdict
- Novelty: medium (high relative to no-cards baseline; medium given V41/V42/V43 cover adjacent ground; the *typed schema and the diff-only re-emission* are the genuinely new bits)
- Feasibility: high (no new deps, RuntimeSession scaffolding already exists, symbol extractor already exists, cache-key extension is the main load)
- Estimated speed of prototype: 2-3 days for a working end-to-end on Python symbols only; +1 week for TS/Go via existing tree_sitter.py paths and for the quality-harness extension
- Recommend prototype: conditional-on-V43 (build V43 first; if V43's generic K-V exists, V49 is a small typed adapter on top and worth it; if V43 is not built, V49 should still ship but as part of the same subsystem rather than ahead of it)
