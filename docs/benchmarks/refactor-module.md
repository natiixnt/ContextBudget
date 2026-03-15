# Benchmark: refactor-module

> **Task:** Refactor database repository layer to use connection pooling

Evaluates selection accuracy when the primary change targets the database connection module and its callers across services.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 3 ms |
| Generated | 2026-03-15T09:55:08.990661+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,230 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,230 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,230 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 1,768 | 10,462 (85.5%) | low | 15 ms |
| cache_assisted_pack | 150 | 12,080 (98.8%) | low | 15 ms |

## Compressed pack details

- **Input tokens:** 1,768 (14.5% of baseline)
- **Saved tokens:** 10,462 (85.5% reduction)
- **Quality risk:** low
- **Files included:** 15

### Files included in packed context

- `README.md`
- `src/app.py`
- `src/config.py`
- `src/db/connection.py`
- `src/db/repository.py`
- `src/models/task.py`
- `src/models/user.py`
- `src/routes/tasks.py`
- `src/routes/users.py`
- `src/services/task_service.py`
- `src/services/user_service.py`
- `src/utils/helpers.py`
- `src/utils/validators.py`
- `tests/test_tasks.py`
- `tests/test_users.py`

## Cache-assisted pack

Second run (warm cache): **150 tokens**, 15 cache hits, 15 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 15 | 18 | 15 *(fallback)* |
| top_ranked_file | 753 | 861 | 753 *(fallback)* |
| packed_context | 1774 | 2028 | 1774 *(fallback)* |
