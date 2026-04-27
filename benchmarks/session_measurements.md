# Session-trace measurements

Empirical signals captured by running 5 synthetic agent sessions over this repo. Used to gate the cross-call / cross-content vectors that V42-V49 / V25-V26 research left as 'measure first'.

## Aggregate

| Metric | Value |
|---|---|
| Sessions simulated | 5 |
| Avg calls per session | 6.4 |
| Avg argv repeat rate | 20.0% |
| Avg cache hit rate | 20.0% |
| Avg 3-line shingle overlap | 23.1% |
| Avg distinct paths / session | 13.2 |
| Avg repeated path refs / session | 5.4 |
| Avg distinct symbols / session | 94 |
| Avg recurring symbol refs / session | 67 |
| Total raw tokens | 14,299 |
| Total compressed tokens | 6,342 |

## Per-vector verdicts based on session measurements

### V41 path aliases (already shipped)
- 5.4 repeated path mentions per session over 13.2 distinct paths -> 41% of paths show up >=2 times. Per the V41 model the per-call saving is ~6 cl100k tokens per repeat, ~32 tokens / session.

### V42/V43 cross-content dedup
- 3-line shingle overlap across calls in the same session: 23.1%.
- Verdict: SHIP candidate (>=5% cross-call line overlap).

### V49 symbol cards
- 67 recurring symbol references per session over 94 distinct symbols. V49's break-even is per-symbol freq >= 2; 72% of symbols cross the bar. SHIP candidate.

### V25/V26 Markov prefetch / replay
- argv repeat rate: 20.0%, cache hit rate: 20.0%.
- Verdict: SKIP (low argv repeat rate).

### V47 snapshot delta (already shipped)
- argv repeat rate 20.0% (each repeat is a potential V47 swap if jaccard >= 0.30).
- The schema-aware renderers (pytest/git_diff/coverage) cover the highest-traffic repeat schemas in the simulated sessions.