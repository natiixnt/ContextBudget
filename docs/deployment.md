# Deployment Architecture

## Overview

ContextBudget has two independently deployable tiers.

```
┌──────────────────────────────────────────────────────────┐
│                      AGENT TIER                          │
│                                                          │
│  Agent process  →  ContextBudget Gateway  →  LLM API    │
│                     (contextbudget package)              │
│                     FastAPI / stdlib HTTP                │
│                     port 8787 (default)                  │
└─────────────────────────┬────────────────────────────────┘
                          │  POST /events   (telemetry push)
                          │  GET  /policies/active (policy fetch)
                          ▼
┌──────────────────────────────────────────────────────────┐
│                   CONTROL PLANE TIER                     │
│                                                          │
│  contextbudget-cloud  (FastAPI + asyncpg)                │
│  PostgreSQL database                                     │
│  port 8080 (default)                                     │
└──────────────────────────────────────────────────────────┘
```

The two tiers are **decoupled** — the agent tier runs without the control plane. The control plane is optional for local development and becomes useful when multiple agents or teams share infrastructure.

---

## Agent Tier

The runtime gateway is part of the `contextbudget` Python package.

```bash
pip install 'contextbudget[gateway]'
export CB_GATEWAY_API_KEY=my-secret-key
contextbudget gateway --host 0.0.0.0 --port 8787
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `CB_GATEWAY_HOST` | `127.0.0.1` | Bind address |
| `CB_GATEWAY_PORT` | `8787` | TCP port |
| `CB_GATEWAY_API_KEY` | *(none)* | Bearer token auth (leave unset to disable) |
| `CB_GATEWAY_MAX_TOKENS` | `128000` | Default token budget |
| `CB_GATEWAY_MAX_FILES` | `100` | Default top-file cap |
| `CB_GATEWAY_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `CB_GATEWAY_CLOUD_POLICY_URL` | *(none)* | Cloud service base URL for remote policy fetch |
| `CB_GATEWAY_CLOUD_API_KEY` | *(none)* | Bearer key for the cloud service |
| `CB_GATEWAY_CLOUD_ORG_ID` | *(none)* | Org ID to scope policy lookups |

When `CB_GATEWAY_CLOUD_POLICY_URL` is set, the gateway fetches the active `PolicySpec` from the control plane before each request and enforces it server-side. If the fetch fails, the gateway continues with the locally-configured policy.

---

## Control Plane Tier

```bash
cd contextbudget-cloud
docker-compose up          # starts PostgreSQL + FastAPI on port 8080
```

Or run the app directly:

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/contextbudget
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Database setup

Apply migrations in order:

```bash
psql $DATABASE_URL -f migrations/001_create_events.sql
psql $DATABASE_URL -f migrations/002_create_control_plane.sql
```

Migrations are idempotent (`CREATE TABLE IF NOT EXISTS`).

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://contextbudget:contextbudget@db:5432/contextbudget` | PostgreSQL DSN |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8080` | TCP port |

---

## Docker Compose (recommended for trials)

The included `docker-compose.yml` starts both PostgreSQL and the cloud service:

```bash
cd contextbudget-cloud
docker-compose up -d
```

Services:
- `db` — PostgreSQL 15 on port 5432
- `app` — ContextBudget Cloud on port 8080

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
