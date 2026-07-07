# Benchmark Dataset: Token Reduction Report

Reproducible evidence of token savings when using Redcon's optimised context selection versus naive full-repository context.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Dataset repo | `/home/dev/ContextBudget/benchmarks/dataset` |
| Generated | 2026-07-07T18:51:46.614583+00:00 |

## Results

| Task | Baseline tokens | Optimized tokens | Reduction |
|------|----------------|-----------------|-----------|
| Add Caching | 12,228 | 2,091 | 82.9% |
| Add Authentication | 12,228 | 4,186 | 65.8% |
| Refactor Module | 12,228 | 1,675 | 86.3% |
| Add Rate Limiting | 12,228 | 802 | 93.4% |

## Aggregate

| Metric | Value |
|--------|-------|
| Total baseline tokens | 48,912 |
| Total optimized tokens | 8,754 |
| Average baseline tokens | 12,228 |
| Average optimized tokens | 2,188 |
| **Average reduction** | **82.1%** |

## Task descriptions

### Add Caching

> Add Redis caching to reduce redundant database queries and improve response times

- **Baseline:** 12,228 tokens (full repo, no selection)
- **Optimized:** 2,091 tokens (Redcon compressed pack)
- **Saved:** 10,137 tokens (82.9% reduction)

### Add Authentication

> Add JWT authentication middleware to protect API routes and validate user sessions

- **Baseline:** 12,228 tokens (full repo, no selection)
- **Optimized:** 4,186 tokens (Redcon compressed pack)
- **Saved:** 8,042 tokens (65.8% reduction)

### Refactor Module

> Refactor the database layer to use a repository pattern for better separation of concerns

- **Baseline:** 12,228 tokens (full repo, no selection)
- **Optimized:** 1,675 tokens (Redcon compressed pack)
- **Saved:** 10,553 tokens (86.3% reduction)

### Add Rate Limiting

> Add rate limiting middleware to API endpoints to prevent abuse and ensure fair usage

- **Baseline:** 12,228 tokens (full repo, no selection)
- **Optimized:** 802 tokens (Redcon compressed pack)
- **Saved:** 11,426 tokens (93.4% reduction)

## How to reproduce

```bash
python benchmarks/generate_dataset.py
```

Override settings via environment variables:

```bash
BENCHMARK_MAX_TOKENS=16000 BENCHMARK_TOP_FILES=30 python benchmarks/generate_dataset.py
```

_Generated 2026-07-07_