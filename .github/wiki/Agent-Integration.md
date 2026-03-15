# Agent Integration

ContextBudget exposes a stable SDK for coding-agent frameworks via `BudgetGuard`. Three primary integration methods:

| Method | Purpose |
|--------|---------|
| `BudgetGuard.pack_context()` | Pack repository context under a token budget |
| `BudgetGuard.simulate_agent()` | Estimate token use and API cost before packing |
| `BudgetGuard.profile_run()` | Pack and return compression metrics in one call |

---

## Quickstart

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)

# Pack context for a task
context = guard.pack_context(task="add caching", repo=".")
print(context["budget"]["estimated_input_tokens"], "tokens")

# Build a prompt from the compressed context
prompt = "\n".join(f["text"] for f in context["compressed_context"])
```

---

## Simulate Cost Before Packing

```python
plan = guard.simulate_agent(task="add caching", repo=".", model="claude-sonnet-4-6")
print(f"Estimated cost: ${plan['cost_estimate']['total_cost_usd']:.4f}")

for step in plan["steps"]:
    print(f"  {step['id']:12} {step['step_total_tokens']:6} tokens")
```

---

## Multi-turn Agent Loop with Delta Context

Re-pack only changed files on subsequent turns:

```python
guard = BudgetGuard(max_tokens=30000)
previous = None

for iteration in range(3):
    result = guard.pack_context(
        task="implement auth caching",
        repo=".",
        delta_from=previous,
    )
    prompt = "\n".join(f["text"] for f in result["compressed_context"])
    # ... send prompt to LLM ...
    previous = result
```

---

## Strict Policy Enforcement

```python
from contextbudget import BudgetGuard, BudgetPolicyViolationError

guard = BudgetGuard(max_tokens=30000, strict=True, max_files_included=10)

try:
    result = guard.pack_context(task="large refactor", repo=".")
except BudgetPolicyViolationError as err:
    for v in err.policy_result["violations"]:
        print(f"policy violation: {v}")
    # err.run_artifact holds the pack result that triggered the error
```

---

## Lower-Level Middleware

For deeper integration, use the middleware layer directly:

```python
from contextbudget import ContextBudgetEngine, enforce_budget, prepare_context, record_run

result = prepare_context(
    "update auth flow across services",
    workspace="workspace.toml",
    max_tokens=28000,
    delta_from="previous-run.json",
    metadata={"agent": "local-runner"},
)

policy = ContextBudgetEngine.make_policy(
    max_estimated_input_tokens=28000,
    max_quality_risk_level="medium",
)

checked = enforce_budget(result, policy=policy, strict=True)
record_run(checked, "agent-run.json")
```

These helpers return `AgentMiddlewareResult`, which contains:
- `run_artifact`: the normal packed-context artifact from the engine
- `metadata`: additive middleware metadata derived from that artifact
- `policy_result`: optional machine-readable policy evaluation output

---

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

`AgentTaskRequest` supports: `task`, `repo`, `workspace`, `max_tokens`, `top_files`, `delta_from`, `config_path`, `metadata`.

---

## Workflow Planning

Use `plan_agent(...)` when an external agent loop needs to budget context across multiple steps before packing any single prompt:

```python
from contextbudget import ContextBudgetEngine

engine = ContextBudgetEngine()
plan = engine.plan_agent(
    task="update auth flow across services",
    workspace="workspace.toml",
    top_files=4,
)

print(plan["total_estimated_tokens"])
print(plan["steps"][0]["context"])
```

The workflow-planning artifact includes:
- `steps`: ordered lifecycle steps — `inspect`, `implement`, `test`, `validate`, `document`
- `shared_context`: files reused across multiple steps
- `estimated_tokens` per step
- `total_estimated_tokens`, `unique_context_tokens`, `reused_context_tokens`

---

## Delta Mode

Pass `delta_from` to emit an incremental package against the previous run artifact:

```python
from contextbudget import prepare_context

result = prepare_context(
    "tighten auth checks",
    repo=".",
    max_tokens=1200,
    delta_from="previous-run.json",
)

print(result.run_artifact["delta"]["budget"])
print(result.metadata["delta_enabled"])
```

The recorded run keeps the full current baseline in `compressed_context` for the next comparison step, while `run_artifact["delta"]` contains the sendable incremental package.

---

## Adapter Abstraction

`AgentAdapter` is the high-level abstraction for embedding ContextBudget into external agent tools while keeping transport and model calls outside this repository.

`LocalDemoAgentAdapter` is a local simulation:

```python
from contextbudget import AgentTaskRequest, ContextBudgetMiddleware, LocalDemoAgentAdapter

middleware = ContextBudgetMiddleware()
adapter = LocalDemoAgentAdapter()
request = AgentTaskRequest(task="update auth flow", repo=".", max_tokens=400)

run = adapter.run(request, middleware, record_path="demo-agent-run.json")
print(run.prompt_preview)
print(run.response)
```

---

## Model-Aware Packing

Enable model-aware packing via `contextbudget.toml`:

```toml
model_profile = "gpt-4.1"
```

With a model profile active, packing automatically:
- Aligns token estimation to the target model
- Scales the context budget to the model context window
- Adjusts compression defaults

The recorded artifact includes a `model_profile` block with tokenizer assumption, context window, recommended compression strategy, and effective `max_tokens`.

---

## Recorded Artifact Structure

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

---

## PR Audit Guard for CI

For CI, pair the runtime middleware with `contextbudget pr-audit` so pull requests that expand default agent context are caught before merge:

```bash
contextbudget pr-audit \
  --repo . \
  --base "${{ github.event.pull_request.base.sha }}" \
  --head "${{ github.event.pull_request.head.sha }}" \
  --out-prefix contextbudget-pr
```

---

## Integration Guidance

| Approach | When to use |
|----------|-------------|
| `prepare_context(...)` | Small, helper-based integration |
| `ContextBudgetMiddleware` | Reusable local integration boundary |
| `AgentAdapter` | Embedding ContextBudget into another agent tool |

Keep vendor-specific transport, inference, and authentication logic outside this repository.
