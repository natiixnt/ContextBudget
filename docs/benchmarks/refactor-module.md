# Benchmark: refactor-module

> **Task:** Refactor database repository layer to use connection pooling

Evaluates selection accuracy when the primary change targets the database connection module and its callers across services.

## Settings

| Parameter | Value |
|-----------|-------|
| Token budget | 8,000 |
| Top files | 20 |
| Token estimator | heuristic |
| Scan runtime | 7 ms |
| Generated | 2026-03-16T20:32:40.134423+00:00 |

## Baseline

Full repository context (no selection, no compression): **12,228 tokens**

## Strategy comparison

| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |
|----------|-------------|--------------|--------------|---------|
| naive_full_context | 12,228 | 0 (0.0%) | low | 0 ms |
| top_k_selection | 12,228 | 0 (0.0%) | low | 0 ms |
| compressed_pack | 1,737 | 10,491 (85.8%) | low | 21 ms |
| cache_assisted_pack | 150 | 12,078 (98.8%) | low | 21 ms |

## Compressed pack details

- **Input tokens:** 1,737 (14.2% of baseline)
- **Saved tokens:** 10,491 (85.8% reduction)
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

Second run (warm cache): **150 tokens**, 15 cache hits, 21 ms

## Token estimator comparison

| Sample | heuristic | model_aligned | exact_tiktoken |
|--------|-----------|---------------|----------------|
| task | 15 | 18 | 15 *(fallback)* |
| top_ranked_file | 753 | 861 | 753 *(fallback)* |
| packed_context | 1740 | 1989 | 1740 *(fallback)* |
