# Usage Event Schemas

ContextBudget emits structured usage events when telemetry is explicitly enabled. The default
behavior is fully local-first: no network calls are made by any built-in sink.

## Enable Telemetry

```toml
[telemetry]
enabled = true
sink    = "file"
file_path = ".contextbudget/telemetry.jsonl"
```

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Must be `true` to emit any events |
| `sink` | `"noop"` | `"noop"` drops all events; `"file"` / `"jsonl"` writes JSONL to disk |
| `file_path` | `.contextbudget/telemetry.jsonl` | Destination for the `file` sink |

## Privacy Model

All events enforce three invariants:

1. **No raw paths** - repository and workspace paths are replaced by deterministic `sha256`
   digests (`repository.repository_id`, `repository.workspace_id`).
2. **No file contents** - file bodies, task text, and summaries are never included.
3. **Aggregated metrics only** - file counts, token totals, hit/miss ratios; never per-file
   breakdowns or path lists.

## Event Envelope

Every event shares the same top-level shape:

```json
{
  "name": "pack_completed",
  "schema_version": "v1",
  "timestamp": "2026-03-15T12:00:00+00:00",
  "run_id": "2e8d3f5c4d7c4bb89f60ec0d6ea1a4d1",
  "payload": { ... }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Event name (see below) |
| `schema_version` | string | Schema version tag, currently `"v1"` for all events |
| `timestamp` | ISO-8601 string | UTC emission time |
| `run_id` | hex string | Stable identifier for the pipeline run that produced this event |
| `payload` | object | Event-specific data (see per-event sections) |

The payload always contains:

```json
{
  "command": "pack",
  "repository": {
    "repository_id": "sha256:<hex>",
    "workspace_id":  "sha256:<hex>"
  },
  "tokens":    { ... },
  "files":     { ... },
  "cache":     { ... },
  "policy":    { ... },
  "delta":     { ... },
  "benchmark": { ... },
  "quality_risk_estimate": "low" | "medium" | "high" | null
}
```

Fields not meaningful for a given event are present with `null` values to keep the schema stable
across all events.

## Supported Events

All events currently use schema version **`v1`**.

---

### `run_started`

Emitted at the start of every `plan`, `pack`, and `benchmark` invocation.

**Populated fields**

| Path | Description |
|------|-------------|
| `payload.command` | `"plan"`, `"pack"`, or `"benchmark"` |
| `payload.tokens.max_tokens` | Token budget (pack/benchmark only) |
| `payload.files.top_files` | Configured top-N file limit |

**Example**

```json
{
  "name": "run_started",
  "schema_version": "v1",
  "payload": {
    "command": "pack",
    "tokens": { "max_tokens": 30000 },
    "files":  { "top_files": 25 }
  }
}
```

---

### `scan_completed`

Emitted after the repository scan stage finishes.

**Populated fields**

| Path | Description |
|------|-------------|
| `payload.files.scanned_files` | Number of files discovered |

---

### `scoring_completed`

Emitted after relevance scoring and ranking complete.

**Populated fields**

| Path | Description |
|------|-------------|
| `payload.files.scanned_files` | Total files scanned |
| `payload.files.ranked_files` | Files that received a score |
| `payload.files.top_files` | Configured top-N limit |

---

### `pack_completed`

Emitted after compressed context is rendered. This is the primary pack-pipeline summary event.

**Populated fields**

| Path | Description |
|------|-------------|
| `payload.tokens.max_tokens` | Configured budget |
| `payload.tokens.estimated_input_tokens` | Estimated tokens in the packed output |
| `payload.tokens.estimated_saved_tokens` | Tokens saved versus full context |
| `payload.files.scanned_files` | Files scanned |
| `payload.files.ranked_files` | Files ranked |
| `payload.files.included_files` | Files included in output |
| `payload.files.skipped_files` | Files excluded by budget |
| `payload.files.top_files` | Configured top-N limit |
| `payload.cache.cache_hits` | Aggregate cache hits during pack |
| `payload.cache.duplicate_reads_prevented` | Duplicate file reads avoided |
| `payload.quality_risk_estimate` | `"low"` / `"medium"` / `"high"` |

---

### `cache_hit`

Emitted once per `pack` run when at least one cache hit occurred. Contains aggregate cache
statistics for the run - individual hits are not surfaced.

**Populated fields**

| Path | Description |
|------|-------------|
| `payload.cache.cache_hits` | Total summary + fragment hits for this run |
| `payload.cache.tokens_saved` | Estimated prompt tokens saved by cache reuse |
| `payload.cache.backend` | Cache backend name (`"local_file"`, `"memory"`, …) |
| `payload.cache.fragment_hits` | Fragment-level cache hits |
| `payload.cache.fragment_misses` | Fragment-level cache misses |

**Example**

```json
{
  "name": "cache_hit",
  "schema_version": "v1",
  "payload": {
    "command": "pack",
    "cache": {
      "cache_hits": 4,
      "tokens_saved": 1280,
      "backend": "local_file",
      "fragment_hits": 2,
      "fragment_misses": 1
    }
  }
}
```

---

### `delta_applied`

Emitted when a delta pack is computed against a previous run artifact (`delta_from` is set).
Contains only counts - no file paths or content.

**Populated fields**

| Path | Description |
|------|-------------|
| `payload.delta.files_added` | Files present in current run but not previous |
| `payload.delta.files_removed` | Files present in previous run but not current |
| `payload.delta.files_changed` | Files present in both runs with content changes |
| `payload.delta.delta_tokens` | Total tokens in the delta package |
| `payload.delta.tokens_saved` | Tokens saved versus a full repack |
| `payload.delta.has_previous_run` | `true` when a previous run reference was resolved |
| `payload.delta.slices_changed` | Number of code slices that changed |
| `payload.delta.symbols_changed` | Number of symbols that changed |

**Example**

```json
{
  "name": "delta_applied",
  "schema_version": "v1",
  "payload": {
    "command": "pack",
    "delta": {
      "files_added": 1,
      "files_removed": 0,
      "files_changed": 3,
      "delta_tokens": 840,
      "tokens_saved": 6200,
      "has_previous_run": true,
      "slices_changed": 5,
      "symbols_changed": 2
    }
  }
}
```

---

### `policy_violation`

Emitted when a policy evaluation runs and at least one threshold is breached. This is the
canonical policy failure event going forward. `policy_failed` is emitted alongside it for
backwards compatibility and carries identical payload.

**Populated fields**

| Path | Description |
|------|-------------|
| `payload.tokens.max_tokens` | Budget from the evaluated run |
| `payload.tokens.estimated_input_tokens` | Tokens in the evaluated output |
| `payload.tokens.estimated_saved_tokens` | Tokens saved in the evaluated output |
| `payload.files.included_files` | Files included in the evaluated output |
| `payload.files.skipped_files` | Files skipped in the evaluated output |
| `payload.cache.cache_hits` | Cache hits during the evaluated run |
| `payload.cache.duplicate_reads_prevented` | Duplicate reads prevented |
| `payload.quality_risk_estimate` | `"low"` / `"medium"` / `"high"` |
| `payload.policy.evaluated` | Always `true` |
| `payload.policy.passed` | Always `false` |
| `payload.policy.violation_count` | Number of violated checks |
| `payload.policy.violations` | Human-readable violation messages |
| `payload.policy.failing_checks` | Names of checks that failed |
| `payload.policy.checks` | Full check map with thresholds and actuals |

**Example**

```json
{
  "name": "policy_violation",
  "schema_version": "v1",
  "payload": {
    "command": "pack",
    "policy": {
      "evaluated": true,
      "passed": false,
      "violation_count": 1,
      "violations": ["estimated_input_tokens 42000 exceeds max 30000"],
      "failing_checks": ["max_estimated_input_tokens"],
      "checks": {
        "max_estimated_input_tokens": {
          "passed": false,
          "threshold": 30000,
          "actual": 42000
        }
      }
    }
  }
}
```

---

### `benchmark_completed`

Emitted after a strategy benchmark run. Contains aggregate metrics across all strategies - no
per-file breakdown.

**Populated fields**

| Path | Description |
|------|-------------|
| `payload.tokens.baseline_full_context_tokens` | Uncompressed baseline token count |
| `payload.tokens.estimated_input_tokens` | Minimum across strategies |
| `payload.tokens.estimated_saved_tokens` | Maximum across strategies |
| `payload.files.scanned_files` | Files scanned |
| `payload.files.ranked_files` | Files ranked |
| `payload.files.top_files` | Configured top-N |
| `payload.files.strategy_count` | Number of strategies compared |
| `payload.cache.cache_hits` | Maximum cache hits across strategies |
| `payload.cache.duplicate_reads_prevented` | Maximum duplicates prevented |
| `payload.benchmark.scan_runtime_ms` | Wall-clock time for the scan stage |
| `payload.benchmark.strategies` | Array of per-strategy summaries (see below) |

Each strategy summary in `benchmark.strategies`:

| Field | Description |
|-------|-------------|
| `name` | Strategy identifier |
| `estimated_input_tokens` | Tokens in strategy output |
| `estimated_saved_tokens` | Tokens saved |
| `included_files` | Files included |
| `skipped_files` | Files skipped |
| `cache_hits` | Cache hits for this strategy |
| `duplicate_reads_prevented` | Duplicate reads prevented |
| `quality_risk_estimate` | Risk level |
| `runtime_ms` | Strategy wall-clock time |

---

## Schema Versioning

The `schema_version` field on every event envelope enables consumers to handle multiple
versions concurrently:

- All current events use **`v1`**.
- Additive changes (new nullable fields) do not require a version bump.
- Breaking changes (removed or renamed fields, changed semantics) must introduce a new version
  tag (e.g., `"v2"`) rather than modifying `v1` in place.
- `EVENT_SCHEMA_VERSIONS` in `contextbudget/telemetry/schemas.py` is the authoritative map
  from event name to version string.

## Stability Contract

- Event names are pinned by the test suite.
- Payload keys are pinned by the test suite.
- Adding new events or new nullable fields to an existing event is a non-breaking change.
- Removing an event or a field, or changing the type/semantics of a field, requires a
  schema version increment and a deprecation notice in this document.
