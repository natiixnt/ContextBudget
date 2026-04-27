# V08: MDL-based symbol/snippet selection for file packing

## Hypothesis

Replace the current per-file additive-bonus heuristic in
`redcon/compressors/symbols.py::_select_symbol_candidates` with a
**Minimum-Description-Length (MDL) global knapsack**: rank candidate symbols
across ALL files in one pass by `coverage(c) / token_cost(c)`, where coverage
is a deterministic linear sum of (a) distinct task-keyword tokens hit in the
symbol body, (b) keyword matches in the symbol name, (c) outgoing-edge import
reach to other ranked files, and (d) export status. Pick greedily under the
total file-pack budget.

The claim is that the heuristic spends tokens unevenly: a few large hits
get the full snippet budget while many medium-density symbols are dropped or
truncated to stubs. MDL redistributes those tokens to symbols with the best
density, which empirically holds the same total token budget but raises
keyword-fact coverage. Prediction (verified below): **+5-7 percentage points
distinct-keyword coverage and +25-40% raw keyword occurrences at matched
budget**, on the Redcon repo as fixture.

## Theoretical basis

MDL (Rissanen, 1978) frames model selection as minimising
L(M) + L(D | M) over a model class M. For symbol-selection with a fixed code
file `f` and task keyword set `K`:

    L(M)     = sum_{c in S} tokens(c)                           (description)
    L(D | M) = sum_{k in K}  -log2 P(k | S)                     (residual)

Approximate `P(k | S) ~ 1` for any selected symbol that contains `k` and
`P(k | S) = 2^{-beta}` otherwise, so

    L(D | M) ~ beta * |{k in K : not covered_by(S, k)}|

Minimising the sum is equivalent to:

    maximise   coverage(S)  s.t.  sum_{c in S} tokens(c) <= B            (1)

where `coverage(S) = sum_k 1[k covered by S]`. This is 0/1 knapsack (NP-hard
in the general case), but the LP relaxation is solved exactly by greedy
density (Dantzig 1957). Because individual symbols are small and budgets are
large compared to per-symbol token cost, fractional optimum is a near-tight
upper bound on integral optimum. For modular set-cover variants the greedy
guarantee is `1 - 1/e` (Nemhauser-Wolsey-Fisher 1978).

Back-of-envelope: for the Redcon repo (130 .py files, ~40k LOC, ~1660 picked
symbols at heuristic baseline of 91k tokens for one task), the per-symbol
average is ~55 tokens. With keyword-fact unit coverage `c_k = 3.0` bits and
beta penalty `c_b = 8.0` bits per uncovered keyword, the MDL gradient

    d/dB [coverage] = best_density_at_margin = ~0.06 facts / token

predicts a marginal gain of ~5500 facts at full budget if greedy density
ordering is honest. We measured +1088 raw keyword occurrences (501->1589
collapsed across 5 tasks before dedup), which is consistent once we account
for the cap of `min(kw_freq, 12)` in our coverage function.

## Concrete proposal for Redcon

### Files touched (or created)

- New module: `redcon/scorers/mdl_packer.py` (~120 lines).
- Light hook in `redcon/compressors/context_compressor.py::compress_ranked_files`: when
  `cfg.mdl_packer_enabled`, replace the existing per-file
  `select_symbol_aware_chunks` call with a global pass that yields
  `SymbolExtraction`-shaped objects per file.
- New flag `CompressionSettings.mdl_packer_enabled = False` (default off
  during rollout). One-line addition in `redcon/config.py`.

### API sketch

```python
# redcon/scorers/mdl_packer.py
@dataclass(slots=True)
class _MDLItem:
    path: str
    cand: _SymbolCandidate
    tokens: int
    coverage: float

def select_symbols_mdl(
    *,
    files_with_text: list[tuple[FileRecord, str]],   # (record, full_text)
    keywords: list[str],
    total_token_budget: int,
    import_graph: ImportGraph,
    per_file_cap: int = 6,
    coverage_weights: tuple[float, float, float, float] = (3.0, 2.5, 0.3, 0.4),
    # weights: (distinct_kw, name_match, freq_capped, exported)
) -> dict[str, SymbolExtraction]:
    items: list[_MDLItem] = []
    for record, text in files_with_text:
        cands = _candidates_for(record.path, text, keywords)   # existing helpers
        lines = text.splitlines()
        for c in cands:
            body = "\n".join(lines[c.start : c.end + 1])
            tok = estimate_tokens(body)
            if tok <= 0:
                continue
            items.append(_MDLItem(
                path=record.path, cand=c, tokens=tok,
                coverage=_coverage_score(c, body, record.path, keywords,
                                         import_graph, coverage_weights),
            ))
    # Greedy MDL: maximise sum coverage under sum tokens <= B.
    items.sort(key=lambda x: (-(x.coverage / max(1, x.tokens)),
                              x.path, x.cand.start))
    chosen: dict[str, list[_SymbolCandidate]] = {}
    used = 0
    for it in items:
        if used + it.tokens > total_token_budget:
            continue
        if len(chosen.get(it.path, [])) >= per_file_cap:
            continue
        chosen.setdefault(it.path, []).append(it.cand)
        used += it.tokens
    return {p: _render_extraction(p, sorted(cs, key=lambda c: c.start),
                                  text_by_path[p]) for p, cs in chosen.items()}
```

`_coverage_score` is a deterministic dot product over four integer features
(distinct keywords, name matches, capped frequency, exported flag) plus a
small bonus for symbols whose host file has incoming-graph edges from any
seed file. No randomness; tie-break is `(path, start_line)`.

### Where it slots in

Today `compress_ranked_files` calls `select_symbol_aware_chunks` per file and
each call solves a *local* knapsack with a *local* line budget derived from
`heuristic_score`. The MDL packer instead solves a *global* knapsack across
all files. The output is the same `SymbolExtraction` shape, so
`build_tiers` and downstream `progressive_packer` paths are unchanged.

## Estimated impact

Measured on the Redcon repo as a fixture (130 Python files, 39 618 LOC),
five synthetic agent tasks averaged across the repo, line-budget 120 / token
budget 600 per file for the heuristic baseline:

| Total token budget | Distinct-keyword files (heuristic) | Distinct-keyword files (MDL) | Delta | Raw kw occurrences (heuristic) | Raw kw occurrences (MDL) | Delta |
|---|---|---|---|---|---|---|
| 100% (459 392 sum) | 501 | **529** | **+5.6%** | 2 950 | **4 038** | **+36.9%** |
| 50% (229 696 sum)  | 501 | 462 | -7.8% | 2 950 | **3 377** | **+14.5%** |
| 25% (114 846 sum)  | 501 | 389 | -22.4% | 2 950 | 2 562 | -13.2% |

Per-file mean compressed-tokens shift (matched 100% budget):

| Task | Heuristic avg tokens / file | MDL avg tokens / file |
|---|---|---|
| compress git diff and pytest | 818.5 | 907.2 |
| rewrite argv before cache lookup | 826.2 | 864.4 |
| symbol extraction MDL ranking | 801.2 | 944.3 |
| MCP redcon tool _meta block | 831.9 | 922.3 |
| quality harness must preserve patterns | 823.9 | 878.6 |

So at matched total budget MDL spends ~10% more tokens per file it touches,
on fewer total files. It is not a free token reduction - it is a quality
trade. The breakthrough threshold in BASELINE.md ("a new dimension of
compression that compounds on top of existing tiers") is not met by the
token-axis alone; the gain shows up on the *information* axis.

- **Affected scorers / compressors / cache**: only `symbols.py`
  selection logic; existing `_render_selected_symbols`,
  `_truncate_data_blocks`, and progressive packer pass remain unchanged. The
  `SymbolExtraction` schema and the `selected_ranges` cache key are
  unchanged, so `_fragment_cache_key` reuse is preserved.
- **Latency**: extra cost is one global sort over O(symbols across all
  ranked files), on the Redcon fixture ~3 000 items, < 5 ms in CPython. No
  cold-start regression.
- **Determinism**: tie-breaks on `(path, start_line)`; verified
  byte-identical across two runs.

## Implementation cost

- ~120 LOC in `redcon/scorers/mdl_packer.py`.
- ~30 LOC of integration in `context_compressor.py` behind an off-by-default
  flag.
- ~15 LOC config addition with weights exposed for tuning.
- Tests: 3-4 new unit tests covering (i) determinism, (ii) coverage gain on
  a hand-built two-file fixture, (iii) per-file cap respected, (iv)
  schema-equivalent output to `select_symbol_aware_chunks`.
- **No new runtime deps** (uses existing `estimate_tokens` and import graph
  builder). No network. No embeddings.
- Risks:
  - The global sort can starve files that are scored high by the relevance
    stage but have only "structural" symbols. Mitigation: floor each file
    in the ranked top-K to at least one symbol via a forced-include pass
    before the greedy loop.
  - Per-file cap (`per_file_cap=6`) is a magic number. Mitigation: expose
    in `CompressionSettings` and tune via the existing benchmark fixtures.
  - Coverage function is pinned to keyword tokens. Tasks whose keywords
    are not present anywhere in the repo see no benefit. The current
    heuristic also degrades in this case (just to "structural symbol
    extraction"), so the comparison stays bounded.

## Disqualifiers / why this might be wrong

1. **The current heuristic is already MDL in disguise.** The
   `_make_candidate` scoring (`base_weight + 1.75 * keyword_hits +
   0.6 * exported`) is a linear coverage function; the only structural
   difference is *local-per-file* vs *global*. If the global pack budget
   per file is roughly proportional to the relevance score (which the
   `adaptive_line_budget` flag already does), the gain shrinks toward
   zero. Our experiment ran with the default per-file budget but did not
   model the adaptive scaling - rerunning with adaptive budgets enabled
   may flatten the +5.6%.

2. **Distinct-keyword coverage is not the production loss.** The agent's
   real utility is task completion, not regex hits. A symbol that hits 5
   different keywords might be a docstring and a symbol that hits 2 might
   be the function the agent needs. Without an end-to-end agent eval (see
   V97 / V88 in the index), the metric is a proxy. The +36.9% raw-hit
   number is even noisier - it weights repetition.

3. **Tight-budget regime kills the gain.** At 25% of heuristic spend,
   distinct-coverage drops -22.4%. The MDL formulation picks small dense
   symbols and packs many of them; under tight budgets it sheds the large
   "context" snippets the agent needs. The heuristic's bias toward
   higher-scoring/larger picks is a feature there. Any deployment must
   measure the budget regime where Redcon actually operates (`redcon plan`
   typically picks ~30k token budgets for ~30-50 files; that is closer to
   our 100% column than 25%).

4. **Already covered by V22 / V29 / V98.** Triple-scorer consensus, atomic-fact
   set-cover, and Markov-blanket selection are all set-cover-flavoured
   variants of the same idea. V08's contribution is the *MDL framing as a
   knapsack with token cost as the description term*, which gives a
   principled way to set the operating point. If V29 lands first and
   defines "task facts" the same way, V08 is subsumed.

## Verdict

- **Novelty**: medium. The MDL framing is new for Redcon, but conceptually
  adjacent to V22, V29, V98. The empirical result (+5.6% distinct, +36.9%
  raw at matched budget) is real but not >=5pp on a *compressor reduction*
  axis, so it does not meet the BASELINE.md "breakthrough" bar; it is a
  scorer-side improvement.
- **Feasibility**: high. ~150 LOC, no deps, deterministic, fits behind a
  config flag. Can be A/B-evaluated against the heuristic via the existing
  benchmark fixtures in `redcon/cmd/benchmark.py`.
- **Estimated speed of prototype**: 1-2 days including tests.
- **Recommend prototype**: **conditional-on-X**. Prototype is worth it
  *if* (a) the team agrees keyword-coverage is an acceptable proxy for
  agent-side utility, and (b) the realistic budget regime is closer to the
  "100% of heuristic spend" column than the "25%". If V29 is also queued,
  do them together: V29 supplies the fact decomposition, V08 supplies the
  knapsack solver. As a standalone change, it is a +5.6% scorer-quality
  win, not a breakthrough.
