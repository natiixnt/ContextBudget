# Benchmark: add-authentication

> **Task:** Add JWT authentication middleware to protect task and user API routes

Evaluates context selection for an auth-focused change spanning route handlers, user model, and application bootstrap.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 6 ms |
| Generated | 2026-03-16T20:16:31.070262+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,228 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,228 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,228 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 3,015 | 9,213 (75.3%) | low | 23 ms |
| cache_assisted_pack | 150 | 12,078 (98.8%) | low | 23 ms |

## Compressed pack details

- **Input tokens:** 3,015 (24.7% of baseline)
- **Saved tokens:** 9,213 (75.3% reduction)
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

Second run (warm cache): **150 tokens**, 15 cache hits, 23 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 18 | 20 | 18 *(fallback)* |
| top_ranked_file | 616 | 704 | 616 *(fallback)* |
| packed_context | 3019 | 3450 | 3019 *(fallback)* |
