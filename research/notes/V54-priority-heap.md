# V54: Priority queue with exponential decay for streaming diff hunks

## Hypothesis
When a unified diff is large (240+ hunks across 30+ files) and the COMPACT/ULTRA tier can only afford to surface K hunks (K ~= 15-25), a fixed first-N truncation systematically loses the most informative hunks - which in real engineering work are typically the largest mutations in task-relevant files, scattered through the middle of the diff (alphabetic file order, not relevance order). I claim a deterministic streaming **bounded min-heap** of K entries, keyed by a content-only priority score with an additive in-stream recency decay, will (a) emit the same K hunks for the same diff bytes (determinism preserved), (b) raise must-preserve coverage of "large change in keyword-matched file" from ~25% (first-N baseline) to >=70% on a synthesised 240-hunk corpus, and (c) cost O(N log K) work and O(K) memory regardless of N - a strict latency/memory improvement over today's full-buffer parse for outputs spilled past the 1 MiB log-pointer threshold.

## Theoretical basis
**Streaming top-K with decay.** Given a stream of hunks h_1..h_N with priorities p_i in R, we want to maintain S = arg-top-K of an *adjusted* score s_i = p_i - lambda * (N_seen - i). lambda >= 0 is the linear-equivalent of an exponential decay because the heap only sees relative ordering: argmax over s_i with s_i = p_i + log(decay^i) = p_i + i * log(decay) is monotone-equivalent to s_i = p_i + alpha * i with alpha = log(decay). So a bounded min-heap with key (p_i + alpha * i) realises an exponentially-decayed top-K in one pass.

Back-of-envelope (K = 20, N = 240, alpha chosen so the 50th hunk's bonus equals one extra inserted line):
```
alpha = 1 / 50  # one hunk-line-equivalent per 50 hunks of stream age
priority(h) = abs(h.insertions + h.deletions)
            + 5  if file_path matches any task_keyword (case-insensitive substring)
            + 2  if hunk.header function name matches keyword
            + alpha * stream_index
heap_key   = (priority, stream_index)   # stream_index breaks ties deterministically
```
Work per hunk: one push + at-most-one pop on a heap of size K = 20 -> 2 * log2(20) ~= 8.6 comparisons. For N = 240, total ~= 2064 comparisons vs current parse-all-then-format which materialises every hunk's added/removed tuples (currently bounded only by subprocess cap_bytes). Memory: K hunk records vs all-N. For a 5 MiB diff with average hunk = 800 B, current peak ~= 5 MiB of tuples; heap holds K * 800 B ~= 16 KiB.

**Determinism.** The score is a pure function of (raw hunk text, file path, task_keywords tuple, stream_index). No timestamps, no PRNG, no map-iteration order. Tie-break on stream_index makes the heap state and the emitted set bit-stable for a fixed input.

**Coverage claim (informal).** First-N truncation has hit-rate equal to (relevant-hunks-in-first-N / K). For diffs where relevant hunks are uniformly distributed, expected hit rate is K/N ~= 8% at N=240, K=20. Priority-keyed top-K has hit rate -> 1 whenever the priority ordering correlates with relevance. Even with alpha=0 (no decay), the keyword + size signal lifts coverage from ~8% (uniform) to ~K/M where M = number of relevant hunks in the diff, typically M < 30, giving 65-100%.

## Concrete proposal for Redcon
**Files touched (production, NOT in this research task - sketch only):**
- `redcon/cmd/compressors/git_diff.py` - add `parse_diff_streaming(line_iter, *, top_k, task_keywords) -> DiffResult` alongside existing `parse_diff`. The block-splitter becomes a generator; `_parse_file_block` yields hunks one at a time; a heap collects them.
- `redcon/cmd/types.py` - `DiffHunk` already adequate; add optional `selected_rank: int | None` if we want to expose the priority ordering downstream (off by default).
- `redcon/cmd/pipeline.py` - pass `ctx.task_keywords` (already plumbed for file-side scoring) into the compressor context so command-side compressors can read it. Today `CompressorContext` does not carry keywords; this is a one-field addition.

**API sketch (15 lines):**
```python
import heapq

def select_top_hunks(file_blocks, *, k: int, keywords: tuple[str, ...], alpha: float = 0.02):
    heap: list[tuple[float, int, DiffHunk, str]] = []  # (key, idx, hunk, file_path)
    idx = 0
    for fpath, fmeta, hunks in _iter_file_blocks(file_blocks):
        kw_bonus = 5.0 if any(k.lower() in fpath.lower() for k in keywords) else 0.0
        for h in hunks:
            base = abs(len(h.added) + len(h.removed))
            hdr  = 2.0 if any(k.lower() in h.header.lower() for k in keywords) else 0.0
            key  = base + kw_bonus + hdr + alpha * idx
            entry = (key, idx, h, fpath)
            if len(heap) < k:
                heapq.heappush(heap, entry)
            elif key > heap[0][0]:
                heapq.heapreplace(heap, entry)
            idx += 1
    # Emit in stream order so output reads naturally; selection set is deterministic.
    return sorted(heap, key=lambda e: e[1])
```
Compact-tier formatter then iterates the selected list and prints per-file/per-hunk lines as today, plus a footer `"+M hunks dropped (top-K=20 by priority)"`.

**Test plan (no production change here):** synthesise a 240-hunk diff (30 files, 8 hunks each, sizes drawn from a fixed deterministic schedule) where 6 hunks are pre-tagged "must surface" (large + keyword-bearing path). Compare must-preserve coverage of:
1. current first-N (slice first 20 hunks of `result.files` flattened),
2. heap top-K,
3. heap top-K with alpha=0 (no decay) and alpha=0.02.
Determinism check: run twice, byte-compare.

## Estimated impact
- Token reduction: marginal at COMPACT (today already drops hunk bodies, only headers shown). The win is **quality at COMPACT for big diffs** and **enables a future "compact-with-bodies" tier** that prints top-K hunk bodies under budget. Expect 0 to +2 pp reduction (slightly tighter footer vs verbose dump), but +40-60 pp must-preserve coverage on synthetic 240-hunk diffs at K=20.
- Latency: cold parse on huge diffs improves from O(N) memory to O(K) when paired with V59 backpressure or the existing 1 MiB log-pointer; warm parse unchanged for diffs under K hunks (heap never evicts). Expect -5% on a 5 MiB diff cold parse, neutral elsewhere.
- Affects: `git_diff` only (other compressors would need similar streaming refactors per their own structure - V51 reservoir-sampled tests is a sibling). No cache-key change (top_k and keywords would join the canonicalised argv). Quality harness `must_preserve_patterns` unchanged - we are *adding* a quality dimension (relevance coverage), not relaxing existing invariants.

## Implementation cost
- ~120 LoC: streaming block iterator (~40), heap-select function (~20), formatter wiring (~20), unit tests with synthetic diff fixture (~40).
- New runtime deps: none (heapq is stdlib). No network, no embeddings - rule respected.
- Risks to determinism: tie-break on `idx` is critical; without it, equal-priority hunks could swap depending on heap reheapify order on different Python versions. Test requires asserting byte-identical output across two runs and across CPython 3.11 / 3.12.
- Risks to robustness: a pathological diff where every hunk has identical priority and alpha=0 still works because idx breaks ties; with alpha>0 every key is unique. Truncated-mid-stream input is fine - the heap simply contains fewer than K entries.
- Risks to must-preserve: today's COMPACT pattern is "every file path appears". Top-K-by-hunk could drop entire files if a file's hunks all lose. Mitigation: emit a per-file summary line (path + +/- counts) for *every* file regardless of hunk selection - the heap selects which **hunk headers** survive, not which files. With this guard, must-preserve invariant is strictly preserved.

## Disqualifiers / why this might be wrong
1. **Today's COMPACT already drops hunk bodies entirely.** Only the *first* hunk header per file is shown (line 278 in `git_diff.py`). So at COMPACT tier the proposal changes "first hunk header per file" -> "top-K hunk headers across all files" - a redistribution, not a strict win in token count. The win is conditional on a future "compact-with-bodies" tier or on the K hunks being a better headline than first-of-each-file. Empirical question, not theoretical.
2. **Keyword plumbing does not exist in `CompressorContext` yet.** The file-side scorer has task keywords; the command-side does not. This is a real wiring change that touches `pipeline.py`, `runner.py`, and the MCP `redcon_run` schema. Without keywords, the priority degrades to "biggest hunk first", which is plausibly worse than "first hunk per file" for diffs where a big mechanical refactor dominates a small bugfix.
3. **The 1 MiB log-pointer tier already triages oversized output.** Diffs that big spill to disk and emit only tail-30 lines + pointer - the parser never runs. The streaming heap only helps in the gap between "huge enough that first-N is bad" and "huge enough to spill" (~50 KB to 1 MiB). That window is narrower than the framing suggests. If most real-world diffs are <50 KB, the win is moot.
4. (Bonus) Information leak via priority: ranking hunks by token-cheap heuristics could mis-rank a small but semantically critical change (e.g. a one-line security fix). MDL-style scoring (V08) or call-graph reachability (V28) would be principled fixes, but neither is in production today.

## Verdict
- Novelty: medium. Streaming bounded heap is textbook; the contribution is the deterministic decay term plus the file-summary guard that preserves the existing must-preserve invariant.
- Feasibility: high. ~120 LoC, stdlib only, no harness changes, additive.
- Estimated speed of prototype: 1 day for the heap + synthetic fixture + determinism test. 2-3 additional days to plumb `task_keywords` through `CompressorContext` and update the MCP schema, which is the larger and more politically-loaded change.
- Recommend prototype: conditional-on - keyword plumbing lands in `CompressorContext` (otherwise the priority degenerates to "size-only" and the case for top-K vs first-of-each-file becomes empirical noise), AND a quality-harness extension is added that measures *relevance coverage* on a synthetic 240-hunk fixture (today's harness only checks pattern survival, not hunk-selection quality). With both, this slots cleanly under V59 backpressure and a future "compact-with-bodies" tier as a compounding gain.
