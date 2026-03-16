# Redcon Demo

End-to-end walkthrough of all core components against this repository.

## Quick start

```bash
# From the repo root — no extra dependencies needed
python demo/run_demo.py
```

For the FastAPI gateway section, install the gateway extra first:

```bash
pip install -e ".[gateway]"
python demo/run_demo.py
```

## What the demo covers

| Step | Component | What it shows |
|------|-----------|---------------|
| 1 | **Pack** | Scan, rank, and compress repository context under a token budget |
| 2 | **Policy** | Evaluate token-budget policy constraints against a run artifact |
| 3 | **Cost analytics** | Translate token savings into estimated USD savings (gpt-4o pricing) |
| 4 | **Benchmark** | Compare packing strategies side-by-side |
| 5 | **Gateway** | Start the FastAPI gateway in-process and send real HTTP requests |
| 6 | **Adapters** | Instantiate the OpenAI and Anthropic wrappers with a stub LLM function |

No network calls are made. The gateway runs on `127.0.0.1:18787` for the duration
of the demo and is stopped immediately after. The adapter demo uses a stub LLM
function so no real API keys are required.

## Full stack (gateway + Redis)

```bash
# Copy and configure
cp .env.example .env          # set RC_GATEWAY_API_KEY etc.

# Start gateway + Redis
docker compose up -d

# Send a request
curl -s http://localhost:8787/health

curl -s -X POST http://localhost:8787/prepare-context \
  -H "Content-Type: application/json" \
  -d '{"task": "add caching to the session store", "repo": "/repos/myproject", "max_tokens": 32000}'
```

## Architecture

```
agent / CI pipeline
      |
      | HTTP JSON
      v
Redcon Gateway  (POST /prepare-context, /run-agent-step, /report-run)
      |               |
      |         Redis session store  (horizontal scaling)
      |
      v
scan -> rank -> compress -> cache -> delta
      |
      v
optimized prompt  -->  OpenAI / Anthropic / any LLM
```

## Key endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check |
| `/metrics` | GET | Request counters and uptime |
| `/prepare-context` | POST | Stateless context optimization |
| `/run-agent-step` | POST | Stateful multi-turn agent session |
| `/report-run` | POST | Acknowledge LLM completion and record telemetry |

## Using the adapters directly

```python
from redcon.integrations import OpenAIAgentWrapper, AnthropicAgentWrapper

# OpenAI
agent = OpenAIAgentWrapper(model="gpt-4.1", repo=".")
result = agent.run_task("add caching to the API")
print(result.llm_response)

# Anthropic
agent = AnthropicAgentWrapper(model="claude-sonnet-4-6", repo=".")
result = agent.run_task("add caching to the API")
print(result.llm_response)
```

Both wrappers automatically run the full Redcon pipeline before every LLM call
and emit telemetry to `.redcon/observe-history.json`.

## Enforcing a token-budget policy

```python
from redcon.core.policy import PolicySpec, evaluate_policy

policy = PolicySpec(
    max_estimated_input_tokens=64_000,
    max_files_included=40,
    max_quality_risk_level="medium",
)

from redcon.engine import RedconEngine
artifact = RedconEngine().pack("my task", repo=".")
result = evaluate_policy(artifact, policy)
print(result.passed, result.violations)
```

## GitHub Action

```yaml
- uses: natiixnt/ContextBudget@v1
  with:
    task: "review pull request changes"
    policy: .github/redcon-policy.toml
    strict: "true"
    model: gpt-4o
```

See [action.yml](../action.yml) for all inputs and outputs.
