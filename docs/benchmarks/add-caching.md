# Benchmark: add-caching

> **Task:** Add Redis caching to task lookup endpoints to reduce database load

Evaluates how well ContextBudget selects the task service, route handlers, and repository layer when the goal is to introduce a caching layer.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 4 ms |
| Generated | 2026-03-15T09:55:08.919756+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,230 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,230 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,230 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 7,937 | 4,293 (35.1%) | medium | 18 ms |
| cache_assisted_pack | 1,906 | 10,324 (84.4%) | low | 16 ms |

## Compressed pack details

- **Input tokens:** 7,937 (64.9% of baseline)
- **Saved tokens:** 4,293 (35.1% reduction)
- **Quality risk:** medium
- **Files included:** 11

### Files included in packed context

- `README.md`
- `src/app.py`
- `src/config.py`
- `src/db/connection.py`
- `src/db/repository.py`
- `src/models/task.py`
- `src/routes/tasks.py`
- `src/routes/users.py`
- `src/services/task_service.py`
- `src/utils/validators.py`
- `tests/test_tasks.py`

### Files skipped

- `src/models/user.py`
- `src/services/user_service.py`
- `src/utils/helpers.py`
- `tests/test_users.py`

## Cache-assisted pack

Second run (warm cache): **1,906 tokens**, 11 cache hits, 16 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 17 | 19 | 17 *(fallback)* |
| top_ranked_file | 1615 | 1845 | 1615 *(fallback)* |
| packed_context | 7939 | 9073 | 7939 *(fallback)* |
