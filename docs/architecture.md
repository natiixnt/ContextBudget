# Architecture

ContextBudget is organized around a single engine and explicit stage boundaries. CLI commands, Python API calls, workspace runs, and agent middleware all route through the same scan, score, pack, render, and policy machinery.

## Design Goals

- deterministic heuristics over opaque model decisions
- stable machine-readable artifacts
- local-first operation with no required network services
- explicit extension points for plugins, summarizers, telemetry sinks, and agent adapters
- additive feature growth without breaking single-repo flows

## System Layers

### Entry Points

- `contextbudget/cli.py`: command-line interface
- `contextbudget/engine.py`: public library API
- `contextbudget/agents/`: middleware and adapter abstractions

These layers delegate into the same core pipeline instead of maintaining parallel implementations.

### Core Pipeline

`contextbudget/core/pipeline.py` is the compatibility facade for high-level callers.

`contextbudget/stages/workflow.py` defines the explicit stage boundaries:

- scan refresh
- scan
- workspace scan
- score
- cache
- pack/compression
- render

This keeps orchestration separate from lower-level scanner, scorer, and compressor logic.

### Scanning

`contextbudget/scanners/` is responsible for repository traversal and scan-state reuse.

Key behaviors:

- respects include and ignore rules from config
- maintains `.contextbudget/scan-index.json`
- reuses unchanged file metadata on later runs
- supports workspace scans by iterating `[[repos]]` entries and tagging files with repo labels

### Scoring

`contextbudget/scorers/` ranks `FileRecord` values against a task using deterministic relevance heuristics plus import-graph signals.

Workspace scoring is cross-repository at the ranking layer: all scanned files are scored together. Import-graph resolution stays repo-local so identical relative paths from different repos do not collide.

### Compression

`contextbudget/compressors/` reduces ranked files into packed context.

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

- `contextbudget/cache/`: summary cache backends and duplicate-read support
- `contextbudget/plugins/`: explicit scorer, compressor, and token-estimator extension registry
- `contextbudget/telemetry/`: optional event sink abstraction
- `contextbudget/schemas/`: typed dataclasses and artifact models

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

`contextbudget/agents/middleware.py` adds an agent-facing boundary on top of `ContextBudgetEngine`.

Responsibilities:

- accept a task or typed request
- call engine-backed packing
- derive additive machine-readable metadata
- optionally enforce policy
- optionally record a combined artifact

`contextbudget/agents/adapters.py` defines the adapter abstraction for local integrations. `LocalDemoAgentAdapter` is a simulation of an agent workflow, not a vendor integration.

## Extension Strategy

Extension hooks are intentionally narrow:

- plugin interfaces change scoring, compression, or token estimation
- summarizer adapters extend summary generation
- telemetry sinks extend event handling
- agent adapters extend local integration behavior

This keeps feature additions aligned with the existing pipeline contract instead of scattering logic across the codebase.
