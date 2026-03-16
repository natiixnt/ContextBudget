# Benchmarking and Diff

## `redcon benchmark`

Compare deterministic packing strategies for one task:

| Strategy | Description |
|----------|-------------|
| naive full-context | All files included without compression |
| top-k selection | Top-ranked files included without compression |
| compressed pack | Full compression pipeline |
| cache-assisted pack | Compressed with summary cache reuse |

```bash
redcon benchmark "add rate limiting to auth API" --repo .
redcon benchmark <task> --workspace <workspace.toml>
```

**Outputs:**
- Terminal summary
- JSON artifact with strategy comparisons
- Markdown report
- Active token-estimator backend report
- `estimator_samples` — compact comparison of built-in estimators on local sample text

---

## `redcon diff`

Compare two run artifacts and inspect what changed.

```bash
redcon diff old-run.json new-run.json
```

**Inspects:**
- Task differences
- Files added/removed in packed context
- Ranked score changes
- Token, savings, risk, and cache deltas

---

## Included Benchmark Dataset

A realistic multi-file Python service ships with the repository at `benchmarks/dataset/` so you can run reproducible benchmarks without an external project.

The dataset is a **task-manager API** with:
- Models (`task.py`, `user.py`)
- Services (`task_service.py`, `user_service.py`)
- Database layer (`connection.py`, `repository.py`)
- Route handlers (`tasks.py`, `users.py`)
- Tests

This is representative of a production codebase with meaningful cross-file dependencies.

---

## Pre-Generated Results

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

See `redcon-benchmarks/` in the repository for raw JSON and the reproducible runner.

---

## Reproduce Locally

```bash
# 1,000-task benchmark (~14 min)
python redcon-benchmarks/run_large_benchmark.py

# 100-task sample (~90 s)
python redcon-benchmarks/run_large_benchmark.py --sample 100

# Single task
redcon benchmark "Add Redis caching to task lookup endpoints" \
    --repo . --max-tokens 32000
```

---

## Using Diff in CI

After two runs (e.g. before and after a code change), diff them to detect context regressions:

```bash
redcon pack "add caching" --repo . --max-tokens 30000
mv run.json baseline-run.json

# make changes to the repo...

redcon pack "add caching" --repo . --max-tokens 30000
redcon diff baseline-run.json run.json
```

For PR-level diffing, see `redcon pr-audit` in the [[CLI Reference]].
