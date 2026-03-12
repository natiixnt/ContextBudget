# ContextBudget Benchmark Report

Task: add rate limiting to auth API
Repository: /Users/naithai/Desktop/amogus/praca/ContextBudget/examples/benchmark/repo
Baseline full-context tokens: 5295
Token budget: 2500
Top files: 20

## Strategy Comparison

| Strategy | Input Tokens | Saved Tokens | Files Included | Duplicate Reads Prevented | Quality Risk | Cache Hits | Runtime (ms) |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| naive_full_context | 5295 | 0 | 4 | 0 | low | 0 | 0 |
| top_k_selection | 5284 | 11 | 3 | 0 | low | 0 | 0 |
| compressed_pack | 91 | 5204 | 3 | 0 | low | 0 | 2 |
| cache_assisted_pack | 91 | 5204 | 3 | 0 | low | 1 | 2 |

## Strategy Details
- `naive_full_context`: Send full readable repository context without selection or compression.
- Files included (4): contextbudget.toml, src/auth.py, src/middleware.py, src/notes.py
- Files skipped (0): none
- `top_k_selection`: Select top-ranked files and include full content without compression.
- Notes: top_files=20
- Files included (3): src/auth.py, src/middleware.py, src/notes.py
- Files skipped (1): contextbudget.toml
- `compressed_pack`: Use ContextBudget scoring + compression under configured token budget.
- Files included (3): src/auth.py, src/middleware.py, src/notes.py
- Files skipped (0): none
- `cache_assisted_pack`: Repeat compressed pack on warm cache to measure cache-assisted behavior.
- Notes: second run with warmed summary cache
- Files included (3): src/auth.py, src/middleware.py, src/notes.py
- Files skipped (0): none
