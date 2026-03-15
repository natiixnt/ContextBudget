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

| Task | Baseline | Compressed | Reduction |
|------|----------|------------|-----------|
| Add Redis caching to task lookup endpoints | 12,230 tok | 7,937 tok | **35%** |
| Add JWT authentication | 12,230 tok | 3,259 tok | **73%** |
| Refactor database module | 12,230 tok | 1,768 tok | **86%** |

See `docs/benchmarks/` in the repository for the full pre-generated reports.

---

## Reproduce Locally

Run all three canonical tasks:

```bash
python benchmarks/run_benchmarks.py
```

Or run a single task:

```bash
redcon benchmark "Add Redis caching to task lookup endpoints" \
    --repo benchmarks/dataset --max-tokens 8000
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
