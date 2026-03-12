# ContextBudget Diff Report

Old run: examples/sample-outputs/risky-auth-run.json
New run: examples/sample-outputs/language-aware-run.json

## Task Difference
- Changed: True
- Old task: tighten auth middleware token validation
- New task: refactor auth exports

## Context File Changes
- Files added: 2
- Files removed: 3
- Added: `src/auth.ts`
- Added: `src/auth_service.py`
- Removed: `src/auth.py`
- Removed: `src/middleware.py`
- Removed: `src/permissions.py`

## Ranked Score Changes
- `src/middleware.py`: 4.1 -> None (delta: -4.1, removed)
- `src/auth.py`: 3.1 -> None (delta: -3.1, removed)
- `src/auth_service.py`: None -> 3.1 (delta: 3.1, added)
- `src/auth.ts`: None -> 2.85 (delta: 2.85, added)
- `src/permissions.py`: 0.35 -> None (delta: -0.35, removed)

## Budget Deltas
- Estimated input tokens: 85 -> 166 (delta: 81)
- Estimated saved tokens: 0 -> 0 (delta: 0)
- Quality risk: medium -> medium (delta level: 0)
- Cache hits: 0 -> 0 (delta: 0)
