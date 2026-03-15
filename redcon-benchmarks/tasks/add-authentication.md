# Agent Run Benchmark: add-authentication

> **Task:** Add JWT authentication middleware to protect task and user API routes and validate user sessions

Evaluates context selection for an auth-focused change spanning route handlers, user model, and application bootstrap.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 3 ms |
| Generated | 2026-03-15T11:35:09.173424+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,230 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,230 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,230 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 2,196 | 10,034 (82.0%) | low | 17 ms |
| cache_assisted_pack | 150 | 12,080 (98.8%) | low | 18 ms |

## Compressed pack details

- **Baseline tokens:** 12,230
- **Optimized tokens:** 2,196 (18.0% of baseline)
- **Saved tokens:** 10,034 (82.0% reduction)
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

Second run (warm cache): **150 tokens**, 15 cache hits, 18 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 24 | 28 | 24 *(fallback)* |
| top_ranked_file | 1025 | 1172 | 1025 *(fallback)* |
| packed_context | 2202 | 2517 | 2202 *(fallback)* |
