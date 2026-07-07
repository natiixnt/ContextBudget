# context-eval results

Repository: `ContextBudget` | Token budget: 24,000 | Tasks: 33 (from real git history) | Generated: 2026-07-07

## Aggregate

| Tool | Mean coverage | Median coverage | Mean tokens used | Tokens / coverage point |
|------|--------------:|----------------:|-----------------:|------------------------:|
| `redcon` | **43.8%** | 50.0% | 23,978 | 306.8 |
| `keyword-topk` | **29.8%** | 0.0% | 23,941 | 538.9 |
| `aider-repomap` | **15.3%** | 0.0% | 23,999 | 533.3 |
| `pagerank` | **11.4%** | 0.0% | 23,998 | 720.0 |
| `random` | **5.8%** | 0.0% | 23,999 | 690.0 |
| `full-dump` | **0.0%** | 0.0% | 23,999 | n/a |

Coverage: share of the files the task's real commit modified that the tool placed inside the budget. Tokens per coverage point: mean of per-task `tokens_used / coverage`; lower is cheaper evidence.

## Per-task coverage

| Task | GT files | `redcon` | `keyword-topk` | `aider-repomap` | `pagerank` | `random` | `full-dump` |
|------|---------:|---:|---:|---:|---:|---:|---:|
| repair pre-existing test_repo_map_respects_budget flake | 1 | 0% | 0% | 0% | 0% | 0% | 0% |
| re-measure sessions with V41+V43+V49 cross-call layers en... | 1 | 100% | 100% | 0% | 0% | 0% | 0% |
| V49 session-scoped symbol aliaser | 2 | 0% | 0% | 0% | 0% | 0% | 0% |
| V43 session-scoped content reference ledger | 2 | 50% | 0% | 0% | 0% | 0% | 0% |
| pre-compile remaining inline regex patterns (V78 follow-up) | 3 | 0% | 33% | 0% | 0% | 0% | 0% |
| extract skeleton-clustering helper to _skeletons module | 1 | 0% | 0% | 0% | 0% | 0% | 0% |
| bundle stats compressor for webpack/esbuild (V63) | 4 | 0% | 25% | 0% | 25% | 0% | 0% |
| lint rule-pivot COMPACT layout with min-gate (V62) | 1 | 100% | 100% | 0% | 0% | 0% | 0% |
| stratified file sampling for >30 test failures (V51) | 1 | 100% | 0% | 0% | 0% | 0% | 0% |
| close V85 residuals on 4 schemas, enable full ENFORCE | 5 | 0% | 20% | 0% | 0% | 40% | 0% |
| make V85 fuzzer per-test seed deterministic across runs | 1 | 100% | 100% | 0% | 0% | 0% | 0% |
| coverage delta renderer for V47 dispatcher (#114) | 3 | 100% | 100% | 33% | 0% | 0% | 0% |
| SQL EXPLAIN ANALYZE compressor (V61) | 4 | 0% | 25% | 50% | 25% | 25% | 0% |
| coverage report compressor (V69) | 4 | 0% | 0% | 50% | 25% | 25% | 0% |
| NDJSON log compressor with schema-mining (V65) | 4 | 0% | 25% | 50% | 25% | 0% | 0% |
| profiler collapsed-stack compressor (V70) | 4 | 0% | 50% | 0% | 25% | 0% | 0% |
| schema-aware V47 deltas for pytest + git_diff | 5 | 80% | 40% | 20% | 0% | 0% | 0% |
| git_status header inflation fallback (V85 finding 3) | 1 | 100% | 0% | 100% | 0% | 0% | 0% |
| align ls/tree/find must-preserve patterns with formatter ... | 1 | 100% | 100% | 0% | 0% | 0% | 0% |
| align git_log parser+regex+formatter (V85 finding 1) | 1 | 100% | 0% | 100% | 0% | 0% | 0% |
| snapshot-delta framework + git_status hookup (V47) | 1 | 0% | 0% | 0% | 0% | 100% | 0% |
| session-scoped path aliases at egress (V41) | 1 | 0% | 0% | 0% | 0% | 0% | 0% |
| cluster duplicate test failures with min-gate (V64) | 1 | 100% | 0% | 0% | 0% | 0% | 0% |
| specialise kubectl events compressor | 2 | 50% | 50% | 50% | 50% | 0% | 0% |
| stamp invariant-cert sha prefix on compact/verbose | 2 | 50% | 0% | 0% | 50% | 0% | 0% |
| inject NO_COLOR + strip ANSI escapes pre-compress | 2 | 0% | 0% | 0% | 0% | 0% | 0% |
| tokenizer-aware substitution table for compact/ultra | 1 | 0% | 0% | 0% | 0% | 0% | 0% |
| tighten ', ' and ': ' whitespace post-normalise | 1 | 100% | 0% | 0% | 0% | 0% | 0% |
| memoise compiled regex in verify_must_preserve | 1 | 100% | 100% | 0% | 100% | 0% | 0% |
| --baseline gating for cmd-bench + bundled cmd_baseline.js... | 2 | 50% | 50% | 0% | 0% | 0% | 0% |
| blend PageRank with engine ranker (T4.A3) | 1 | 0% | 0% | 0% | 0% | 0% | 0% |
| extract_imports() across Python/TS/JS/Rust/Go/Java/Ruby/C... | 3 | 67% | 67% | 0% | 0% | 0% | 0% |
| LLMLingua-2 opt-in semantic fallback (T3.D) | 2 | 0% | 0% | 50% | 50% | 0% | 0% |
