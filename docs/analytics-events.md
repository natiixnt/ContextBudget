# Analytics Events

ContextBudget emits versioned analytics events only when telemetry is explicitly enabled. The OSS implementation stays local-first: the built-in sink writes JSONL to disk and no network collector is included.

## Enable Telemetry

```toml
[telemetry]
enabled = true
sink = "file"
file_path = ".contextbudget/telemetry.jsonl"
```

Defaults:

- `enabled = false`
- `sink = "noop"`
- `file_path = ".contextbudget/telemetry.jsonl"`

## Privacy Model

- raw repository paths are not emitted
- raw file paths are not emitted
- file contents are not emitted
- task text is not emitted
- repository identifiers are deterministic `sha256` digests of local paths

Each payload includes:

- `repository.repository_id`
- `repository.workspace_id`

These identifiers are stable for the same local path and safe to aggregate in local dashboards.

## Event Envelope

Every event uses the same top-level shape:

```json
{
  "name": "pack_completed",
  "schema_version": "v1",
  "timestamp": "2026-03-15T12:00:00+00:00",
  "run_id": "2e8d3f5c4d7c4bb89f60ec0d6ea1a4d1",
  "payload": {
    "command": "pack",
    "repository": {
      "repository_id": "sha256:...",
      "workspace_id": "sha256:..."
    },
    "tokens": {
      "max_tokens": 500,
      "estimated_input_tokens": 182,
      "estimated_saved_tokens": 94,
      "baseline_full_context_tokens": null
    },
    "files": {
      "scanned_files": 12,
      "ranked_files": 12,
      "included_files": 3,
      "skipped_files": 9,
      "top_files": 25,
      "strategy_count": null
    },
    "cache": {
      "cache_hits": 1,
      "duplicate_reads_prevented": 0
    },
    "policy": {
      "evaluated": false,
      "passed": null,
      "violation_count": 0,
      "violations": [],
      "failing_checks": [],
      "checks": {}
    },
    "quality_risk_estimate": "low",
    "benchmark": {
      "scan_runtime_ms": null,
      "strategies": []
    }
  }
}
```

Null values mean the field is part of the stable schema but not meaningful for that event.

## Supported Events

The current schema version for all events is `v1`.

### `run_started`

Used by `plan`, `pack`, and `benchmark` at command start.

Populates:

- `command`
- `tokens.max_tokens` when relevant
- `files.top_files`

### `scan_completed`

Emitted after scanning completes.

Populates:

- `files.scanned_files`

### `scoring_completed`

Emitted after deterministic ranking completes.

Populates:

- `files.scanned_files`
- `files.ranked_files`
- `files.top_files`

### `pack_completed`

Emitted after packed output is rendered.

Populates:

- token budget and estimate fields
- included and skipped file counts
- cache hit and duplicate-read metrics
- `quality_risk_estimate`

### `benchmark_completed`

Emitted after strategy comparison completes.

Populates:

- `tokens.baseline_full_context_tokens`
- scan and ranking counts
- aggregate cache metrics
- `files.strategy_count`
- `benchmark.scan_runtime_ms`
- `benchmark.strategies`

Each strategy summary contains only counts and aggregate metrics:

- `name`
- `estimated_input_tokens`
- `estimated_saved_tokens`
- `included_files`
- `skipped_files`
- `cache_hits`
- `duplicate_reads_prevented`
- `quality_risk_estimate`
- `runtime_ms`

File path lists are intentionally excluded from analytics events.

### `policy_failed`

Emitted only when policy evaluation runs and the policy fails.

Populates:

- token and file counts from the evaluated run
- cache metrics
- `quality_risk_estimate`
- `policy.evaluated`
- `policy.passed`
- `policy.violation_count`
- `policy.violations`
- `policy.failing_checks`
- `policy.checks`

## Stability

- event names are pinned by tests
- payload keys are pinned by tests
- schema changes should add a new version rather than silently changing `v1`
