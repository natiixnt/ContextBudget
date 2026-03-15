# Agent Runtime

The Redcon Agent Runtime turns Redcon into infrastructure that
manages context for AI agents.  It sits between the coding agent and the
downstream LLM, intercepting every task and applying the full optimisation
pipeline before the prompt reaches the model.

```
agent  →  AgentRuntime  →  LLM
              │
    ┌─────────┴──────────┐
    repo scan             cache reuse
    file ranking          delta prompts
    symbol extraction     token budget
    context slicing       policy checks
    compression
```

---

## Quick-start

```python
from redcon.runtime import AgentRuntime

# No LLM — just prepare and inspect context
runtime = AgentRuntime(max_tokens=32_000)
result  = runtime.run("add Redis caching to the session store", repo=".")

ctx = result.prepared_context
print(f"Files included : {len(ctx.files_included)}")
print(f"Tokens used    : {ctx.estimated_tokens}")
print(f"Tokens saved   : {ctx.tokens_saved}")
print(f"Quality risk   : {ctx.quality_risk}")
print(ctx.prompt_text[:800])
```

---

## Architecture

### `agent → AgentRuntime → LLM`

`AgentRuntime` is the interception layer.  For every agent turn it:

1. **Intercepts** the task description and repository path.
2. **Runs the pipeline** — scan, rank, symbol-extract, slice, compress, cache,
   delta.
3. **Enforces constraints** — token budget, quality-risk policy, file-count
   limits.
4. **Assembles the prompt** — serialises the optimised context into a plain
   text `prompt_text` string.
5. **Dispatches (optional)** — calls the registered `llm_fn` with the prompt.
6. **Records the turn** — appends metrics to the `RuntimeSession` for
   cumulative tracking.

### Delta context

After the first turn the runtime automatically passes the previous run
artifact as `delta_from`.  Only files that changed since the last pack are
re-sent; unchanged files are referenced from the cache.  This can dramatically
reduce token usage for iterative coding tasks.

To disable delta behaviour:

```python
runtime = AgentRuntime(delta=False)
```

---

## Classes

### `AgentRuntime`

```python
from redcon.runtime import AgentRuntime
```

**Constructor parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_tokens` | `int \| None` | config default | Token budget for the packed context |
| `top_files` | `int \| None` | config default | Max ranked files considered |
| `policy` | `PolicySpec \| None` | `None` | Policy object to evaluate on every turn |
| `policy_path` | `str \| Path \| None` | `None` | Path to a TOML policy file |
| `strict` | `bool` | `False` | Raise `BudgetPolicyViolationError` on policy failure |
| `delta` | `bool` | `True` | Auto-pass previous run as delta context |
| `llm_fn` | `Callable[[str], str] \| None` | `None` | LLM dispatch callable |
| `config_path` | `str \| Path \| None` | `None` | Path to `redcon.toml` |
| `session` | `RuntimeSession \| None` | new session | Session to resume |
| `engine` | `RedconEngine \| None` | new engine | Engine to reuse |

**Methods**

#### `prepare_context(task, repo, ...) → PreparedContext`

Intercepts a task + repo and runs the full optimisation pipeline.  Returns a
`PreparedContext` without dispatching to the LLM.

```python
ctx = runtime.prepare_context("refactor the auth module", repo=".")
print(ctx.prompt_text)
```

#### `run(task, repo, ...) → RuntimeResult`

Full turn cycle: `prepare_context` → optional LLM dispatch → session record.

```python
result = runtime.run("add unit tests for the payment module", repo=".")
print(result.prepared_context.estimated_tokens)
print(result.llm_response)          # None if no llm_fn registered
print(result.session_tokens)        # cumulative tokens this session
```

#### `session_summary() → dict`

Return a JSON-serialisable summary of the current session.

```python
summary = runtime.session_summary()
print(summary["turn_count"])
print(summary["cumulative_tokens"])
```

#### `reset_session()`

Clear session history and reset cumulative counters.

---

### `PreparedContext`

Returned by `prepare_context()` and accessible via `RuntimeResult.prepared_context`.

| Field | Type | Description |
|-------|------|-------------|
| `task` | `str` | Original task description |
| `repo` | `str` | Repository path |
| `prompt_text` | `str` | Assembled, compressed context ready for the LLM |
| `files_included` | `list[str]` | Ordered file paths in the context |
| `estimated_tokens` | `int` | Estimated input token count |
| `tokens_saved` | `int` | Tokens eliminated by the pipeline |
| `quality_risk` | `str` | Compression risk: `low`, `medium`, or `high` |
| `policy_passed` | `bool \| None` | Policy result (`None` = not evaluated) |
| `policy_violations` | `list[str]` | Violation messages (empty when passing) |
| `delta_enabled` | `bool` | Whether delta context was applied |
| `cache_hits` | `int` | Context fragments served from cache |
| `metadata` | `dict` | Full middleware metrics |
| `run_artifact` | `dict` | Raw pipeline artifact for auditing |

---

### `RuntimeResult`

Returned by `run()`.

| Field | Type | Description |
|-------|------|-------------|
| `prepared_context` | `PreparedContext` | The optimised context |
| `llm_response` | `str \| None` | LLM response, or `None` |
| `turn_number` | `int` | 1-based turn index |
| `session_tokens` | `int` | Cumulative tokens across all turns |
| `session_id` | `str` | Session UUID |

---

### `RuntimeSession`

Tracks multi-turn state.  Created automatically; access via `runtime.session`.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | UUID for this session |
| `turns` | `list[dict]` | Per-turn metric summaries |
| `cumulative_tokens` | `int` | Total input tokens consumed |
| `last_run_artifact` | `dict \| None` | Previous run (used for delta) |
| `turn_number` | `int` (property) | Next turn index (1-based) |

---

## Recipes

### With an LLM

```python
import anthropic
from redcon.runtime import AgentRuntime

client = anthropic.Anthropic()

def call_claude(prompt: str) -> str:
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text

runtime = AgentRuntime(max_tokens=32_000, llm_fn=call_claude)
result  = runtime.run("add input validation to the signup endpoint", repo=".")
print(result.llm_response)
```

### Token budget enforcement

```python
from redcon.runtime import AgentRuntime

# Hard budget + quality gate
runtime = AgentRuntime(
    max_tokens=20_000,
    strict=True,            # raises BudgetPolicyViolationError on violations
)
result = runtime.run("refactor the database layer", repo=".")
```

### Custom policy from TOML

Create `policy.toml`:

```toml
max_estimated_input_tokens = 25000
max_files_included         = 30
max_quality_risk_level     = "medium"
min_estimated_savings_percentage = 10.0
```

```python
from redcon.runtime import AgentRuntime

runtime = AgentRuntime(policy_path="policy.toml", strict=True)
result  = runtime.run("add caching layer", repo=".")
```

### Multi-turn session

```python
from redcon.runtime import AgentRuntime

runtime = AgentRuntime(max_tokens=32_000, delta=True)

# Turn 1 — full context pack
r1 = runtime.run("scaffold the new payments module", repo=".")
print(f"Turn 1: {r1.prepared_context.estimated_tokens} tokens")

# Turn 2 — delta: only changed files are re-sent
r2 = runtime.run("add error handling to the payments module", repo=".")
print(f"Turn 2: {r2.prepared_context.estimated_tokens} tokens (delta)")

# Session summary
summary = runtime.session_summary()
print(f"Total turns    : {summary['turn_count']}")
print(f"Total tokens   : {summary['cumulative_tokens']}")
```

### Resuming a session

```python
from redcon.runtime import AgentRuntime, RuntimeSession

# Restore a saved session
previous_session = RuntimeSession(session_id="abc-123")
previous_session.cumulative_tokens = 14_500

runtime = AgentRuntime(session=previous_session)
result  = runtime.run("finalise the payments module", repo=".")
print(f"Session tokens: {result.session_tokens}")
```

### Inspecting the pipeline artifact

```python
result = runtime.run("add caching", repo=".")
artifact = result.prepared_context.run_artifact

# Pipeline stages
from redcon.runtime import AgentRuntime
from redcon.core.pipeline_trace import build_pipeline_trace

trace = build_pipeline_trace(artifact)
for stage in trace.stages:
    print(f"{stage.label:30s}  saved={stage.tokens_saved:,}")
```

---

## Relationship to existing APIs

| API | Role |
|-----|------|
| `RedconEngine` | Low-level pack/plan/simulate engine |
| `BudgetGuard` | Strict-policy wrapper around `RedconEngine` |
| `RedconMiddleware` | One-shot context prep for agent integrations |
| **`AgentRuntime`** | **Stateful multi-turn runtime with LLM dispatch** |

`AgentRuntime` builds on top of `RedconMiddleware` and adds session
tracking, delta context across turns, and the `llm_fn` dispatch interface.
It does **not** replace any existing API.

---

## Error handling

| Exception | When raised |
|-----------|-------------|
| `BudgetPolicyViolationError` | `strict=True` and policy fails |
| `FileNotFoundError` | `repo` path does not exist |
| Any exception from `llm_fn` | Propagated as-is; context is still prepared |
