# Python API

Use ContextBudget as a reusable library for local tools, CI wrappers, and agent integrations.

## High-level API

```python
from contextbudget import ContextBudgetEngine

engine = ContextBudgetEngine()
plan = engine.plan(task="refactor auth middleware", repo=".")
agent_plan = engine.plan_agent(task="refactor auth middleware", repo=".")
run = engine.pack(task="refactor auth middleware", repo=".", max_tokens=24000)
workspace_run = engine.pack(task="update auth flow", workspace="workspace.toml", max_tokens=24000)
summary = engine.report(run)
```

`engine.plan_agent(...)` returns a machine-readable workflow artifact with:

- ordered workflow `steps`
- assigned `context` per step
- `estimated_tokens` per step
- `total_estimated_tokens` across the workflow

## Budget Guard API

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.pack(task="add caching to search API", repo=".")
workspace_result = guard.pack(task="update auth flow", workspace="workspace.toml")
```

## Strict Policy Evaluation

```python
policy = engine.make_policy(max_files_included=12, max_quality_risk_level="medium")
policy_result = engine.evaluate_policy(run, policy=policy)
```

Public types are exported from `contextbudget` package root.
Plugin interfaces and registry helpers are exported from `contextbudget.plugins`.

## Cache Backends

Built-in cache backends live under `contextbudget.cache`.

- `LocalFileSummaryCacheBackend`: default persistent backend used by CLI and API.
- `SharedSummaryCacheBackendStub`: no-op shared-cache stub for future remote/team reuse.
- `InMemorySummaryCacheBackend`: process-local backend for tests and advanced embeddings.

Pack artifacts and `engine.report(...)` include a `cache` block with backend name plus hit/miss/write counters.
Workspace artifacts also include `workspace`, `scanned_repos`, and `selected_repos` fields.

## Summarizer Adapters

External summarization uses an adapter interface rather than a built-in vendor client.

```python
from contextbudget import (
    ContextBudgetEngine,
    ExternalSummaryAdapter,
    register_external_summarizer_adapter,
)


class TeamSummaryAdapter(ExternalSummaryAdapter):
    name = "team-summary"

    def summarize(self, request) -> str:
        return f"summary for {request.path}"


register_external_summarizer_adapter("team-summary", TeamSummaryAdapter())

engine = ContextBudgetEngine()
run = engine.pack(task="refactor auth middleware", repo=".")
```

Select the adapter through config:

```toml
[summarization]
backend = "external"
adapter = "team-summary"
```

If the adapter is unavailable or raises, ContextBudget falls back to deterministic summarization and records that fallback in the artifact.
