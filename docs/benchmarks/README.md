# Benchmark Summary

## Large-scale benchmark — 1,000 tasks, 15 categories

Results from running Redcon against the Redcon codebase itself
(2,201,380 baseline tokens, token budget 32,000, top files 30).

| Metric | compressed_pack | cache_assisted_pack |
|--------|----------------|---------------------|
| Mean tokens | 2,565 | 496 |
| Median tokens | 1,283 | 300 |
| Mean savings | **99.9%** | **100.0%** |
| p10 savings | 99.7% | 100.0% |
| p50 savings | 99.9% | 100.0% |
| p95 savings | 100.0% | 100.0% |
| Quality risk low | 99.8% | - |
| Mean runtime | 304 ms | 304 ms |
| Tasks | 1,000 | 1,000 |
| Errors | 0 | 0 |

Categories: api, auth, caching, database, deployment, docs, error_handling,
events, logging, multitenancy, performance, refactoring, search, security, testing.

Raw JSON and reproducible runner: [`redcon-benchmarks/`](../../redcon-benchmarks/).

---

## Focused benchmark — 4 tasks on a small microservice

Results from running Redcon against a 15-file Python FastAPI service
(12,228 baseline tokens, token budget 8,000, top files 20).

Each task was measured with a cold cache (no prior summaries).
`cache_assisted_pack` shows savings when summaries from the prior run are reused.

| Task | Baseline | compressed_pack (cold) | Reduction | cache_assisted (warm) | Reduction |
|------|----------|-----------------------|-----------|-----------------------|-----------|
| [add-caching](./add-caching.md) | 12,228 | 7,827 | 36% | 1,459 | 88% |
| [add-authentication](./add-authentication.md) | 12,228 | 7,512 | 39% | 150 | 99% |
| [refactor-module](./refactor-module.md) | 12,228 | 7,831 | 36% | 610 | 95% |
| [add-rate-limiting](./add-rate-limiting.md) | 12,228 | 7,827 | 36% | 1,459 | 88% |
| **Average** | | | **37%** | | **92%** |

## How to reproduce

```bash
# 1,000-task benchmark (~14 min)
python redcon-benchmarks/run_large_benchmark.py

# 100-task sample (~90 s)
python redcon-benchmarks/run_large_benchmark.py --sample 100

# Single task via CLI
redcon benchmark "Add Redis caching to task lookup endpoints" \
    --repo . --max-tokens 32000
```

_Updated 2026-03-16 — Redcon v1.1.0_
