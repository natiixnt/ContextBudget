# Benchmark Summary

Results from running ContextBudget against the included dataset (token budget: 8,000, top files: 20).

| Task | Baseline tokens | Compressed tokens | Reduction | Quality risk |
|------|----------------|-------------------|-----------|--------------|
| [add-caching](./add-caching.md) | 12,230 | 7,937 | 35.1% | medium |
| [add-authentication](./add-authentication.md) | 12,230 | 3,259 | 73.4% | low |
| [refactor-module](./refactor-module.md) | 12,230 | 1,768 | 85.5% | low |

## How to reproduce

```bash
python benchmarks/run_benchmarks.py
```

Or run a single task via the CLI:

```bash
contextbudget benchmark "Add Redis caching to task lookup endpoints" \
    --repo benchmarks/dataset --max-tokens 8000
```

_Generated 2026-03-15_