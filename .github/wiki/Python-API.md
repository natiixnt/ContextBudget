# Python API

Use Redcon as a reusable library for local tools, CI wrappers, and agent integrations.

---

## Quick Start

```python
from redcon import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.pack_context(task="add caching", repo=".")
print(result["budget"]["estimated_input_tokens"], "tokens")
```

---

## BudgetGuard

`BudgetGuard` is the stable SDK entry point. It wraps `RedconEngine` and adds opinionated defaults, policy enforcement, and profiling.

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
    engine: RedconEngine | None = None,
)
```

| Parameter | Default | Description |
|---|---|---|
| `max_tokens` | `None` | Token budget. Uses config default when `None`. |
| `top_files` | `None` | Maximum candidate files per scoring pass. |
| `max_files_included` | `None` | Policy: maximum files allowed in a packed run. |
| `max_quality_risk_level` | `None` | Policy: `"low"`, `"medium"`, or `"high"`. |
| `min_estimated_savings_percentage` | `None` | Policy: minimum compression savings required. |
| `max_context_size_bytes` | `None` | Policy: maximum total byte size of context payload. |
| `policy_path` | `None` | Path to a TOML policy file. |
| `strict` | `False` | Raise `BudgetPolicyViolationError` on policy violations. |
| `config_path` | `None` | Path to a `redcon.toml` config file. |
| `engine` | `None` | Inject a pre-configured `RedconEngine`. |

---

## SDK Methods

### `pack_context`

Pack repository context for a task, respecting the configured token budget.

```python
result = guard.pack_context(
    task="add caching to search API",
    repo=".",
    max_tokens=None,   # overrides guard.max_tokens
    top_files=None,    # overrides guard.top_files
    delta_from=None,   # previous run artifact for incremental context
    strict=None,       # overrides guard.strict
    policy_path=None,  # overrides guard.policy_path
    config_path=None,
)
```

#### Return value

| Field | Type | Description |
|---|---|---|
| `command` | `str` | Always `"pack"` |
| `task` | `str` | Task description |
| `repo` | `str` | Resolved repository path |
| `max_tokens` | `int` | Effective token budget |
| `ranked_files` | `list[dict]` | Files ranked by relevance; each has `path`, `score`, `heuristic_score`, `historical_score`, `reasons` |
| `files_included` | `list[str]` | Paths of files packed into context |
| `files_skipped` | `list[str]` | Paths excluded by budget or policy |
| `compressed_context` | `list[dict]` | Packed entries; each has `path`, `strategy`, `original_tokens`, `compressed_tokens`, `text` |
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
result = guard.pack_context(task="quick fix", repo=".", max_tokens=8000)
```

**Strict policy enforcement:**

```python
from redcon import BudgetGuard, BudgetPolicyViolationError

guard = BudgetGuard(max_tokens=30000, strict=True, max_files_included=10)
try:
    result = guard.pack_context(task="large refactor", repo=".")
except BudgetPolicyViolationError as err:
    for v in err.policy_result["violations"]:
        print(f"  - {v}")
    # err.run_artifact contains the pack result that triggered the violation
```

**Incremental delta mode:**

```python
first = guard.pack_context(task="add caching", repo=".")

# After some files have changed:
second = guard.pack_context(task="add caching", repo=".", delta_from=first)
print(second["delta"]["budget"]["tokens_saved"], "tokens saved by delta")
```

---

### `simulate_agent`

Simulate a multi-step agent workflow with per-step token and API cost estimates. Returns a plan before any context is packed.

```python
plan = guard.simulate_agent(
    task="refactor auth flow",
    repo=".",
    top_files=None,
    model="gpt-4o",
    price_per_1m_input=None,   # uses model default when None
    price_per_1m_output=None,
    config_path=None,
)
```

#### Return value

| Field | Type | Description |
|---|---|---|
| `command` | `str` | Always `"simulate-agent"` |
| `task` | `str` | Task description |
| `steps` | `list[dict]` | Ordered workflow steps |
| `total_tokens` | `int` | Token sum across all steps |
| `unique_context_tokens` | `int` | Tokens counted once per unique file |
| `total_context_tokens` | `int` | Context tokens summed across steps |
| `cost_estimate` | `dict` | USD cost breakdown: `total_cost_usd`, `input_cost_usd`, `output_cost_usd`, `model` |
| `model` | `str` | Model used for pricing |

**Step schema**

| Field | Type | Description |
|---|---|---|
| `id` | `str` | `inspect`, `implement`, `test`, `validate`, `document` |
| `title` | `str` | Human-readable step title |
| `files_read` | `list[dict]` | Files read; each has `path`, `tokens`, `read_type` |
| `context_tokens` | `int` | Context tokens for this step |
| `prompt_tokens` | `int` | Prompt overhead tokens |
| `output_tokens` | `int` | Estimated output tokens |
| `step_total_tokens` | `int` | Sum of the three above |

#### Examples

```python
guard = BudgetGuard(max_tokens=30000)
plan = guard.simulate_agent(task="refactor auth flow", repo=".")

for step in plan["steps"]:
    print(f"{step['id']:12} {step['step_total_tokens']:6} tokens")

print(f"\nTotal:  {plan['total_tokens']} tokens")
print(f"Cost:   ${plan['cost_estimate']['total_cost_usd']:.4f}")
```

**Cost estimation with Claude:**

```python
plan = guard.simulate_agent(
    task="refactor auth flow",
    repo=".",
    model="claude-sonnet-4-6",
)
print(f"Estimated Claude cost: ${plan['cost_estimate']['total_cost_usd']:.4f}")
```

---

### `profile_run`

Pack context and return the run artifact augmented with a `profile` block containing wall-clock timing and compression metrics.

```python
result = guard.profile_run(
    task="add caching",
    repo=".",
    max_tokens=None,
    top_files=None,
    config_path=None,
)
```

**Profile block**

| Field | Type | Description |
|---|---|---|
| `elapsed_ms` | `int` | Wall-clock milliseconds |
| `estimated_input_tokens` | `int` | Tokens sent to the model |
| `estimated_saved_tokens` | `int` | Tokens removed by compression |
| `compression_ratio` | `float` | `saved / (saved + input)`, range 0-1 |
| `files_included_count` | `int` | Number of files packed |
| `files_skipped_count` | `int` | Number of files excluded |
| `quality_risk_estimate` | `str` | `"low"`, `"medium"`, or `"high"` |

```python
result = guard.profile_run(task="add caching", repo=".")
p = result["profile"]
print(f"packed in {p['elapsed_ms']} ms")
print(f"compression: {p['compression_ratio']:.1%} ({p['estimated_saved_tokens']} tokens saved)")
```

---

### `read_profile`

Analyze read patterns from a pack run. Detects duplicates, unnecessary reads, and high token-cost files.

```python
run = guard.pack_context(task="add caching", repo=".")
report = guard.read_profile(run)

print(f"Files read:          {report['unique_files_read']}")
print(f"Duplicate reads:     {report['duplicate_reads']}")
print(f"Tokens wasted total: {report['tokens_wasted_total']}")
```

Also accepts a path to a run JSON file:

```python
report = guard.read_profile("run.json")
```

---

### `evaluate_policy`

Check policy without raising an exception.

```python
guard = BudgetGuard(max_tokens=30000, max_files_included=5)
result = guard.pack_context(task="large refactor", repo=".")

policy_result = guard.evaluate_policy(result)
if not policy_result["passed"]:
    for v in policy_result["violations"]:
        print(v)
```

---

## RedconEngine

Lower-level programmatic API. `BudgetGuard` wraps it and delegates to the same methods.

```python
from redcon import RedconEngine

engine = RedconEngine()
plan = engine.plan(task="refactor auth middleware", repo=".")
agent_plan = engine.plan_agent(task="refactor auth middleware", repo=".")
run = engine.pack(task="refactor auth middleware", repo=".", max_tokens=24000)
workspace_run = engine.pack(task="update auth flow", workspace="workspace.toml", max_tokens=24000)
summary = engine.report(run)
```

**Architecture advisor:**

```python
advice = engine.advise(repo=".")
for s in advice["suggestions"]:
    print(f"[{s['suggestion']}] {s['path']}  impact={s['estimated_token_impact']}")

# Weight suggestions by real inclusion frequency:
advice = engine.advise(repo=".", history=["./runs/"])
```

**Policy evaluation:**

```python
policy = engine.make_policy(max_files_included=12, max_quality_risk_level="medium")
policy_result = engine.evaluate_policy(run, policy=policy)
```

---

## Error Handling

### `BudgetPolicyViolationError`

Raised when `strict=True` and a policy is violated.

```python
from redcon import BudgetGuard, BudgetPolicyViolationError

guard = BudgetGuard(max_tokens=30000, strict=True, max_files_included=5)

try:
    result = guard.pack_context(task="large refactor", repo=".")
except BudgetPolicyViolationError as err:
    # err.policy_result  - dict with "passed", "violations", "checks"
    # err.run_artifact   - the pack result that triggered the violation
    for violation in err.policy_result["violations"]:
        print(f"policy violation: {violation}")
```

`str(err)` returns a semicolon-joined string of violation messages. It is a `RuntimeError` subclass.

---

## Integration Patterns

### Agent loop with delta context

```python
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

### Pre-flight cost check

```python
guard = BudgetGuard(max_tokens=30000)

plan = guard.simulate_agent(task="refactor auth flow", repo=".", model="gpt-4o")
estimated_cost = plan["cost_estimate"]["total_cost_usd"]
print(f"Estimated run cost: ${estimated_cost:.4f}")

if estimated_cost < 0.50:
    result = guard.pack_context(task="refactor auth flow", repo=".")
```

### Custom engine for tests

```python
from redcon import BudgetGuard, RedconEngine
from redcon.telemetry import NoOpTelemetrySink

engine = RedconEngine(telemetry_sink=NoOpTelemetrySink())
guard = BudgetGuard(max_tokens=30000, engine=engine)
result = guard.pack_context(task="add caching", repo=".")
```

---

## Summarizer Adapters

```python
from redcon import (
    RedconEngine,
    ExternalSummaryAdapter,
    register_external_summarizer_adapter,
)


class TeamSummaryAdapter(ExternalSummaryAdapter):
    name = "team-summary"

    def summarize(self, request) -> str:
        return f"summary for {request.path}"


register_external_summarizer_adapter("team-summary", TeamSummaryAdapter())

engine = RedconEngine()
run = engine.pack(task="refactor auth middleware", repo=".")
```

Select the adapter through config:

```toml
[summarization]
backend = "external"
adapter = "team-summary"
```

If the adapter is unavailable or raises, Redcon falls back to deterministic summarization automatically.
