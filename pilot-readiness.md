# Redcon — Pilot Readiness

> Last updated: 2026-03-15
> Version: v1.1

This document describes what is production-ready, what is in beta, known limitations, and the recommended deployment model for early paid pilots.

---

## What is Production-Ready

### Core Engine
| Feature | Status | Notes |
|---------|--------|-------|
| File scanning and ranking | ✅ Production | Used in 600+ tests |
| Token-budget enforcement | ✅ Production | Configurable via TOML policy |
| Context compression & summarization | ✅ Production | Multiple strategies; pluggable |
| Incremental scanning (delta context) | ✅ Production | Git-aware; skips unchanged files |
| Prompt cache tracking | ✅ Production | Local file and Redis backends |
| Policy evaluation (PASS/FAIL) | ✅ Production | Strict and soft modes |

### CLI (`redcon`)
| Command | Status | Notes |
|---------|--------|-------|
| `pack` | ✅ Production | Core use case |
| `plan` / `plan-agent` | ✅ Production | Pre-run context planning |
| `cost-analysis` | ✅ Production | USD savings from a run artifact |
| `roi` | ✅ Production | Aggregate ROI across many runs |
| `benchmark-report` | ✅ Production | Customer-facing markdown report |
| `enforce` | ✅ Production | CI policy gate |
| `drift` | ✅ Production | Context growth alerting |
| `heatmap` | ✅ Production | Token usage visualization |
| `init` | ✅ Production | Zero-config onboarding |

### Gateway (HTTP API)
| Feature | Status | Notes |
|---------|--------|-------|
| `/prepare-context` | ✅ Production | Stateless context optimization |
| `/run-agent-step` | ✅ Production | Multi-turn agent sessions |
| `/report-run` | ✅ Production | Run outcome telemetry |
| Bearer auth | ✅ Production | Static API key |
| Remote policy fetch | ✅ Production | Pulls from cloud control plane |
| Audit push to cloud | ✅ Production | Fire-and-forget; non-blocking |
| Webhook dispatch | ✅ Production | Policy violations + budget overruns |

### Cloud Control Plane (`redcon-cloud`)
| Feature | Status | Notes |
|---------|--------|-------|
| Event ingestion (`POST /events`) | ✅ Production | Batch-compatible |
| Org / Project / Repo hierarchy | ✅ Production | Full CRUD with cascade delete |
| API key management | ✅ Production | Hashed; expiry supported |
| Audit log | ✅ Production | Append-only; paginated |
| Policy version management | ✅ Production | Versioned; scope inheritance |
| Cost analytics (`/analytics/cost/*`) | ✅ Production | By repo, by date, by run |
| ROI dashboard (`/dashboard/roi`) | ✅ Production | Dollars saved; top repos |
| Webhook registration | ✅ Production | HMAC-signed delivery |
| PostgreSQL row-level security | ✅ Production | Per-org isolation |

### GitHub Action
| Feature | Status | Notes |
|---------|--------|-------|
| `action.yml` (composite) | ✅ Production | Marketplace-ready |
| Token budget enforcement in CI | ✅ Production | Fail PR on policy violation |
| Cost report in GitHub Step Summary | ✅ Production | Tokens + dollars |
| Artifact upload | ✅ Production | JSON + Markdown reports |

### Deployment
| Artifact | Status | Notes |
|----------|--------|-------|
| CLI `Dockerfile` | ✅ Production | Python 3.12-slim + git |
| Cloud `Dockerfile` | ✅ Production | Python 3.12-slim + uvicorn |
| `docker-compose.yml` (dev) | ✅ Production | PostgreSQL 16 included |
| `docker-compose.prod.yml` | ✅ Production | Resource limits + nginx |
| nginx reverse proxy config | ✅ Production | TLS + rate limiting |
| Environment variable templates | ✅ Production | `.env.example` for both services |
| Database migrations (001–004) | ✅ Production | Incremental; idempotent |

---

## What is Alpha / Beta

### Alpha — Not Recommended for Production Use
| Feature | Status | Why |
|---------|--------|-----|
| Redis cache backend | ⚠️ Alpha | Limited production validation; no cluster support |
| `observe` command (metrics persistence) | ⚠️ Alpha | Schema may change |
| `visualize` HTML dependency graph | ⚠️ Alpha | Best-effort; layout issues on large repos |
| Node.js runner integration | ⚠️ Alpha | Wrapper only; limited testing |
| Plugin system | ⚠️ Alpha | API may change between minor versions |

### Beta — Functional but Not Load-Tested
| Feature | Status | Why |
|---------|--------|-----|
| Multi-turn agent runtime | 🔶 Beta | Session isolation under concurrent load not fully validated |
| Summarization stage | 🔶 Beta | Quality varies significantly by file type and LLM |
| Dashboard web UI (Next.js) | 🔶 Beta | No auth; dev-only use |
| Benchmark dataset builder | 🔶 Beta | Output format may evolve |
| PR audit command | 🔶 Beta | Requires clean git history |

---

## Known Limitations

### Scale
- **No horizontal scaling** for the gateway: sessions are in-memory, so `/run-agent-step` multi-turn sessions are node-local. Use `/prepare-context` (stateless) if you need load-balanced deployments.
- **PostgreSQL single-node**: the cloud service uses a single Postgres instance. For >100 req/s event ingestion, add a connection pooler (PgBouncer) in front.
- **No message queue**: webhook delivery is synchronous in the gateway. Under high load, slow webhook endpoints will add latency to gateway responses.

### Security
- **`POST /orgs` is unauthenticated**: org creation is a bootstrap endpoint. In production, block it at the network level (nginx `deny all` except internal IPs).
- **No rate limiting on event ingestion**: the cloud API has no built-in rate limiting per API key. Add nginx `limit_req` for unauthenticated `/events` calls.
- **Webhook secrets are hashed, not stored**: the raw secret is never recoverable after creation. Customers must save it on first creation.

### Observability
- **No built-in metrics endpoint** (Prometheus/OpenMetrics): add a `/metrics` exporter for production monitoring.
- **Structured logging only in gateway**: the cloud service logs via uvicorn defaults. Ship logs to a log aggregator (Datadog, CloudWatch, Loki) for production alerting.

### Features Not Yet Built
- **Multi-region / data residency**: all data is in a single Postgres instance with no sharding.
- **SSO / SAML / OIDC**: authentication is API-key only. No OAuth2 login flow.
- **Usage quotas and billing meters**: the control plane tracks usage but has no quota enforcement or billing hooks.
- **Slack / PagerDuty webhook templates**: webhooks deliver raw JSON; no platform-specific adapters yet.
- **SDK for non-Python runtimes**: only Python SDK provided. TypeScript SDK is not yet available.

---

## Recommended Deployment Model for Pilot Customers

### Smallest viable pilot (1–3 teams)

```
[Developer Machine / CI]
  └─ redcon CLI (pip install redcon)
       ├─ cb pack / cb roi / cb benchmark-report
       └─ GitHub Action (action.yml in repo)

[Single VPS or ECS task, 2 vCPU / 1 GB RAM]
  └─ redcon-cloud (docker-compose.prod.yml)
       ├─ FastAPI (uvicorn, 2 workers)
       ├─ PostgreSQL 16
       └─ nginx (TLS termination)
```

**Setup steps:**
1. `pip install redcon` on developer machines or in CI.
2. Run `redcon init` in each repo to generate `redcon.toml`.
3. Deploy `redcon-cloud` using `docker-compose.prod.yml`.
4. Run migrations (automatically applied via Docker init scripts).
5. Create an org: `POST /orgs` → note the `id`.
6. Issue an API key: `POST /orgs/{id}/api-keys` → save the `raw_key`.
7. Set `RC_GATEWAY_CLOUD_API_KEY` and `RC_GATEWAY_CLOUD_POLICY_URL` on developer machines.
8. Add the GitHub Action to CI repos.

### What to show in a pilot demo

1. **ROI report**: run `cb roi` across a week of PR pack runs → show tokens saved and USD saved.
2. **Benchmark report**: run `cb benchmark-report` on a real pack artifact → share the markdown with stakeholders.
3. **`/dashboard/roi` endpoint**: wire to a simple HTML page or Retool dashboard.
4. **GitHub Action step summary**: open any merged PR and click the Redcon step → tokens saved and $ saved visible inline.
5. **Policy gate**: set a `max_estimated_input_tokens` limit and open a PR that exceeds it → CI fails with a clear policy violation message.

---

## Before Enterprise Rollout

All items below have been implemented and are included in v1.1.0.

| Priority | Item | Status | Notes |
|----------|------|--------|-------|
| P0 | Prometheus `/metrics` endpoint on cloud service | ✅ Done | `GET /metrics` — standard text exposition; `app/metrics.py` |
| P0 | Rate limiting per API key on event ingestion | ✅ Done | Sliding window; `RC_CLOUD_EVENTS_RATE_LIMIT` / `_RATE_WINDOW`; `app/rate_limit.py` |
| P0 | Block `POST /orgs` by default (require admin token) | ✅ Done | `RC_CLOUD_ADMIN_TOKEN` Bearer required; 403 when unset |
| P1 | Horizontal gateway scaling (Redis session store) | ✅ Done | `RC_GATEWAY_REDIS_URL`; `redcon/gateway/session_store.py`; in-memory fallback |
| P1 | PgBouncer connection pooling for >50 req/s | ✅ Done | `docker-compose.prod.yml` — `pgbouncer` service (bitnami/pgbouncer); `deploy/pgbouncer.ini`; transaction pool mode; 20 connections per node |
| P1 | Usage quotas + per-org token allowances | ✅ Done | `app/quotas.py`; `GET|PUT /orgs/{id}/quota`; migration 005 |
| P1 | TypeScript/Node.js SDK | ✅ Done | `sdk/nodejs/` — `CloudClient` + `GatewayClient`; no runtime deps |
| P2 | SSO (OIDC) for the cloud control plane | ✅ Done | `app/oidc.py`; `RC_CLOUD_OIDC_*` env vars; `GET /auth/oidc/config` |
| P2 | Slack + PagerDuty webhook adapters | ✅ Done | `app/webhook_adapters.py`; `RC_CLOUD_SLACK_WEBHOOK_URL` / `RC_CLOUD_PAGERDUTY_ROUTING_KEY` |
| P2 | Multi-region Postgres replication | ✅ Done | `docker-compose.multiregion.yml` — primary + streaming replica + two PgBouncer pools; `READ_DATABASE_URL` routes analytics reads to replica; `deploy/postgres-primary.conf` / `postgres-replica.conf` |
| P3 | Billing meter integration (Stripe) | ✅ Done | `app/billing.py`; `RC_CLOUD_STRIPE_*` env vars; migration 006; `GET /orgs/{id}/billing` |
| P3 | Self-service onboarding UI | ✅ Done | `dashboard/app/onboarding/` — 5-step wizard; connects to cloud, creates org, issues key |
