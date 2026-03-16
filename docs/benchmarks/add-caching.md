# Benchmark: add-caching

> **Task:** Add Redis caching to task lookup endpoints to reduce database load

Evaluates how well Redcon selects the task service, route handlers, and repository layer when the goal is to introduce a caching layer.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 12 ms |
| Generated | 2026-03-16T20:13:35.148681+00:00 |

## Baseline

Full repository context (no selection, no compression): **13,538 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 13,538 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 13,538 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 4,295 | 9,243 (68.3%) | low | 24 ms |
| cache_assisted_pack | 160 | 13,378 (98.8%) | low | 21 ms |

## Compressed pack details

- **Input tokens:** 4,295 (31.7% of baseline)
- **Saved tokens:** 9,243 (68.3% reduction)
- **Quality risk:** low
- **Files included:** 16

### Files included in packed context

- `.contextbudget_cache.json`
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

Second run (warm cache): **160 tokens**, 17 cache hits, 21 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 17 | 19 | 17 *(fallback)* |
| top_ranked_file | 1615 | 1845 | 1615 *(fallback)* |
| packed_context | 4298 | 4912 | 4298 *(fallback)* |
