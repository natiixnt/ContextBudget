# Benchmark: add-rate-limiting

> **Task:** Add rate limiting middleware to API endpoints to prevent abuse and ensure fair usage

Evaluates context selection for rate-limiting middleware spanning route handlers, application bootstrap, and configuration.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 20 ms |
| Generated | 2026-03-18T11:06:33.279952+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,228 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,228 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,228 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 802 | 11,426 (93.4%) | low | 36 ms |
| cache_assisted_pack | 802 | 11,426 (93.4%) | low | 37 ms |

## Compressed pack details

- **Input tokens:** 802 (6.6% of baseline)
- **Saved tokens:** 11,426 (93.4% reduction)
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

Second run (warm cache): **802 tokens**, 30 cache hits, 37 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 21 | 24 | 21 *(fallback)* |
| top_ranked_file | 392 | 448 | 392 *(fallback)* |
| packed_context | 804 | 919 | 804 *(fallback)* |
