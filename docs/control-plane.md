# Control Plane Architecture

This document describes the architecture that enables a future centralized analytics
and governance layer while preserving the current local-first behavior.

## Design Goals

- **Local-first by default.** No network calls are made unless a non-local sink is explicitly
  configured. The default sink is a no-op that discards all events.
- **Pluggable sinks.** The `TelemetrySink` protocol decouples event emission from event
  consumption. A cloud sink can be dropped in without touching pipeline code.
- **Stable, versioned schemas.** Events carry a `schema_version` field. Breaking changes
  introduce a new version tag rather than modifying existing payloads in place.
- **Privacy by construction.** All events use SHA-256 digests instead of raw paths.
  No file contents, task text, or per-file breakdowns are ever included.

---

## Current Architecture

### Layers

```
Pipeline stages
      │  emit(name, **payload)
      ▼
 EventEmitter          ← Protocol (contextbudget.telemetry.EventEmitter)
      │                  satisfied by TelemetrySession
      │  TelemetryEvent(name, schema_version, timestamp, run_id, payload)
      ▼
 TelemetrySink         ← Protocol (contextbudget.telemetry.TelemetrySink)
      │
      ├── NoOpTelemetrySink       (default — drops all events)
      └── JsonlFileTelemetrySink  (local development — appends JSONL to disk)
```

### Interfaces

#### `EventEmitter` (pipeline-facing)

```python
class EventEmitter(Protocol):
    def emit(self, name: str, **payload: Any) -> None: ...
```

Pipeline stages depend on `EventEmitter`, not on the concrete `TelemetrySession`.
This lets tests inject a lightweight double and lets future cloud sessions satisfy the
same interface without changing pipeline code.

`TelemetrySession` is the production implementation. It:

- generates a stable `run_id` (UUID hex) shared across all events in one run
- attaches shared context fields (`command`, `repository`)
- calls `build_analytics_payload()` to produce the versioned, privacy-safe payload
- delegates to the configured `TelemetrySink`

#### `TelemetrySink` (consumption-facing)

```python
class TelemetrySink(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...
```

A cloud sink satisfies this protocol by implementing a single method. The sink receives
a fully-formed `TelemetryEvent` with `name`, `schema_version`, `timestamp`, `run_id`,
and `payload` already serialization-ready.

### Factory

`build_telemetry_sink(*, repo, enabled, sink, file_path)` constructs the active sink
from config values. Unknown sink names fall back to `NoOpTelemetrySink`.

---

## Event Catalog

All events use schema version **`v1`**. Detailed field-level documentation is in
[events.md](events.md).

| Event | Emitted when | Key payload sections |
|-------|-------------|----------------------|
| `run_started` | Start of `plan`, `pack`, or `benchmark` | `tokens.max_tokens`, `files.top_files` |
| `scan_completed` | Repository scan finishes | `files.scanned_files` |
| `scoring_completed` | Relevance scoring finishes | `files.scanned_files`, `files.ranked_files`, `files.top_files` |
| `pack_completed` | Compressed context is rendered | `tokens`, `files`, `cache`, `quality_risk_estimate` |
| `cache_hit` | At least one cache hit occurred during a pack run | `cache.cache_hits`, `cache.tokens_saved`, `cache.backend`, fragment stats |
| `policy_violation` | A policy check is breached | `policy`, `tokens`, `files`, `cache` |
| `delta_applied` | A delta pack is computed against a prior run | `delta.*` |
| `benchmark_completed` | A strategy benchmark finishes | `tokens`, `files`, `benchmark.strategies` |

### Common payload structure

Every event payload shares this stable skeleton:

```json
{
  "command": "pack",
  "repository": {
    "repository_id": "sha256:<hex>",
    "workspace_id":  "sha256:<hex>"
  },
  "tokens": {
    "max_tokens": null,
    "estimated_input_tokens": null,
    "estimated_saved_tokens": null,
    "baseline_full_context_tokens": null
  },
  "files": {
    "scanned_files": null,
    "ranked_files": null,
    "included_files": null,
    "skipped_files": null,
    "top_files": null,
    "strategy_count": null
  },
  "cache": {
    "cache_hits": null,
    "duplicate_reads_prevented": null,
    "tokens_saved": null,
    "backend": null,
    "fragment_hits": null,
    "fragment_misses": null
  },
  "policy": {
    "evaluated": false,
    "passed": null,
    "violation_count": 0,
    "violations": [],
    "failing_checks": [],
    "checks": {}
  },
  "quality_risk_estimate": null,
  "delta": { ... },
  "benchmark": { ... }
}
```

Fields that are not meaningful for a given event carry `null` rather than being omitted.
This keeps the schema uniform and allows consumers to parse all events with the same model.

---

## Privacy Model

Three invariants are enforced in `build_analytics_payload()` and cannot be bypassed
by sink implementations:

1. **No raw paths.** Repository and workspace paths are replaced by deterministic
   SHA-256 digests before the payload reaches the sink.
2. **No file contents.** File bodies, task prompts, and cached summaries are never
   included in any event field.
3. **Aggregated metrics only.** Payloads contain totals and counts; no per-file
   breakdowns or path lists.

These invariants are enforced at schema construction time, not at the sink level.
A cloud sink receives only the already-sanitized `TelemetryEvent` object.

---

## Schema Versioning

- `EVENT_SCHEMA_VERSIONS` in `contextbudget/telemetry/schemas.py` maps every event
  name to its current version string.
- All current events are at **`v1`**.
- **Additive changes** (new nullable fields) do not require a version bump.
- **Breaking changes** (removed fields, renamed fields, changed semantics) must
  introduce a new version tag (e.g. `"v2"`) for the affected event.
- Consumers should branch on `schema_version` so multiple versions can coexist
  during a rollout.

---

## Extending to a Cloud Sink

To route events to a centralized control plane, implement `TelemetrySink` and register
it via `build_telemetry_sink` or by constructing a `TelemetrySession` directly.

### Minimal example

```python
from contextbudget.telemetry import TelemetryEvent

class HttpControlPlaneSink:
    """POST events to a centralized analytics endpoint."""

    def __init__(self, endpoint: str, api_key: str) -> None:
        self._endpoint = endpoint
        self._api_key = api_key

    def emit(self, event: TelemetryEvent) -> None:
        import urllib.request, json
        body = json.dumps(event.to_dict()).encode()
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
```

Wire it into `TelemetrySession`:

```python
from contextbudget.telemetry import TelemetrySession

session = TelemetrySession(
    sink=HttpControlPlaneSink(endpoint="https://...", api_key="..."),
    base_payload={"command": "pack", "repo": repo_path},
)
```

### Governance considerations for cloud sinks

When routing events to a centralized plane, cloud sink implementations should:

- **Send only the pre-sanitized payload.** The `TelemetryEvent.payload` dict contains
  nothing beyond the three-invariant-guaranteed fields listed above.
- **Use async or fire-and-forget emission.** `emit()` is called synchronously on the
  hot path; network latency must not stall the pipeline. Buffer and flush in a
  background thread or use a non-blocking HTTP client.
- **Handle failures gracefully.** Wrap network calls in `try/except` and log or drop on
  error. A telemetry failure must never propagate to the calling pipeline stage.
- **Respect `enabled = false`.** The `build_telemetry_sink` factory short-circuits to
  `NoOpTelemetrySink` before the cloud sink is ever constructed when telemetry is
  disabled. Cloud sinks should not need to check this themselves.

---

## Multi-Tenant Data Model (v1.0-beta)

The cloud service organises tenants into a three-level hierarchy backed by PostgreSQL.

```
Org
 └── Project
      └── Repository  ──  AgentRun (many)
```

### Bootstrap sequence

```bash
# 1. Create the org (unauthenticated — operator endpoint)
curl -s -X POST http://localhost:8080/orgs \
     -H "Content-Type: application/json" \
     -d '{"slug": "acme", "display_name": "Acme Corp"}'
# → {"id": 1, "slug": "acme", ...}

# 2. Issue the first API key (unauthenticated bootstrap)
curl -s -X POST http://localhost:8080/orgs/1/api-keys \
     -H "Content-Type: application/json" \
     -d '{"label": "ci"}'
# → {"raw_key": "cbk_...", "id": 5, ...}   ← save this; it is shown once

# 3. All further management uses the API key
curl -s http://localhost:8080/orgs/1/projects \
     -H "Authorization: Bearer cbk_..."
```

### Repository linking

The `repository_id` on a `Repository` row should match the SHA-256 digest that the ContextBudget runtime includes in telemetry events.  This links control plane records to events in the `events` table.

```python
from contextbudget.telemetry.schemas import build_repository_identifiers
ids = build_repository_identifiers("/path/to/repo")
# Use ids.repository_id when creating the Repository via the API
```

### Agent runs

Run outcome records are written by calling `cp_store.record_agent_run` from application code, or populated from incoming `pack_completed` events via custom integration.  The `task_hash` field stores a SHA-256 digest of the raw task text — the plaintext task is never stored.

---

## Future Extension Points

The following are the minimal hooks needed to support centralized governance without
re-architecting the pipeline:

| Capability | Extension point |
|-----------|----------------|
| Stream events to cloud | New `TelemetrySink` implementation |
| Aggregate across repos or teams | Consume `repository.repository_id` / `workspace_id` as group keys |
| Policy enforcement from control plane | New `AgentAdapter` or policy-check plugin that queries a remote rules endpoint |
| Cross-run anomaly detection | Consume `pack_completed.tokens` and `cache_hit` events; correlate by `run_id` |
| Compliance audit trail | Route `policy_violation` events to an immutable append-only store |
| Real-time dashboards | Subscribe to `run_started` + `pack_completed` pairs keyed on `run_id` |

None of these require changes to the core pipeline. All can be satisfied by adding a
sink implementation and configuring it alongside the existing local sinks.
