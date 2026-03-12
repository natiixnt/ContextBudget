# Telemetry Architecture

## Purpose

ContextBudget includes a telemetry abstraction so future integrations can route run metrics
to different sinks without changing pipeline logic.

Current behavior remains local-first:
- telemetry is disabled by default
- no network sinks are implemented
- no hidden data collection exists

## Components

- `TelemetrySink` interface (`contextbudget.telemetry`)
- `NoOpTelemetrySink` default implementation
- `JsonlFileTelemetrySink` local development sink
- `TelemetrySession` run-scoped emitter with shared context fields

## Event Model

Events are structured JSON objects:

```json
{
  "name": "pack_completed",
  "timestamp": "2026-03-12T20:00:00+00:00",
  "run_id": "f6b1...",
  "payload": {
    "command": "pack",
    "task": "refactor auth middleware",
    "repo": "/path/to/repo",
    "estimated_input_tokens": 1240
  }
}
```

## Emitted Events

The pipeline currently emits:
- `run_started`
- `scan_completed`
- `scoring_completed`
- `pack_completed`
- `policy_failed` (on failed policy evaluation)

## Configuration

Telemetry is configured via `contextbudget.toml`:

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

## Trust and Privacy

- No telemetry events are emitted unless users explicitly enable telemetry.
- No HTTP clients or hosted collectors are included.
- File sink writes only to local filesystem paths under user control.
