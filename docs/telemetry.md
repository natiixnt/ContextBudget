# Telemetry Architecture

## Purpose

Redcon includes a telemetry abstraction so future integrations can route run metrics
to different sinks without changing pipeline logic.

Current behavior remains local-first:
- telemetry is disabled by default (`enabled = false`, sink `noop`)
- nothing is written or sent unless you explicitly enable it
- no hidden data collection exists

## Components

- `TelemetrySink` interface (`redcon.telemetry`)
- `NoOpTelemetrySink` default implementation
- `JsonlFileTelemetrySink` local development sink
- `HttpTelemetrySink` opt-in network sink: POSTs events to a Redcon Cloud
  ingestion endpoint. Only active when you explicitly set `sink = "cloud"`
  (or `"http"`); the endpoint comes from `redcon.toml` or `REDCON_CLOUD_URL`.
  Delivery is best-effort and never interrupts the pipeline.
- `TelemetrySession` run-scoped emitter with shared context fields

## Event Model

Events are structured JSON objects:

```json
{
  "name": "pack_completed",
  "schema_version": "v1",
  "timestamp": "2026-03-12T20:00:00+00:00",
  "run_id": "f6b1...",
  "payload": {
    "command": "pack",
    "repository": {
      "repository_id": "sha256:...",
      "workspace_id": "sha256:..."
    },
    "tokens": {
      "estimated_input_tokens": 1240
    }
  }
}
```

## Emitted Events

The pipeline currently emits:
- `run_started`
- `scan_completed`
- `scoring_completed`
- `pack_completed`
- `benchmark_completed`
- `policy_failed` (on failed policy evaluation)

## Configuration

Telemetry is configured via `redcon.toml`:

```toml
[telemetry]
enabled = true
sink = "file"
file_path = ".redcon/telemetry.jsonl"
```

Defaults:
- `enabled = false`
- `sink = "noop"`
- `file_path = ".redcon/telemetry.jsonl"`

Accepted sink values: `noop` (or `none`), `file` (aliases `jsonl`,
`jsonl_file`) for the local JSONL file, and `cloud` (alias `http`) for the
opt-in network sink described above.

## Trust and Privacy

- No telemetry events are emitted unless users explicitly enable telemetry.
- Nothing ever leaves the machine unless you explicitly select the `cloud`
  sink; the default and `file` sinks are purely local.
- File sink writes only to local filesystem paths under user control.
- Event payloads exclude raw file contents, raw repository paths, and task text.
