# Deployment Architecture

## Overview

Redcon has two independently deployable tiers.

```
┌──────────────────────────────────────────────────────────┐
│                      AGENT TIER                          │
│                                                          │
│  Agent process  →  Redcon Gateway  →  LLM API    │
│                     (redcon package)              │
│                     FastAPI / stdlib HTTP                │
│                     port 8787 (default)                  │
└─────────────────────────┬────────────────────────────────┘
                          │  POST /events   (telemetry push)
                          │  GET  /policies/active (policy fetch)
                          ▼
┌──────────────────────────────────────────────────────────┐
│                   CONTROL PLANE TIER                     │
│                                                          │
│  Redcon Cloud  (private repo) or local            │
│  redcon control-plane (SQLite)                           │
│  port 8080 (default)                                     │
└──────────────────────────────────────────────────────────┘
```

The two tiers are **decoupled** - the agent tier runs without the control plane. The control plane is optional for local development and becomes useful when multiple agents or teams share infrastructure.

---

## Agent Tier

The runtime gateway is part of the `redcon` Python package.

```bash
pip install "redcon[gateway]"
export RC_GATEWAY_API_KEY=my-secret-key
redcon gateway --host 0.0.0.0 --port 8787
```

The gateway refuses to start on a non-loopback host (anything other than
`127.0.0.1` / `::1`) unless `RC_GATEWAY_API_KEY` is set, so an unauthenticated
service is never exposed to the network by accident.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `RC_GATEWAY_HOST` | `127.0.0.1` | Bind address |
| `RC_GATEWAY_PORT` | `8787` | TCP port |
| `RC_GATEWAY_API_KEY` | *(none)* | Bearer token auth (leave unset to disable) |
| `RC_GATEWAY_MAX_TOKENS` | `128000` | Default token budget |
| `RC_GATEWAY_MAX_FILES` | `100` | Default top-file cap |
| `RC_GATEWAY_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `RC_GATEWAY_CLOUD_POLICY_URL` | *(none)* | Cloud service base URL for remote policy fetch |
| `RC_GATEWAY_CLOUD_API_KEY` | *(none)* | Bearer key for the cloud service |
| `RC_GATEWAY_CLOUD_ORG_ID` | *(none)* | Org ID to scope policy lookups |

When `RC_GATEWAY_CLOUD_POLICY_URL` is set, the gateway fetches the active `PolicySpec` from the control plane before each request and enforces it server-side. If the fetch fails, the gateway continues with the locally-configured policy.

---

## Control Plane Tier

The hosted multi-tenant control plane (Redcon Cloud: org and project
management, API keys, quotas, usage metering, billing, dashboards) is
commercial and lives in a separate private repository. This public
repository ships the local single-node alternative:

```bash
redcon control-plane        # stdlib HTTP + SQLite, single team, no auth
```

The gateway integrates with either through the same environment
variables (`RC_GATEWAY_CLOUD_POLICY_URL`, `RC_GATEWAY_CLOUD_API_KEY`,
`RC_GATEWAY_CLOUD_ORG_ID`).

---

## Network trust model

| Path | Auth | Notes |
|---|---|---|
| `POST /events` | None | Internal use; put behind VPC or nginx auth |
| `GET /health` | None | Liveness probe |
| `POST /orgs` | None | Operator bootstrap; put behind VPC |
| `POST /orgs/{id}/api-keys` | None | Operator bootstrap; put behind VPC |
| All other endpoints | Bearer API key | Issued via `/orgs/{id}/api-keys` |

For production: run the control plane behind a reverse proxy that restricts `/orgs` and `/events` to internal networks.

---

## Scaling notes

- The control plane is stateless beyond the database connection pool. Horizontal scaling is safe.
- PostgreSQL connection pool defaults: min=2, max=10 per process.
- The gateway is stateful (in-memory session registry for multi-turn runs). Do not load-balance a single agent session across multiple gateway instances.
