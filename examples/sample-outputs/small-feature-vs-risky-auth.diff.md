# ContextBudget Diff Report

Old run: examples/sample-outputs/small-feature-run.json
New run: examples/sample-outputs/risky-auth-run.json

## Task Difference
- Changed: True
- Old task: add caching to search API
- New task: tighten auth middleware token validation

## Context File Changes
- Files added: 3
- Files removed: 2
- Added: `src/auth.py`
- Added: `src/middleware.py`
- Added: `src/permissions.py`
- Removed: `src/cache.py`
- Removed: `src/search_api.py`

## Ranked Score Changes
- `src/middleware.py`: None -> 4.1 (delta: 4.1, added)
- `src/auth.py`: None -> 3.1 (delta: 3.1, added)
- `src/search_api.py`: 2.6 -> None (delta: -2.6, removed)
- `src/cache.py`: 0.35 -> None (delta: -0.35, removed)
- `src/permissions.py`: None -> 0.35 (delta: 0.35, added)

## Budget Deltas
- Estimated input tokens: 69 -> 85 (delta: 16)
- Estimated saved tokens: 0 -> 0 (delta: 0)
- Quality risk: medium -> medium (delta level: 0)
- Cache hits: 0 -> 0 (delta: 0)
