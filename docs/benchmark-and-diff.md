# Benchmark and Diff

## `redcon diff`

Compare two runs and inspect:
- task differences
- files added/removed in packed context
- ranked score changes
- token/savings/risk/cache deltas

```bash
redcon diff old-run.json new-run.json
```

## `redcon benchmark`

Compare deterministic strategies for one task:
- naive full-context
- top-k selection
- compressed pack
- cache-assisted pack

Benchmark artifacts also include:
- the active token-estimator backend report
- `estimator_samples`, a compact comparison of built-in estimators on local sample text

```bash
redcon benchmark "add rate limiting to auth API" --repo .
```

Outputs include terminal summary, JSON artifact, and Markdown report.

## Included benchmark dataset

A realistic multi-file Python service (`benchmarks/dataset/`) ships with the
repository so you can run reproducible benchmarks without an external project.

The dataset is a task-manager API with models, services, a database layer, and
route handlers - representative of a production codebase with meaningful
cross-file dependencies.

### Pre-generated results

See [`docs/benchmarks/`](benchmarks/) for results from the three canonical
tasks:

| Task | Baseline | Compressed | Reduction |
|------|----------|------------|-----------|
| Add caching | 12,230 tok | 7,937 tok | 35 % |
| Add authentication | 12,230 tok | 3,259 tok | 73 % |
| Refactor module | 12,230 tok | 1,768 tok | 86 % |

### Reproduce locally

```bash
python benchmarks/run_benchmarks.py
```

Or run a single task:

```bash
redcon benchmark "Add Redis caching to task lookup endpoints" \
    --repo benchmarks/dataset --max-tokens 8000
```
