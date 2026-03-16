# Benchmark Summary

Results from running Redcon against the included dataset (token budget: 8,000, top files: 20).

| Task | Baseline tokens | Compressed tokens | Reduction | Quality risk |
|------|----------------|-------------------|-----------|--------------|
| [add-caching](./add-caching.md) | 13,538 | 4,295 | 68.3% | low |
| [add-authentication](./add-authentication.md) | 13,538 | 3,025 | 77.7% | low |
| [refactor-module](./refactor-module.md) | 13,538 | 2,976 | 78.0% | low |
| [add-rate-limiting](./add-rate-limiting.md) | 13,538 | 642 | 95.3% | low |

## How to reproduce

```bash
python benchmarks/run_benchmarks.py
```

Or run a single task via the CLI:

```bash
redcon benchmark "Add Redis caching to task lookup endpoints" \
    --repo benchmarks/dataset --max-tokens 8000
```

_Generated 2026-03-16_