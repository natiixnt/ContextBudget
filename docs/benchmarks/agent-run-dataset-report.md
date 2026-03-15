# Agent Run Benchmark Dataset: Token Reduction Report

Reproducible evidence of token savings when using Redcon's optimised context selection versus naive full-repository context across four canonical agent run scenarios.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Dataset repo | `/Users/naithai/Desktop/amogus/praca/Redcon/benchmarks/dataset` |
| Generated | 2026-03-15T11:35:09.251810+00:00 |

## Results

| Task | Baseline tokens | Optimized tokens | Reduction |
|------|----------------|-----------------|-----------|
| [Add Caching](./add-caching.md) | 12,230 | 7,935 | 35.1% |
| [Add Authentication](./add-authentication.md) | 12,230 | 2,196 | 82.0% |
| [Refactor Module](./refactor-module.md) | 12,230 | 2,484 | 79.7% |
| [Add Rate Limiting](./add-rate-limiting.md) | 12,230 | 2,619 | 78.6% |

## Aggregate

| Metric | Value |
|--------|-------|
| Total baseline tokens | 48,920 |
| Total optimized tokens | 15,234 |
| Average baseline tokens | 12,230 |
| Average optimized tokens | 3,808 |
| **Average reduction** | **68.9%** |

## Task descriptions

### Add Caching

> Add Redis caching to task lookup endpoints to reduce database load and improve response times

- **Baseline:** 12,230 tokens (full repo, no selection)
- **Optimized:** 7,935 tokens (Redcon compressed pack)
- **Saved:** 4,295 tokens (35.1% reduction)

### Add Authentication

> Add JWT authentication middleware to protect task and user API routes and validate user sessions

- **Baseline:** 12,230 tokens (full repo, no selection)
- **Optimized:** 2,196 tokens (Redcon compressed pack)
- **Saved:** 10,034 tokens (82.0% reduction)

### Refactor Module

> Refactor the database repository layer to use connection pooling for better performance and separation of concerns

- **Baseline:** 12,230 tokens (full repo, no selection)
- **Optimized:** 2,484 tokens (Redcon compressed pack)
- **Saved:** 9,746 tokens (79.7% reduction)

### Add Rate Limiting

> Add rate limiting middleware to API endpoints to prevent abuse and ensure fair usage

- **Baseline:** 12,230 tokens (full repo, no selection)
- **Optimized:** 2,619 tokens (Redcon compressed pack)
- **Saved:** 9,611 tokens (78.6% reduction)

## How to reproduce

```bash
python benchmarks/build_agent_run_dataset.py
```

Override settings via environment variables:

```bash
BENCHMARK_MAX_TOKENS=16000 BENCHMARK_TOP_FILES=30 python benchmarks/build_agent_run_dataset.py
```

_Generated 2026-03-15_