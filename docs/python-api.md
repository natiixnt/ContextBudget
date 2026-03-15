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

## SDK Interface

`BudgetGuard` exposes three higher-level methods designed for direct use in agent
frameworks and coding tools.

### `pack_context`

Pack repository context for a task using the guard's token budget.  This is the
primary entry point for SDK consumers — it is equivalent to `pack()` but named
to align with the agent SDK interface pattern.

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.pack_context(task="add caching", repo=".")
print(result["budget"]["estimated_input_tokens"])
```

When `strict=True` (or set on the guard), a `BudgetPolicyViolationError` is raised
if the run violates the configured policy.

### `simulate_agent`

Simulate a multi-step agent workflow for a task.  Returns a step-by-step plan
showing how context would be distributed across lifecycle phases (*inspect*,
*implement*, *test*, *validate*, *document*) before any prompt is packed.

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
plan = guard.simulate_agent(task="refactor auth flow", repo=".")
for step in plan["steps"]:
    print(step["id"], step["estimated_tokens"])
print("total tokens:", plan["total_estimated_tokens"])
```

The artifact mirrors `ContextBudgetEngine.plan_agent()` and includes `steps`,
`shared_context`, `total_estimated_tokens`, `unique_context_tokens`, and
`reused_context_tokens`.

### `profile_run`

Pack context and return the run artifact augmented with a `profile` block containing
wall-clock timing and derived budget metrics.

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.profile_run(task="add caching", repo=".")
p = result["profile"]
print(f"packed in {p['elapsed_ms']} ms")
print(f"compression ratio: {p['compression_ratio']:.1%}")
print(f"files included: {p['files_included_count']}")
```

Profile block fields:

| Field | Type | Description |
|---|---|---|
| `elapsed_ms` | int | Wall-clock milliseconds for the pack operation |
| `estimated_input_tokens` | int | Tokens that will be sent to the model |
| `estimated_saved_tokens` | int | Tokens removed by compression |
| `compression_ratio` | float | `saved / (saved + input)`, 0–1 |
| `files_included_count` | int | Number of files packed |
| `files_skipped_count` | int | Number of files excluded by budget |
| `quality_risk_estimate` | str | Risk level from the budget engine |

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
