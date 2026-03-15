# Agent integrations

`redcon.integrations` provides production-ready wrappers that slot
Redcon into the call path between a coding agent and the downstream LLM.

Every wrapper:

1. **Intercepts** the task description and repository path.
2. **Optimises** context via the full Redcon pipeline (scan → rank →
   compress → cache → delta).
3. **Calls** the model API with the packed prompt.
4. **Emits** run telemetry to `.redcon/observe-history.json`.

---

## OpenAIAgentWrapper

Wraps the [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat).

**Install the optional dependency:**

```bash
pip install openai
```

### Basic usage

```python
from redcon.integrations import OpenAIAgentWrapper

agent = OpenAIAgentWrapper(
    model="gpt-4.1",
    repo=".",
)

result = agent.run_task("add caching to API")
print(result.llm_response)
print(f"tokens used: {result.prepared_context.estimated_tokens}")
print(f"tokens saved: {result.prepared_context.tokens_saved}")
```

### Constructor parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"gpt-4.1"` | OpenAI model identifier |
| `repo` | `str \| Path` | `"."` | Default repository path |
| `max_tokens` | `int \| None` | `None` | Token budget for packed context |
| `top_files` | `int \| None` | `None` | Max ranked files considered |
| `max_completion_tokens` | `int` | `2048` | `max_tokens` forwarded to the API |
| `system_prompt` | `str \| None` | `None` | Optional system message |
| `policy` | `PolicySpec \| None` | `None` | Budget policy to enforce |
| `strict` | `bool` | `False` | Raise on policy violations |
| `delta` | `bool` | `True` | Enable incremental delta context |
| `config_path` | `str \| Path \| None` | `None` | Path to `redcon.toml` |
| `session` | `RuntimeSession \| None` | `None` | Resume an existing session |
| `engine` | `RedconEngine \| None` | `None` | Reuse an existing engine |
| `openai_client` | `openai.OpenAI \| None` | `None` | Pre-constructed client |
| `telemetry_base_dir` | `str \| Path \| None` | `None` | Base dir for observe-history |

### With a policy

```python
from redcon.integrations import OpenAIAgentWrapper
from redcon.engine import RedconEngine

agent = OpenAIAgentWrapper(
    model="gpt-4.1",
    repo=".",
    max_tokens=32_000,
    policy=RedconEngine.make_policy(
        max_estimated_input_tokens=32_000,
        max_quality_risk_level="medium",
    ),
    strict=True,
)

result = agent.run_task("refactor auth middleware")
```

### Multi-turn sessions

```python
agent = OpenAIAgentWrapper(model="gpt-4.1", repo=".", max_tokens=32_000)

result1 = agent.run_task("add Redis caching to session store")
result2 = agent.run_task("write unit tests for the new cache layer")

print(agent.session_summary())
# {"session_id": "...", "turns": 2, "cumulative_tokens": ...}
```

Delta context is enabled by default: on the second turn, only changed files
are re-sent to the model.

---

## AnthropicAgentWrapper

Wraps the [Anthropic Messages API](https://docs.anthropic.com/en/api/messages).

**Install the optional dependency:**

```bash
pip install anthropic
```

### Basic usage

```python
from redcon.integrations import AnthropicAgentWrapper

agent = AnthropicAgentWrapper(
    model="claude-sonnet-4-6",
    repo=".",
)

result = agent.run_task("add caching to API")
print(result.llm_response)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"claude-sonnet-4-6"` | Anthropic model identifier |
| `repo` | `str \| Path` | `"."` | Default repository path |
| `max_tokens` | `int \| None` | `None` | Token budget for packed context |
| `top_files` | `int \| None` | `None` | Max ranked files considered |
| `max_completion_tokens` | `int` | `2048` | `max_tokens` forwarded to the API |
| `system_prompt` | `str \| None` | `None` | Optional system prompt |
| `policy` | `PolicySpec \| None` | `None` | Budget policy to enforce |
| `strict` | `bool` | `False` | Raise on policy violations |
| `delta` | `bool` | `True` | Enable incremental delta context |
| `config_path` | `str \| Path \| None` | `None` | Path to `redcon.toml` |
| `session` | `RuntimeSession \| None` | `None` | Resume an existing session |
| `engine` | `RedconEngine \| None` | `None` | Reuse an existing engine |
| `anthropic_client` | `anthropic.Anthropic \| None` | `None` | Pre-constructed client |
| `telemetry_base_dir` | `str \| Path \| None` | `None` | Base dir for observe-history |

### With a system prompt

```python
from redcon.integrations import AnthropicAgentWrapper

agent = AnthropicAgentWrapper(
    model="claude-opus-4-6",
    repo=".",
    max_tokens=64_000,
    system_prompt=(
        "You are an expert software engineer. "
        "Answer concisely and include working code."
    ),
)

result = agent.run_task("add input validation to the user registration endpoint")
print(result.llm_response)
```

---

## GenericAgentRunner

A vendor-neutral runner that accepts any `(prompt: str) -> str` callable as
the LLM backend.  Use this for local models, custom API wrappers, or any
provider not covered by the first-party wrappers.

### Basic usage

```python
from redcon.integrations import GenericAgentRunner

def my_llm(prompt: str) -> str:
    # call any model you like
    return call_my_model(prompt)

runner = GenericAgentRunner(llm_fn=my_llm, repo=".")
result = runner.run_task("add caching to API")
print(result.llm_response)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `llm_fn` | `Callable[[str], str]` | *(required)* | Backend callable |
| `repo` | `str \| Path` | `"."` | Default repository path |
| `adapter_name` | `str` | `"generic"` | Label used in telemetry |
| `max_tokens` | `int \| None` | `None` | Token budget for packed context |
| `top_files` | `int \| None` | `None` | Max ranked files considered |
| `policy` | `PolicySpec \| None` | `None` | Budget policy to enforce |
| `strict` | `bool` | `False` | Raise on policy violations |
| `delta` | `bool` | `True` | Enable incremental delta context |
| `config_path` | `str \| Path \| None` | `None` | Path to `redcon.toml` |
| `session` | `RuntimeSession \| None` | `None` | Resume an existing session |
| `engine` | `RedconEngine \| None` | `None` | Reuse an existing engine |
| `telemetry_base_dir` | `str \| Path \| None` | `None` | Base dir for observe-history |

### Example: local Ollama model

```python
import requests
from redcon.integrations import GenericAgentRunner

def ollama_llm(prompt: str) -> str:
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": "codellama", "prompt": prompt, "stream": False},
        timeout=120,
    )
    return response.json()["response"]

runner = GenericAgentRunner(
    llm_fn=ollama_llm,
    repo=".",
    adapter_name="ollama-codellama",
    max_tokens=8_000,
)

result = runner.run_task("add error handling to the payment module")
print(result.llm_response)
```

---

## run_task return value

All three wrappers return a
[`RuntimeResult`](python-api.md#runtimeresult) with the following fields:

| Field | Type | Description |
|---|---|---|
| `prepared_context` | `PreparedContext` | Optimised context details |
| `llm_response` | `str \| None` | Raw response from the model |
| `turn_number` | `int` | 1-based turn index in the session |
| `session_tokens` | `int` | Cumulative input tokens this session |
| `session_id` | `str` | UUID identifying the current session |

`prepared_context` exposes:

| Field | Description |
|---|---|
| `prompt_text` | The assembled context string sent to the LLM |
| `files_included` | Ordered list of packed file paths |
| `estimated_tokens` | Estimated input token count |
| `tokens_saved` | Tokens eliminated by compression and caching |
| `quality_risk` | Compression risk level (`low`, `medium`, `high`) |
| `policy_passed` | Policy evaluation result (`True`, `False`, or `None`) |
| `delta_enabled` | Whether incremental delta was applied |
| `cache_hits` | Number of context fragments served from cache |

---

## Telemetry

Each `run_task` call appends an entry to `.redcon/observe-history.json`
(capped at 500 entries).  The entry schema:

```json
{
  "adapter": "openai",
  "model": "gpt-4.1",
  "task": "add caching to API",
  "repo": "/path/to/repo",
  "session_id": "uuid",
  "turn_number": 1,
  "session_tokens": 1850,
  "estimated_tokens": 1850,
  "tokens_saved": 3200,
  "files_included": ["src/cache.py", "src/api.py"],
  "quality_risk": "low",
  "policy_passed": true,
  "delta_enabled": false,
  "cache_hits": 0,
  "llm_prompt_tokens": 1850,
  "llm_completion_tokens": 312,
  "llm_total_tokens": 2162,
  "generated_at": "2026-03-15T12:00:00+00:00"
}
```

`llm_prompt_tokens`, `llm_completion_tokens`, and `llm_total_tokens` are populated from the LLM API response when the provider reports usage. They are omitted if the API does not return usage data (e.g. when using `GenericAgentRunner` or `NodeJSAgentRunner`).

Read history programmatically:

```python
from redcon.telemetry.store import load_observe_history

entries = load_observe_history(base_dir=".")
for entry in entries:
    print(entry["adapter"], entry["tokens_saved"])
```

---

## NodeJSAgentRunner

A runner that passes the optimised context prompt to a **Node.js script** via
stdin and reads the LLM response from stdout.  Use this to plug Redcon
into any Node.js agent loop — LangChain.js, Vercel AI SDK, OpenAI Node SDK,
or custom scripts — without writing Python.

### Node.js script contract

The runner writes the assembled prompt to the script's **stdin** (UTF-8,
closed at EOF) and reads the response from **stdout**.  stderr is forwarded
to the Python process.

Minimal `agent.js`:

```js
import OpenAI from "openai";

const prompt = await new Promise((resolve) => {
    let data = "";
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => resolve(data));
});

const client = new OpenAI();
const response = await client.chat.completions.create({
    model: "gpt-4.1",
    messages: [{ role: "user", content: prompt }],
});

process.stdout.write(response.choices[0].message.content);
```

### Basic usage

```python
from redcon.integrations import NodeJSAgentRunner

runner = NodeJSAgentRunner(
    script="./agent.js",
    repo=".",
)

result = runner.run_task("add caching")
print(result.llm_response)
print(f"tokens used: {result.prepared_context.estimated_tokens}")
print(f"tokens saved: {result.prepared_context.tokens_saved}")
```

### Custom command

```python
runner = NodeJSAgentRunner(
    command=["node", "--experimental-vm-modules", "agent.js"],
    repo=".",
    max_tokens=32_000,
)

result = runner.run_task("refactor auth middleware")
```

### Constructor parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `script` | `str \| Path \| None` | `None` | Path to a `.js`/`.mjs` entry point |
| `repo` | `str \| Path` | `"."` | Default repository path |
| `command` | `Sequence[str] \| None` | `None` | Full command list (overrides `script`) |
| `node_executable` | `str` | `"node"` | Node.js binary name or path |
| `adapter_name` | `str` | `"nodejs"` | Label used in telemetry |
| `max_tokens` | `int \| None` | `None` | Token budget for packed context |
| `top_files` | `int \| None` | `None` | Max ranked files considered |
| `policy` | `PolicySpec \| None` | `None` | Budget policy to enforce |
| `strict` | `bool` | `False` | Raise on policy violations |
| `delta` | `bool` | `True` | Enable incremental delta context |
| `timeout` | `float \| None` | `None` | Seconds to wait for the script |
| `env` | `dict[str, str] \| None` | `None` | Extra environment variables for the script |
| `config_path` | `str \| Path \| None` | `None` | Path to `redcon.toml` |
| `session` | `RuntimeSession \| None` | `None` | Resume an existing session |
| `engine` | `RedconEngine \| None` | `None` | Reuse an existing engine |
| `telemetry_base_dir` | `str \| Path \| None` | `None` | Base dir for observe-history |

### Passing environment variables

```python
runner = NodeJSAgentRunner(
    script="./agent.js",
    repo=".",
    env={"OPENAI_API_KEY": "sk-..."},
    timeout=60.0,
)

result = runner.run_task("add input validation")
```

### Multi-turn sessions

```python
runner = NodeJSAgentRunner(script="./agent.js", repo=".", max_tokens=32_000)

result1 = runner.run_task("add Redis caching to session store")
result2 = runner.run_task("write unit tests for the new cache layer")

print(runner.session_summary())
```

Delta context is enabled by default — on the second turn only changed files
are re-sent to the Node.js script.

### Vercel AI SDK example (`agent.js`)

```js
import { generateText } from "ai";
import { openai } from "@ai-sdk/openai";

const prompt = await new Promise((resolve) => {
    let data = "";
    process.stdin.on("data", (c) => (data += c));
    process.stdin.on("end", () => resolve(data));
});

const { text } = await generateText({
    model: openai("gpt-4.1"),
    prompt,
});

process.stdout.write(text);
```

---

## Compatibility with the runtime pipeline

All wrappers delegate to
[`AgentRuntime`](agent-runtime.md), which is the canonical
`agent → Redcon → LLM` entry point.  Any feature supported by
`AgentRuntime` — policies, delta context, custom engines, session replay — is
available through the integration wrappers.

To compose with the lower-level middleware directly, see
[`RedconMiddleware`](agent-integration.md).
