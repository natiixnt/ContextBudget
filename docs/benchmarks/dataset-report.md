# Benchmark Dataset: Token Reduction Report

Reproducible evidence of token savings when using ContextBudget's optimised context selection versus naive full-repository context.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Dataset repo | `/Users/naithai/Desktop/amogus/praca/ContextBudget/benchmarks/dataset` |
| Generated | 2026-03-15T11:05:45.147657+00:00 |

## Results

| Task | Baseline tokens | Optimized tokens | Reduction |
|------|----------------|-----------------|-----------|
| Add Caching | 12,230 | 7,860 | 35.7% |
| Add Authentication | 12,230 | 6,609 | 46.0% |
| Refactor Module | 12,230 | 3,743 | 69.4% |
| Add Rate Limiting | 12,230 | 919 | 92.5% |

## Aggregate

| Metric | Value |
|--------|-------|
| Total baseline tokens | 48,920 |
| Total optimized tokens | 19,131 |
| Average baseline tokens | 12,230 |
| Average optimized tokens | 4,782 |
| **Average reduction** | **60.9%** |

## Task descriptions

### Add Caching

> Add Redis caching to reduce redundant database queries and improve response times

- **Baseline:** 12,230 tokens (full repo, no selection)
- **Optimized:** 7,860 tokens (ContextBudget compressed pack)
- **Saved:** 4,370 tokens (35.7% reduction)

### Add Authentication

> Add JWT authentication middleware to protect API routes and validate user sessions

- **Baseline:** 12,230 tokens (full repo, no selection)
- **Optimized:** 6,609 tokens (ContextBudget compressed pack)
- **Saved:** 5,621 tokens (46.0% reduction)

### Refactor Module

> Refactor the database layer to use a repository pattern for better separation of concerns

- **Baseline:** 12,230 tokens (full repo, no selection)
- **Optimized:** 3,743 tokens (ContextBudget compressed pack)
- **Saved:** 8,487 tokens (69.4% reduction)

### Add Rate Limiting

> Add rate limiting middleware to API endpoints to prevent abuse and ensure fair usage

- **Baseline:** 12,230 tokens (full repo, no selection)
- **Optimized:** 919 tokens (ContextBudget compressed pack)
- **Saved:** 11,311 tokens (92.5% reduction)

## How to reproduce

```bash
python benchmarks/generate_dataset.py
```

Override settings via environment variables:

```bash
BENCHMARK_MAX_TOKENS=16000 BENCHMARK_TOP_FILES=30 python benchmarks/generate_dataset.py
```

_Generated 2026-03-15_