# Python API

Use ContextBudget as a reusable library for local tools, CI wrappers, and agent
integrations.

## Quick start

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.pack_context(task="add caching", repo=".")
print(result["budget"]["estimated_input_tokens"], "tokens")
```

---

## BudgetGuard

`BudgetGuard` is the stable SDK entry point for agent frameworks.  It wraps
`ContextBudgetEngine` and adds opinionated defaults, policy enforcement, and
profiling on top.

### Constructor

```python
BudgetGuard(
    max_tokens: int | None = None,
    top_files: int | None = None,
    max_files_included: int | None = None,
    max_quality_risk_level: str | None = None,
    min_estimated_savings_percentage: float | None = None,
    max_context_size_bytes: int | None = None,
    policy_path: str | Path | None = None,
    strict: bool = False,
    config_path: str | Path | None = None,
    engine: ContextBudgetEngine | None = None,
)
```

| Parameter | Default | Description |
|---|---|---|
| `max_tokens` | `None` | Token budget inherited by `pack_context` and `profile_run`. Uses config default when `None`. |
| `top_files` | `None` | Maximum candidate files per scoring pass. Inherited by all three SDK methods. |
| `max_files_included` | `None` | Policy constraint: maximum files allowed in a packed run. |
| `max_quality_risk_level` | `None` | Policy constraint: `"low"`, `"medium"`, or `"high"`. |
| `min_estimated_savings_percentage` | `None` | Policy constraint: minimum compression savings required. |
| `max_context_size_bytes` | `None` | Policy constraint: maximum total byte size of the context payload. |
| `policy_path` | `None` | Path to a TOML policy file.  Merged with any inline constraints above. |
| `strict` | `False` | When `True`, all SDK pack methods raise `BudgetPolicyViolationError` on policy violations. |
| `config_path` | `None` | Path to a `contextbudget.toml` config file.  Defaults to `<repo>/contextbudget.toml`. |
| `engine` | `None` | Inject a pre-configured `ContextBudgetEngine` instance. Useful for tests and custom telemetry. |

---

## SDK methods

### `pack_context`

Pack repository context for a task, respecting the configured token budget.
Primary entry point for agent frameworks.

```python
result = guard.pack_context(
    task="add caching to search API",
    repo=".",
    max_tokens=None,    # overrides guard.max_tokens
    top_files=None,     # overrides guard.top_files
    delta_from=None,    # previous run artifact for incremental context
    strict=None,        # overrides guard.strict
    policy_path=None,   # overrides guard.policy_path
    config_path=None,
)
```

#### Return value — pack artifact

| Field | Type | Description |
|---|---|---|
| `command` | `str` | Always `"pack"` |
| `task` | `str` | Task description |
| `repo` | `str` | Resolved repository path |
| `max_tokens` | `int` | Effective token budget |
| `ranked_files` | `list[dict]` | Files ranked by relevance; each item has `path`, `score`, `heuristic_score`, `historical_score`, `reasons` |
| `files_included` | `list[str]` | Paths of files packed into context |
| `files_skipped` | `list[str]` | Paths excluded by budget or policy |
| `compressed_context` | `list[dict]` | Packed file entries; each item has `path`, `strategy`, `original_tokens`, `compressed_tokens`, `text` |
| `budget` | `dict` | See budget block below |
| `cache` | `dict` | Cache hit/miss counters |
| `token_estimator` | `dict` | Active estimation backend metadata |
| `summarizer` | `dict` | Summarization backend metadata |
| `generated_at` | `str` | ISO-8601 timestamp |

**Budget block**

| Field | Type | Description |
|---|---|---|
| `estimated_input_tokens` | `int` | Tokens that will be sent to the model |
| `estimated_saved_tokens` | `int` | Tokens removed by compression |
| `duplicate_reads_prevented` | `int` | Cache deduplications performed |
| `quality_risk_estimate` | `str` | `"low"`, `"medium"`, or `"high"` |

#### Examples

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.pack_context(task="add caching", repo=".")

budget = result["budget"]
print(f"tokens: {budget['estimated_input_tokens']} / {guard.max_tokens}")
print(f"saved:  {budget['estimated_saved_tokens']}")
print(f"risk:   {budget['quality_risk_estimate']}")

for path in result["files_included"]:
    print(f"  {path}")
```

**Override budget at call time:**

```python
# Guard has a high default; this specific pack uses a tighter window.
result = guard.pack_context(task="quick fix", repo=".", max_tokens=8000)
```

**Strict policy enforcement:**

```python
from contextbudget import BudgetGuard, BudgetPolicyViolationError

guard = BudgetGuard(max_tokens=30000, strict=True, max_files_included=10)
try:
    result = guard.pack_context(task="large refactor", repo=".")
except BudgetPolicyViolationError as err:
    print("Policy violations:")
    for v in err.policy_result["violations"]:
        print(f"  - {v}")
    # err.run_artifact contains the pack result that triggered the violation
```

**Incremental context (delta mode):**

```python
first = guard.pack_context(task="add caching", repo=".")

# Later in the agent loop, after some files have changed:
second = guard.pack_context(task="add caching", repo=".", delta_from=first)
print(second["delta"]["budget"]["tokens_saved"], "tokens saved by delta")
```

---

### `simulate_agent`

Simulate a multi-step agent workflow with per-step token and API cost estimates.
Returns a plan before any context is packed, so the caller can inspect the
cost profile before committing to a run.

```python
plan = guard.simulate_agent(
    task="refactor auth flow",
    repo=".",
    top_files=None,             # overrides guard.top_files
    model="gpt-4o",             # model for cost pricing
    price_per_1m_input=None,    # custom USD price (uses model default when None)
    price_per_1m_output=None,
    config_path=None,
)
```

#### Return value — simulation artifact

| Field | Type | Description |
|---|---|---|
| `command` | `str` | Always `"simulate-agent"` |
| `task` | `str` | Task description |
| `steps` | `list[dict]` | Ordered workflow steps; see step schema below |
| `total_tokens` | `int` | Token sum across all steps |
| `unique_context_tokens` | `int` | Tokens counted once per unique file |
| `total_context_tokens` | `int` | Context tokens summed across steps (includes reuse) |
| `total_prompt_tokens` | `int` | Prompt overhead tokens across steps |
| `total_output_tokens` | `int` | Estimated output tokens across steps |
| `cost_estimate` | `dict` | USD cost breakdown; has `total_cost_usd`, `input_cost_usd`, `output_cost_usd`, `model` |
| `context_mode` | `str` | `"isolated"` (each step reads independently) |
| `model` | `str` | Model used for pricing |
| `token_estimator` | `dict` | Token estimator metadata |
| `generated_at` | `str` | ISO-8601 timestamp |

**Step schema**

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Step identifier: `inspect`, `implement`, `test`, `validate`, `document` |
| `title` | `str` | Human-readable step title |
| `objective` | `str` | Step objective derived from the task |
| `files_read` | `list[dict]` | Files read in this step; each item has `path`, `tokens`, `read_type` (`"step"` or `"shared"`) |
| `file_count` | `int` | Number of files read |
| `context_tokens` | `int` | Context tokens for this step |
| `prompt_tokens` | `int` | Prompt overhead tokens |
| `output_tokens` | `int` | Estimated output tokens |
| `step_total_tokens` | `int` | `context_tokens + prompt_tokens + output_tokens` |
| `cumulative_context_tokens` | `int` | Running total of context tokens through this step |
| `cumulative_total_tokens` | `int` | Running total of all tokens through this step |

#### Examples

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
plan = guard.simulate_agent(task="refactor auth flow", repo=".")

for step in plan["steps"]:
    print(f"{step['id']:12} {step['step_total_tokens']:6} tokens")

print(f"\nTotal:  {plan['total_tokens']} tokens")
print(f"Cost:   ${plan['cost_estimate']['total_cost_usd']:.4f}")
```

**Cost estimation with a specific model:**

```python
plan = guard.simulate_agent(
    task="refactor auth flow",
    repo=".",
    model="claude-sonnet-4-6",
)
print(f"Estimated Claude cost: ${plan['cost_estimate']['total_cost_usd']:.4f}")
```

**Inspect which files each step needs:**

```python
for step in plan["steps"]:
    step_files = [f["path"] for f in step["files_read"] if f["read_type"] == "step"]
    print(f"{step['id']}: {step_files}")
```

---

### `profile_run`

Pack context and return the run artifact augmented with a `profile` block
containing wall-clock timing and derived compression metrics.

```python
result = guard.profile_run(
    task="add caching",
    repo=".",
    max_tokens=None,   # overrides guard.max_tokens
    top_files=None,    # overrides guard.top_files
    config_path=None,
)
```

The return value is a complete pack artifact (same schema as `pack_context`)
with an additional `profile` key.

#### Profile block

| Field | Type | Description |
|---|---|---|
| `elapsed_ms` | `int` | Wall-clock milliseconds for the pack operation |
| `estimated_input_tokens` | `int` | Tokens sent to the model |
| `estimated_saved_tokens` | `int` | Tokens removed by compression |
| `compression_ratio` | `float` | `saved / (saved + input)`, range 0–1 |
| `files_included_count` | `int` | Number of files packed |
| `files_skipped_count` | `int` | Number of files excluded by budget |
| `quality_risk_estimate` | `str` | `"low"`, `"medium"`, or `"high"` |

#### Example

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.profile_run(task="add caching", repo=".")

p = result["profile"]
print(f"packed in {p['elapsed_ms']} ms")
print(f"compression: {p['compression_ratio']:.1%} ({p['estimated_saved_tokens']} tokens saved)")
print(f"files: {p['files_included_count']} included, {p['files_skipped_count']} skipped")
print(f"risk: {p['quality_risk_estimate']}")
```

---

## Error handling

### `BudgetPolicyViolationError`

Raised by `pack_context` and `pack` when `strict=True` and a configured policy
is violated.  Also raised by `BudgetGuard.evaluate_policy(strict=True)`.

```python
from contextbudget import BudgetGuard, BudgetPolicyViolationError

guard = BudgetGuard(max_tokens=30000, strict=True, max_files_included=5)

try:
    result = guard.pack_context(task="large refactor", repo=".")
except BudgetPolicyViolationError as err:
    # err.policy_result  — dict with "passed", "violations", "checks"
    # err.run_artifact   — the pack artifact that triggered the violation
    for violation in err.policy_result["violations"]:
        print(f"policy violation: {violation}")
    # Optionally inspect or log the artifact:
    print("files included:", err.run_artifact.get("files_included"))
```

`str(err)` returns a semicolon-joined string of the violation messages.
The exception is a `RuntimeError` subclass.

### Evaluating policy without raising

```python
guard = BudgetGuard(max_tokens=30000, max_files_included=5)
result = guard.pack_context(task="large refactor", repo=".")

policy_result = guard.evaluate_policy(result)
if not policy_result["passed"]:
    for v in policy_result["violations"]:
        print(v)
```

---

## Integration patterns

### Agent loop with delta context

Re-pack only what changed between iterations:

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
previous_run = None

for iteration in range(3):
    result = guard.pack_context(
        task="implement auth caching",
        repo=".",
        delta_from=previous_run,
    )
    context_text = "\n".join(f["text"] for f in result["compressed_context"])
    # ... send context_text to the model ...
    previous_run = result
```

### Pre-flight cost check before packing

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)

# Estimate cost without packing:
plan = guard.simulate_agent(task="refactor auth flow", repo=".", model="gpt-4o")
estimated_cost = plan["cost_estimate"]["total_cost_usd"]
print(f"Estimated run cost: ${estimated_cost:.4f}")

if estimated_cost < 0.50:
    result = guard.pack_context(task="refactor auth flow", repo=".")
    # proceed with agent run
```

### Structured logging from profile_run

```python
import logging
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)

def run_with_telemetry(task: str, repo: str) -> dict:
    result = guard.profile_run(task=task, repo=repo)
    p = result["profile"]
    logging.info(
        "context_pack",
        extra={
            "task": task,
            "elapsed_ms": p["elapsed_ms"],
            "input_tokens": p["estimated_input_tokens"],
            "compression_ratio": p["compression_ratio"],
            "files_included": p["files_included_count"],
            "risk": p["quality_risk_estimate"],
        },
    )
    return result
```

### Using a custom engine (for tests)

```python
from contextbudget import BudgetGuard, ContextBudgetEngine
from contextbudget.telemetry import NoOpTelemetrySink

engine = ContextBudgetEngine(telemetry_sink=NoOpTelemetrySink())
guard = BudgetGuard(max_tokens=30000, engine=engine)
result = guard.pack_context(task="add caching", repo=".")
```

---

## ContextBudgetEngine

`ContextBudgetEngine` exposes the full programmatic API.  `BudgetGuard` wraps it
and delegates to the same underlying methods.

```python
from contextbudget import ContextBudgetEngine

engine = ContextBudgetEngine()
plan = engine.plan(task="refactor auth middleware", repo=".")
agent_plan = engine.plan_agent(task="refactor auth middleware", repo=".")
run = engine.pack(task="refactor auth middleware", repo=".", max_tokens=24000)
workspace_run = engine.pack(task="update auth flow", workspace="workspace.toml", max_tokens=24000)
summary = engine.report(run)
```

`engine.plan_agent(...)` returns a multi-step workflow plan with:

- ordered `steps` with assigned `context` files and `estimated_tokens` per step
- `shared_context` — files reused across multiple steps
- `total_estimated_tokens` across the whole workflow

### Architecture advisor

Detect files that are over-sized, frequently included, or highly coupled, and
get ranked suggestions for splitting them:

```python
advice = engine.advise(repo=".")
for s in advice["suggestions"]:
    print(f"[{s['suggestion']}] {s['path']}  impact={s['estimated_token_impact']}")
```

Pass pack history to weight suggestions by real inclusion frequency:

```python
advice = engine.advise(repo=".", history=["./runs/"])
```

### Policy evaluation

```python
policy = engine.make_policy(max_files_included=12, max_quality_risk_level="medium")
policy_result = engine.evaluate_policy(run, policy=policy)
```

Public types are exported from the `contextbudget` package root.
Plugin interfaces and registry helpers are exported from `contextbudget.plugins`.

---

## Cache backends

Built-in cache backends live under `contextbudget.cache`.

- `LocalFileSummaryCacheBackend` — default persistent backend used by CLI and API.
- `SharedSummaryCacheBackendStub` — no-op shared-cache stub for future remote/team reuse.
- `InMemorySummaryCacheBackend` — process-local backend for tests and advanced embeddings.

Pack artifacts and `engine.report(...)` include a `cache` block with backend
name plus hit/miss/write counters.  Workspace artifacts also include `workspace`,
`scanned_repos`, and `selected_repos` fields.

---

## Summarizer adapters

External summarization uses an adapter interface rather than a built-in vendor
client.

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

If the adapter is unavailable or raises, ContextBudget falls back to
deterministic summarization and records that fallback in the artifact.
