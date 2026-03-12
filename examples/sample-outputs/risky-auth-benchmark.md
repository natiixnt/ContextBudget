# ContextBudget Benchmark Report

Task: add rate limiting to auth API
Repository: /Users/naithai/Desktop/amogus/praca/ContextBudget/examples/risky-auth-change/repo
Baseline full-context tokens: 73
Token budget: 30000
Top files: 25

## Strategy Comparison

| Strategy | Input Tokens | Saved Tokens | Files Included | Duplicate Reads Prevented | Quality Risk | Cache Hits | Runtime (ms) |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| naive_full_context | 73 | 0 | 4 | 0 | low | 0 | 0 |
| top_k_selection | 67 | 6 | 3 | 0 | low | 0 | 0 |
| compressed_pack | 85 | 0 | 3 | 0 | medium | 0 | 1 |
| cache_assisted_pack | 85 | 0 | 3 | 0 | medium | 0 | 1 |

## Strategy Details
- `naive_full_context`: Send full readable repository context without selection or compression.
- Files included (4): .contextbudget_cache.json, src/auth.py, src/middleware.py, src/permissions.py
- Files skipped (0): none
- `top_k_selection`: Select top-ranked files and include full content without compression.
- Notes: top_files=25
- Files included (3): src/auth.py, src/middleware.py, src/permissions.py
- Files skipped (1): .contextbudget_cache.json
- `compressed_pack`: Use ContextBudget scoring + compression under configured token budget.
- Files included (3): src/auth.py, src/middleware.py, src/permissions.py
- Files skipped (0): none
- `cache_assisted_pack`: Repeat compressed pack on warm cache to measure cache-assisted behavior.
- Notes: second run with warmed summary cache
- Files included (3): src/auth.py, src/middleware.py, src/permissions.py
- Files skipped (0): none
