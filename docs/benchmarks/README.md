# Benchmark Summary

Results from running Redcon against the included dataset (token budget: 8,000, top files: 20).

| Task | Baseline tokens | Compressed tokens | Reduction | Quality risk |
|------|----------------|-------------------|-----------|--------------|
| [add-caching](./add-caching.md) | 12,228 | 2,862 | 76.6% | low |
| [add-authentication](./add-authentication.md) | 12,228 | 4,390 | 64.1% | low |
| [refactor-module](./refactor-module.md) | 12,228 | 2,285 | 81.3% | low |
| [add-rate-limiting](./add-rate-limiting.md) | 12,228 | 802 | 93.4% | low |

## How to reproduce

```bash
python benchmarks/run_benchmarks.py
```

Or run a single task via the CLI:

```bash
redcon benchmark "Add Redis caching to task lookup endpoints" \
    --repo benchmarks/dataset --max-tokens 8000
```

_Generated 2026-03-18_