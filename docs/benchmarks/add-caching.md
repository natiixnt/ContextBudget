# Benchmark: add-caching

> **Task:** Add Redis caching to task lookup endpoints to reduce database load

Evaluates how well Redcon selects the task service, route handlers, and repository layer when the goal is to introduce a caching layer.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 10 ms |
| Generated | 2026-03-16T20:27:45.597681+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,228 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,228 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,228 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 2,962 | 9,266 (75.8%) | low | 25 ms |
| cache_assisted_pack | 150 | 12,078 (98.8%) | low | 22 ms |

## Compressed pack details

- **Input tokens:** 2,962 (24.2% of baseline)
- **Saved tokens:** 9,266 (75.8% reduction)
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

Second run (warm cache): **150 tokens**, 15 cache hits, 22 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 17 | 19 | 17 *(fallback)* |
| top_ranked_file | 1615 | 1845 | 1615 *(fallback)* |
| packed_context | 2965 | 3389 | 2965 *(fallback)* |
