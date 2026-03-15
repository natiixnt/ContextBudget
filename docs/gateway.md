# Runtime Gateway

The ContextBudget Runtime Gateway exposes the full context optimization pipeline as an authenticated HTTP service. Agent frameworks call it instead of embedding the SDK directly.

## Quick-start

```bash
# Install gateway extras (FastAPI + Uvicorn)
pip install 'contextbudget[gateway]'

# Start with API key auth
export CB_GATEWAY_API_KEY=my-secret-key
contextbudget gateway --host 0.0.0.0 --port 8787 --api-key "$CB_GATEWAY_API_KEY"
```

Without the extras, the gateway falls back to a stdlib HTTP implementation (single-threaded, suitable for development only).

## Endpoints

### `GET /health`

Returns service liveness status.

```json
{"status": "ok", "version": "1.0.0-alpha"}
```

### `GET /metrics`

Returns basic runtime statistics.

```json
{
  "gateway": {
    "requests_total": 42,
    "requests_active": 1,
    "uptime_seconds": 3600.0
  }
}
```

### `POST /prepare-context`

Stateless — runs the full pipeline once and returns compressed context.

**Request:**
```json
{
  "task": "add caching layer to the auth service",
  "repo": "/path/to/repo",
  "max_tokens": 64000,
  "max_files": 80
}
```

**Response:**
```json
{
  "optimized_context": {
    "files": [...],
    "prompt_text": "# File: src/auth.py\n...",
    "files_included": ["src/auth.py", "src/cache.py"]
  },
  "token_estimate": 12400,
  "tokens_saved": 8300,
  "cache_hits": 3,
  "quality_risk": "low",
  "policy_status": {"passed": true, "violations": []},
  "run_id": "abc123",
  "session_id": "xyz789"
}
```

### `POST /run-agent-step`

Stateful — subsequent calls with the same `session_id` apply automatic delta context (only changed files are re-sent between turns).

**Request:**
```json
{
  "task": "implement the login handler",
  "repo": "/path/to/repo",
  "session_id": "xyz789",
  "max_tokens": 64000
}
```

**Response adds:**
```json
{
  "turn": 2,
  "session_tokens": 28000,
  "llm_response": null
}
```

### `POST /report-run`

Acknowledge that a run completed and record telemetry.

```json
{
  "session_id": "xyz789",
  "run_id": "abc123",
  "status": "success",
  "tokens_used": 14200
}
```

## Authentication

Set `api_key` in config or `CB_GATEWAY_API_KEY` env var to enable Bearer token auth.

```bash
# All requests must include:
Authorization: Bearer my-secret-key
```

Requests without a valid key receive a `401` response:
```json
{"detail": "Invalid API key"}
```

Leave `api_key` unset to disable auth (development mode).

## Configuration

All config fields can be set via CLI flags or environment variables:

| Field | CLI flag | Env var | Default |
|---|---|---|---|
| `host` | `--host` | `CB_GATEWAY_HOST` | `127.0.0.1` |
| `port` | `--port` | `CB_GATEWAY_PORT` | `8787` |
| `api_key` | `--api-key` | `CB_GATEWAY_API_KEY` | `None` (disabled) |
| `max_tokens` | `--max-tokens` | `CB_GATEWAY_MAX_TOKENS` | `128000` |
| `max_files` | `--max-files` | `CB_GATEWAY_MAX_FILES` | `100` |
| `request_timeout_seconds` | — | `CB_GATEWAY_TIMEOUT_SECONDS` | `30` |
| `config_path` | `--config` | `CB_GATEWAY_CONFIG_PATH` | `None` |
| `telemetry_enabled` | `--telemetry` | `CB_GATEWAY_TELEMETRY` | `false` |
| `log_requests` | `--no-log-requests` | `CB_GATEWAY_LOG_REQUESTS` | `true` |

## Python API

```python
from contextbudget.gateway import GatewayConfig, GatewayServer

config = GatewayConfig(
    host="0.0.0.0",
    port=8787,
    api_key="my-secret-key",
    max_tokens=64_000,
    request_timeout_seconds=30,
)

# Block until Ctrl-C
GatewayServer(config).start()

# Or run in background
server = GatewayServer(config)
server.start(block=False)
# ...
server.stop()
```

## Docker

```dockerfile
FROM python:3.12-slim
RUN pip install 'contextbudget[gateway]'
ENV CB_GATEWAY_HOST=0.0.0.0
ENV CB_GATEWAY_PORT=8787
CMD ["contextbudget-gateway"]
```

```bash
docker run -e CB_GATEWAY_API_KEY=secret -p 8787:8787 contextbudget-gateway
```

## k8s Health Probes

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8787
  initialDelaySeconds: 5
readinessProbe:
  httpGet:
    path: /health
    port: 8787
```
