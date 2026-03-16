# Benchmark Summary

Results from running Redcon against the included dataset (token budget: 8,000, top files: 20).

| Task | Baseline tokens | Compressed tokens | Reduction | Quality risk |
|------|----------------|-------------------|-----------|--------------|
| [add-caching](./add-caching.md) | 12,228 | 3,445 | 71.8% | low |
| [add-authentication](./add-authentication.md) | 12,228 | 2,845 | 76.7% | low |
| [refactor-module](./refactor-module.md) | 12,228 | 2,277 | 81.4% | low |
| [add-rate-limiting](./add-rate-limiting.md) | 12,228 | 642 | 94.7% | low |

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