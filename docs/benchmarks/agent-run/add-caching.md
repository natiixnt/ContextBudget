# Agent Run Benchmark: add-caching

> **Task:** Add Redis caching to task lookup endpoints to reduce database load and improve response times

Evaluates how well Redcon selects the task service, route handlers, and repository layer when the goal is to introduce a caching layer.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 175 ms |
| Generated | 2026-07-07T18:51:42.810637+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,228 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,228 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,228 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 3,359 | 8,869 (72.5%) | low | 75 ms |
| cache_assisted_pack | 3,359 | 8,869 (72.5%) | low | 134 ms |

## Compressed pack details

- **Baseline tokens:** 12,228
- **Optimized tokens:** 3,359 (27.5% of baseline)
- **Saved tokens:** 8,869 (72.5% reduction)
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

Second run (warm cache): **3,359 tokens**, 30 cache hits, 134 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 24 | 27 | 24 *(fallback)* |
| top_ranked_file | 1615 | 1845 | 1615 *(fallback)* |
| packed_context | 3363 | 3843 | 3363 *(fallback)* |
