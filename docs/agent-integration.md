# Agent Integration

Redcon exposes a stable SDK for coding-agent frameworks via `BudgetGuard`.
The three primary integration methods are:

| Method | Purpose |
|--------|---------|
| `BudgetGuard.pack_context()` | Pack repository context under a token budget |
| `BudgetGuard.simulate_agent()` | Estimate token use and API cost before packing |
| `BudgetGuard.profile_run()` | Pack and return compression metrics in one call |

## Quickstart

```python
from redcon import BudgetGuard

guard = BudgetGuard(max_tokens=30000)

# Pack context for a task
context = guard.pack_context(task="add caching", repo=".")
print(context["budget"]["estimated_input_tokens"], "tokens")

# Build a prompt from the compressed context
prompt = "\n".join(f["text"] for f in context["compressed_context"])
```

## Simulate cost before packing

```python
plan = guard.simulate_agent(task="add caching", repo=".", model="claude-sonnet-4-6")
print(f"Estimated cost: ${plan['cost_estimate']['total_cost_usd']:.4f}")

for step in plan["steps"]:
    print(f"  {step['id']:12} {step['step_total_tokens']:6} tokens")
```

## Profile a run

```python
result = guard.profile_run(task="add caching", repo=".")
p = result["profile"]
print(f"packed in {p['elapsed_ms']} ms, ratio {p['compression_ratio']:.1%}")
print(f"files: {p['files_included_count']} included, {p['files_skipped_count']} skipped")
```

## Multi-turn agent loop with delta context

Re-pack only changed files on subsequent turns:

```python
guard = BudgetGuard(max_tokens=30000)
previous = None

for iteration in range(3):
    result = guard.pack_context(task="implement auth caching", repo=".", delta_from=previous)
    prompt = "\n".join(f["text"] for f in result["compressed_context"])
    # ... send prompt to LLM ...
    previous = result
```

## Strict policy enforcement

```python
from redcon import BudgetGuard, BudgetPolicyViolationError

guard = BudgetGuard(max_tokens=30000, strict=True, max_files_included=10)

try:
    result = guard.pack_context(task="large refactor", repo=".")
except BudgetPolicyViolationError as err:
    for v in err.policy_result["violations"]:
        print(f"policy violation: {v}")
    # err.run_artifact holds the pack result that triggered the error
```

See [python-api.md](python-api.md) for the full `BudgetGuard` reference.

---

## Lower-level middleware

Redcon also includes a lower-level middleware layer for deeper integration.
The middleware wraps the same engine; it does not reimplement scanning, scoring,
compression, or policy logic.

Model-aware packing can be enabled directly in `redcon.toml`:

```toml
model_profile = "gpt-4.1"
```

With a model profile selected, middleware-driven `pack(...)` runs automatically align token estimation, context budget, and compression defaults to the target model, and the recorded artifact includes a `model_profile` block describing those assumptions.

## Middleware Flow

The integration flow is:

1. receive a task
2. optionally plan the workflow through `RedconEngine.plan_agent(...)`
3. prepare packed context through `RedconEngine.pack(...)`
4. optionally enforce a budget policy
5. return additive machine-readable metadata
6. optionally record the combined artifact

## Workflow Planning

Use `plan_agent(...)` when an external agent loop needs to budget context across multiple steps before packing any single prompt:

```python
from redcon import RedconEngine

engine = RedconEngine()
plan = engine.plan_agent(
    task="update auth flow across services",
    workspace="workspace.toml",
    top_files=4,
)

print(plan["total_estimated_tokens"])
print(plan["steps"][0]["context"])
```

The workflow-planning artifact includes:

- `steps`: ordered lifecycle steps such as inspect, implement, test, and validate
- `shared_context`: files reused across multiple steps
- `estimated_tokens` per step
- `total_estimated_tokens`, `unique_context_tokens`, and `reused_context_tokens`

## Library Helpers

The shortest path is the helper trio requested by the middleware layer:

```python
from redcon import RedconEngine, enforce_budget, prepare_context, record_run

result = prepare_context(
    "update auth flow across services",
    workspace="workspace.toml",
    max_tokens=28000,
    delta_from="previous-run.json",
    metadata={"agent": "local-runner"},
)

policy = RedconEngine.make_policy(
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

Use `RedconMiddleware` directly when an agent framework already has a request object or wants to share middleware state.

```python
from redcon import AgentTaskRequest, RedconMiddleware

middleware = RedconMiddleware()
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
- `delta_from`
- `config_path`
- `metadata`

## Delta Mode

For multi-step agent loops, pass `delta_from` to emit an incremental package against
the previous run artifact instead of resending the whole packed context:

```python
from redcon import prepare_context

result = prepare_context(
    "tighten auth checks",
    repo=".",
    max_tokens=1200,
    delta_from="previous-run.json",
)

print(result.run_artifact["delta"]["budget"])
print(result.metadata["delta_enabled"])
```

The recorded run still keeps the full current baseline in `compressed_context` for the
next comparison step, while `run_artifact["delta"]` contains the sendable incremental
package and token accounting for the current step.

## Adapter Abstraction

`AgentAdapter` is the high-level abstraction for embedding Redcon into external agent tools while keeping transport and model calls outside this repository.

`LocalDemoAgentAdapter` is included as a local simulation:

```python
from redcon import AgentTaskRequest, RedconMiddleware, LocalDemoAgentAdapter

middleware = RedconMiddleware()
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

When `model_profile` is configured, the underlying run artifact also records:

- selected and resolved model profile
- tokenizer assumption
- context window
- recommended compression strategy
- effective `max_tokens` after profile-based clamping

## Integration Guidance

- Use `prepare_context(...)` when you want a small helper-based integration.
- Use `RedconMiddleware` when you want a reusable local integration boundary.
- Use `AgentAdapter` when you are embedding Redcon into another agent tool.
- Keep vendor-specific transport, inference, and authentication logic outside this repository.

## PR Audit Guard

For CI, pair the runtime middleware with `redcon pr-audit` so pull requests that expand default agent context are caught before merge. The audit works directly from git refs, estimates changed-file tokens before vs. after the PR, highlights files that grew, reports newly introduced dependencies, and writes a ready-to-post `*.comment.md` artifact for PR discussions.
