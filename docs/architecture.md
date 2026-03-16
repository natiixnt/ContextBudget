# Architecture

Redcon is organized around a single engine and explicit stage boundaries. CLI commands, Python API calls, workspace runs, agent middleware, and the runtime gateway all route through the same scan, score, pack, render, and policy machinery.

## Design Goals

- deterministic heuristics over opaque model decisions
- stable machine-readable artifacts
- local-first operation with no required network services
- explicit extension points for plugins, summarizers, telemetry sinks, and agent adapters
- additive feature growth without breaking single-repo flows

## System Layers

```
┌─────────────────────────────────────────────────────┐
│                 ENTRY POINTS                        │
│  CLI (cli.py)  │  SDK (engine.py)  │  Gateway API  │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│          CONTEXT OPTIMIZATION ENGINE                │
│                                                     │
│  core/pipeline.py  ──▶  stages/workflow.py          │
│                                                     │
│  Scan ▶ Score ▶ Cache ▶ Compress ▶ Render ▶ Policy │
└──────────────┬────────────────────┬─────────────────┘
               │                    │
               ▼                    ▼
┌─────────────────────┐  ┌──────────────────────────┐
│   CACHE LAYER       │  │   AGENT RUNTIME LAYER    │
│                     │  │                          │
│  LocalFile / Redis  │  │  AgentRuntime            │
│  SQLite history     │  │  AgentMiddleware         │
│  Org-namespaced TTL │  │  LLM adapters            │
└─────────────────────┘  └──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│              RUNTIME GATEWAY (v1.0-alpha)           │
│                                                     │
│  FastAPI + Uvicorn  (falls back to stdlib HTTP)     │
│  /prepare-context  /run-agent-step  /report-run     │
│  Auth: Bearer API key  │  /health  │  /metrics      │
└─────────────────────────────────────────────────────┘
```

### Entry Points

- `redcon/cli.py`: command-line interface (includes `init`, `pack`, `plan`, `gateway`, …)
- `redcon/engine.py`: public library API (`RedconEngine`)
- `redcon/agents/`: middleware and adapter abstractions
- `redcon/gateway/`: HTTP gateway service for agent frameworks

These layers delegate into the same core pipeline instead of maintaining parallel implementations.

### Core Pipeline

`redcon/core/pipeline.py` is the high-level orchestrator. It re-exports `as_json_dict` and the `run_*` functions used by the engine and CLI.

`redcon/stages/workflow.py` is the **canonical stage implementation** - this is the single source of truth for:

- `run_scan_stage` / `run_scan_refresh_stage` / `run_scan_workspace_stage`
- `run_score_stage`
- `run_cache_stage`
- `run_pack_stage`
- `run_render_stage`
- `build_plan_result` / `build_agent_plan_result`
- `as_json_dict`

`core/pipeline.py` imports these directly from `stages.workflow` and does **not** re-implement them. All new callers should import stage functions from `redcon.stages.workflow`.

### Scanning

`redcon/scanners/` is responsible for repository traversal and scan-state reuse.

Key behaviors:

- respects include and ignore rules from config
- maintains `.redcon/scan-index.json`
- reuses unchanged file metadata on later runs
- supports workspace scans by iterating `[[repos]]` entries and tagging files with repo labels

### Scoring

`redcon/scorers/` ranks `FileRecord` values against a task using deterministic relevance heuristics plus import-graph signals.

Workspace scoring is cross-repository at the ranking layer: all scanned files are scored together. Import-graph resolution stays repo-local so identical relative paths from different repos do not collide.

### Compression

`redcon/compressors/` reduces ranked files into packed context.

Built-in strategies include:

- full-file inclusion
- snippet extraction
- deterministic summaries

Compression also owns:

- language-aware chunk selection
- summary-cache usage
- duplicate-read tracking
- quality-risk estimation

### Shared Services

- `redcon/cache/`: summary cache backends and duplicate-read support
- `redcon/plugins/`: explicit scorer, compressor, and token-estimator extension registry
- `redcon/telemetry/`: optional event sink abstraction
- `redcon/schemas/`: typed dataclasses and artifact models

## Artifact Model

The primary machine-readable artifact is `run.json`. It keeps a stable core shape and adds metadata blocks when features are active.

Common additive fields include:

- `cache`
- `summarizer`
- `token_estimator`
- `implementations`
- `workspace`
- `scanned_repos`
- `selected_repos`
- `agent_middleware`

The compatibility rule is simple: new features should add fields rather than replacing existing ones.

## Workspace Architecture

Workspace support is local-only in the current release.

`load_workspace(...)` parses one TOML file that combines:

- shared config sections such as `[scan]` and `[budget]`
- one or more `[[repos]]` entries

The workspace scan stage:

- resolves repo paths relative to the workspace TOML
- applies repo labels
- applies repo-specific include and ignore rules
- returns both scanned file records and per-repo scan summaries

Rendered plan and pack artifacts preserve repo provenance so downstream tools can tell which repos were scanned and which repos actually contributed selected files.

## Agent Middleware Architecture

`redcon/agents/middleware.py` adds an agent-facing boundary on top of `RedconEngine`.

Responsibilities:

- accept a task or typed request
- call engine-backed packing
- derive additive machine-readable metadata
- optionally enforce policy
- optionally record a combined artifact

`redcon/agents/adapters.py` defines the adapter abstraction for local integrations. `LocalDemoAgentAdapter` is a simulation of an agent workflow, not a vendor integration.

## Extension Strategy

Extension hooks are intentionally narrow:

- plugin interfaces change scoring, compression, or token estimation
- summarizer adapters extend summary generation
- telemetry sinks extend event handling
- agent adapters extend local integration behavior

This keeps feature additions aligned with the existing pipeline contract instead of scattering logic across the codebase.
