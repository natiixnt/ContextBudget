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

See [`docs/benchmarks/`](benchmarks/) for full results.

**Focused (4 tasks, Python microservice — 12,228 baseline tokens):**

Each task was measured with a cold cache; `cache_assisted_pack` shows savings
when summaries from the prior run are reused.

| Task | Baseline | Cold (compressed_pack) | Reduction | Warm (cache_assisted) | Reduction |
|------|----------|-----------------------|-----------|-----------------------|-----------|
| Add Redis caching | 12,228 | 7,827 | 36% | 1,459 | 88% |
| Add JWT authentication | 12,228 | 7,512 | 39% | 150 | 99% |
| Refactor module | 12,228 | 7,831 | 36% | 610 | 95% |
| Add rate limiting | 12,228 | 7,827 | 36% | 1,459 | 88% |
| **Average** | | | **37%** | | **92%** |

**Large-scale (1,000 tasks, Redcon codebase — 2.2 M baseline tokens):**

| Metric | compressed_pack | cache_assisted_pack |
|--------|----------------|---------------------|
| Mean tokens | 2,565 | 496 |
| Mean savings | 99.9% | 100.0% |
| p10 savings | 99.7% | 100.0% |
| Quality risk low | 99.8% | - |

> The 2.2 M baseline includes all repo files (source, docs, wiki, examples).

### Reproduce locally

```bash
# 1,000-task benchmark
python redcon-benchmarks/run_large_benchmark.py --sample 100

# Single task
redcon benchmark "Add Redis caching to task lookup endpoints" \
    --repo . --max-tokens 32000
```
