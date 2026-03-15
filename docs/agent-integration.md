# Agent Integration

ContextBudget includes a local-first middleware layer for sitting between a coding agent and repository context selection. The middleware wraps the existing engine; it does not reimplement scanning, scoring, compression, or policy logic.

## Middleware Flow

The integration flow is:

1. receive a task
2. prepare packed context through `ContextBudgetEngine.pack(...)`
3. optionally enforce a budget policy
4. return additive machine-readable metadata
5. optionally record the combined artifact

## Library Helpers

The shortest path is the helper trio requested by the middleware layer:

```python
from contextbudget import ContextBudgetEngine, enforce_budget, prepare_context, record_run

result = prepare_context(
    "update auth flow across services",
    workspace="workspace.toml",
    max_tokens=28000,
    metadata={"agent": "local-runner"},
)

policy = ContextBudgetEngine.make_policy(
    max_estimated_input_tokens=28000,
    max_quality_risk_level="medium",
)

checked = enforce_budget(result, policy=policy, strict=True)
record_run(checked, "agent-run.json")
```

These helpers return or operate on `AgentMiddlewareResult`, which contains:

- `run_artifact`: the normal packed-context artifact from the engine
- `metadata`: additive middleware metadata derived from that artifact
- `policy_result`: optional machine-readable policy evaluation output

## Typed Middleware API

Use `ContextBudgetMiddleware` directly when an agent framework already has a request object or wants to share middleware state.

```python
from contextbudget import AgentTaskRequest, ContextBudgetMiddleware

middleware = ContextBudgetMiddleware()
request = AgentTaskRequest(
    task="update auth flow across services",
    workspace="workspace.toml",
    max_tokens=28000,
    metadata={"agent_session": "demo-001"},
)

result = middleware.handle(request)
print(result.metadata["estimated_input_tokens"])
print(result.metadata["selected_repos"])
```

`AgentTaskRequest` supports:

- `task`
- `repo`
- `workspace`
- `max_tokens`
- `top_files`
- `config_path`
- `metadata`

## Adapter Abstraction

`AgentAdapter` is the high-level abstraction for embedding ContextBudget into external agent tools while keeping transport and model calls outside this repository.

`LocalDemoAgentAdapter` is included as a local simulation:

```python
from contextbudget import AgentTaskRequest, ContextBudgetMiddleware, LocalDemoAgentAdapter

middleware = ContextBudgetMiddleware()
adapter = LocalDemoAgentAdapter()
request = AgentTaskRequest(task="update auth flow", repo=".", max_tokens=400)

run = adapter.run(request, middleware, record_path="demo-agent-run.json")
print(run.prompt_preview)
print(run.response)
```

The demo adapter:

- prepares context with the middleware
- optionally enforces policy
- optionally records the run artifact
- returns a local prompt preview plus simulated agent output

## Recorded Metadata

`record_run(...)` writes the normal run artifact with an additive `agent_middleware` block:

```json
{
  "task": "update auth flow",
  "files_included": ["src/auth.py"],
  "budget": {
    "estimated_input_tokens": 820
  },
  "agent_middleware": {
    "request": {
      "task": "update auth flow",
      "repo": "."
    },
    "metadata": {
      "files_included_count": 1,
      "estimated_input_tokens": 820,
      "selected_repos": []
    }
  }
}
```

When the result is recorded by an adapter, the same block can also include:

- `recorded_path`
- `adapter`
- `adapter_metadata`

The middleware metadata is designed for machine consumers and includes file counts, selected repos, scanned repos, token estimates, quality risk, cache summary, and request metadata.

## Integration Guidance

- Use `prepare_context(...)` when you want a small helper-based integration.
- Use `ContextBudgetMiddleware` when you want a reusable local integration boundary.
- Use `AgentAdapter` when you are embedding ContextBudget into another agent tool.
- Keep vendor-specific transport, inference, and authentication logic outside this repository.
