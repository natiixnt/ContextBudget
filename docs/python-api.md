# Python API

Use ContextBudget as a reusable library for local tools, CI wrappers, and agent integrations.

## High-level API

```python
from contextbudget import ContextBudgetEngine

engine = ContextBudgetEngine()
plan = engine.plan(task="refactor auth middleware", repo=".")
run = engine.pack(task="refactor auth middleware", repo=".", max_tokens=24000)
summary = engine.report(run)
```

## Budget Guard API

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.pack(task="add caching to search API", repo=".")
```

## Strict Policy Evaluation

```python
policy = engine.make_policy(max_files_included=12, max_quality_risk_level="medium")
policy_result = engine.evaluate_policy(run, policy=policy)
```

Public types are exported from `contextbudget` package root.
