# Agent Run Benchmark Dataset: Token Reduction Report

Reproducible evidence of token savings when using Redcon's optimised context selection versus naive full-repository context across four canonical agent run scenarios.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Dataset repo | `/home/dev/ContextBudget/benchmarks/dataset` |
| Generated | 2026-07-07T18:51:44.333100+00:00 |

## Results

| Task | Baseline tokens | Optimized tokens | Reduction |
|------|----------------|-----------------|-----------|
| [Add Caching](./add-caching.md) | 12,228 | 3,359 | 72.5% |
| [Add Authentication](./add-authentication.md) | 12,228 | 4,514 | 63.1% |
| [Refactor Module](./refactor-module.md) | 12,228 | 2,285 | 81.3% |
| [Add Rate Limiting](./add-rate-limiting.md) | 12,228 | 802 | 93.4% |

## Aggregate

| Metric | Value |
|--------|-------|
| Total baseline tokens | 48,912 |
| Total optimized tokens | 10,960 |
| Average baseline tokens | 12,228 |
| Average optimized tokens | 2,740 |
| **Average reduction** | **77.6%** |

## Task descriptions

### Add Caching

> Add Redis caching to task lookup endpoints to reduce database load and improve response times

- **Baseline:** 12,228 tokens (full repo, no selection)
- **Optimized:** 3,359 tokens (Redcon compressed pack)
- **Saved:** 8,869 tokens (72.5% reduction)

### Add Authentication

> Add JWT authentication middleware to protect task and user API routes and validate user sessions

- **Baseline:** 12,228 tokens (full repo, no selection)
- **Optimized:** 4,514 tokens (Redcon compressed pack)
- **Saved:** 7,714 tokens (63.1% reduction)

### Refactor Module

> Refactor the database repository layer to use connection pooling for better performance and separation of concerns

- **Baseline:** 12,228 tokens (full repo, no selection)
- **Optimized:** 2,285 tokens (Redcon compressed pack)
- **Saved:** 9,943 tokens (81.3% reduction)

### Add Rate Limiting

> Add rate limiting middleware to API endpoints to prevent abuse and ensure fair usage

- **Baseline:** 12,228 tokens (full repo, no selection)
- **Optimized:** 802 tokens (Redcon compressed pack)
- **Saved:** 11,426 tokens (93.4% reduction)

## How to reproduce

```bash
python benchmarks/build_agent_run_dataset.py
```

Override settings via environment variables:

```bash
BENCHMARK_MAX_TOKENS=16000 BENCHMARK_TOP_FILES=30 python benchmarks/build_agent_run_dataset.py
```

_Generated 2026-07-07_