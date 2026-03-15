# Benchmark and Diff

## `contextbudget diff`

Compare two runs and inspect:
- task differences
- files added/removed in packed context
- ranked score changes
- token/savings/risk/cache deltas

```bash
contextbudget diff old-run.json new-run.json
```

## `contextbudget benchmark`

Compare deterministic strategies for one task:
- naive full-context
- top-k selection
- compressed pack
- cache-assisted pack

Benchmark artifacts also include:
- the active token-estimator backend report
- `estimator_samples`, a compact comparison of built-in estimators on local sample text

```bash
contextbudget benchmark "add rate limiting to auth API" --repo .
```

Outputs include terminal summary, JSON artifact, and Markdown report.
