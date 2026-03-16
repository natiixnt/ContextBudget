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
| Generated | 2026-03-16T20:13:35.200605+00:00 |

## Baseline

Full repository context (no selection, no compression): **13,538 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 13,538 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 13,538 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 3,025 | 10,513 (77.7%) | low | 22 ms |
| cache_assisted_pack | 160 | 13,378 (98.8%) | low | 22 ms |

## Compressed pack details

- **Input tokens:** 3,025 (22.3% of baseline)
- **Saved tokens:** 10,513 (77.7% reduction)
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

Second run (warm cache): **160 tokens**, 17 cache hits, 22 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 18 | 20 | 18 *(fallback)* |
| top_ranked_file | 616 | 704 | 616 *(fallback)* |
| packed_context | 3029 | 3462 | 3029 *(fallback)* |
