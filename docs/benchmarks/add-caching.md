# Agent Run Benchmark: add-caching

> **Task:** Add Redis caching to task lookup endpoints to reduce database load and improve response times

Evaluates how well ContextBudget selects the task service, route handlers, and repository layer when the goal is to introduce a caching layer.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 4 ms |
| Generated | 2026-03-15T11:35:09.132893+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,230 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,230 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,230 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 7,935 | 4,295 (35.1%) | medium | 19 ms |
| cache_assisted_pack | 2,255 | 9,975 (81.6%) | low | 17 ms |

## Compressed pack details

- **Baseline tokens:** 12,230
- **Optimized tokens:** 7,935 (64.9% of baseline)
- **Saved tokens:** 4,295 (35.1% reduction)
- **Quality risk:** medium
- **Files included:** 11

### Files included in packed context

- `README.md`
- `src/app.py`
- `src/config.py`
- `src/db/repository.py`
- `src/models/task.py`
- `src/routes/tasks.py`
- `src/routes/users.py`
- `src/services/task_service.py`
- `src/utils/helpers.py`
- `src/utils/validators.py`
- `tests/test_tasks.py`

### Files skipped

- `src/db/connection.py`
- `src/models/user.py`
- `src/services/user_service.py`
- `tests/test_users.py`

## Cache-assisted pack

Second run (warm cache): **2,255 tokens**, 11 cache hits, 17 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 24 | 27 | 24 *(fallback)* |
| top_ranked_file | 1615 | 1845 | 1615 *(fallback)* |
| packed_context | 7937 | 9071 | 7937 *(fallback)* |
