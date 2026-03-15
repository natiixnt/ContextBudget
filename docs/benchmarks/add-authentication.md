# Benchmark: add-authentication

> **Task:** Add JWT authentication middleware to protect task and user API routes

Evaluates context selection for an auth-focused change spanning route handlers, user model, and application bootstrap.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 3 ms |
| Generated | 2026-03-15T10:04:25.421766+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,230 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,230 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,230 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 3,259 | 8,971 (73.4%) | low | 16 ms |
| cache_assisted_pack | 150 | 12,080 (98.8%) | low | 16 ms |

## Compressed pack details

- **Input tokens:** 3,259 (26.6% of baseline)
- **Saved tokens:** 8,971 (73.4% reduction)
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

Second run (warm cache): **150 tokens**, 15 cache hits, 16 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 18 | 20 | 18 *(fallback)* |
| top_ranked_file | 616 | 704 | 616 *(fallback)* |
| packed_context | 3265 | 3732 | 3265 *(fallback)* |
