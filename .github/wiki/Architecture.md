# Architecture

ContextBudget is organized around a single engine and explicit stage boundaries. CLI commands, Python API calls, workspace runs, and agent middleware all route through the same scan, score, pack, render, and policy machinery.

---

## Design Goals

- **Deterministic heuristics** over opaque model decisions
- **Stable machine-readable artifacts** — `run.json` with additive metadata blocks
- **Local-first** operation with no required network services
- **Explicit extension points** for plugins, summarizers, telemetry sinks, and agent adapters
- **Additive feature growth** without breaking single-repo flows

---

## System Layers

```
┌─────────────────────────────────────────────────────────┐
│                      Entry Points                        │
│  contextbudget/cli.py  │  engine.py  │  agents/          │
└────────────────────────┬────────────────────────────────┘
                         │ all delegate to
┌────────────────────────▼────────────────────────────────┐
│                    Core Pipeline                          │
│  core/pipeline.py  (compat facade)                       │
│  stages/workflow.py (explicit stage boundaries)          │
│                                                          │
│  1. Scan Refresh   │  4. Cache                          │
│  2. Scan           │  5. Pack / Compression             │
│  3. Score          │  6. Render                         │
└──────────┬──────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────┐
│                    Supporting Layers                      │
│  scanners/   scorers/   compressors/                     │
│  cache/      plugins/   telemetry/   schemas/            │
└─────────────────────────────────────────────────────────┘
```

---

## Entry Points

| Module | Role |
|--------|------|
| `contextbudget/cli.py` | Command-line interface |
| `contextbudget/engine.py` | Public library API (`ContextBudgetEngine`, `BudgetGuard`) |
| `contextbudget/agents/` | Middleware and adapter abstractions |

These layers delegate into the same core pipeline rather than maintaining parallel implementations.

---

## Core Pipeline

### Pipeline Orchestration

`contextbudget/core/pipeline.py` is the compatibility facade for high-level callers.

`contextbudget/stages/workflow.py` defines the explicit stage boundaries:

1. **Scan Refresh** — Update incremental scan index
2. **Scan** — Repository file traversal with metadata
3. **Workspace Scan** — Multi-repo scanning with repo labels
4. **Score** — Deterministic file relevance ranking
5. **Cache** — Summary cache reuse and duplicate tracking
6. **Pack / Compression** — Reduce ranked files into context
7. **Render** — Output JSON and Markdown artifacts

This keeps orchestration separate from lower-level scanner, scorer, and compressor logic.

---

## Scanning

`contextbudget/scanners/` handles repository traversal and scan-state reuse.

- Respects include and ignore rules from config
- Maintains `.contextbudget/scan-index.json` for incremental reuse
- Reuses unchanged file metadata on later runs
- Supports workspace scans by iterating `[[repos]]` entries and tagging files with repo labels

| Module | Role |
|--------|------|
| `scanners/repository.py` | Core file scanning |
| `scanners/incremental.py` | Manages `.contextbudget/scan-index.json` |
| `scanners/workspace.py` | Multi-repo scanning with repo labels |
| `scanners/git_diff.py` | Git diff scanning for PR audits |

---

## Scoring

`contextbudget/scorers/` ranks `FileRecord` values against a task using deterministic relevance heuristics plus import-graph signals.

Workspace scoring is cross-repository at the ranking layer: all scanned files are scored together. Import-graph resolution stays repo-local so identical relative paths from different repos do not collide.

| Module | Role |
|--------|------|
| `scorers/relevance.py` | Deterministic scoring: keyword weights, extension bonuses, test penalties |
| `scorers/import_graph.py` | Builds call graph, scores files by import relationships |
| `scorers/history.py` | Boosts files from similar historical tasks; penalizes ignored files |

---

## Compression

`contextbudget/compressors/` reduces ranked files into packed context.

Built-in strategies:
- Full-file inclusion
- Snippet extraction (keyword-window slices)
- Symbol extraction (classes, functions, types)
- Language-aware import/dependency slicing
- Deterministic summaries
- External summarizer adapter

Compression also owns:
- Summary-cache usage
- Duplicate-read tracking and deduplication
- Quality-risk estimation (`"low"`, `"medium"`, `"high"`)

---

## Shared Services

| Module | Role |
|--------|------|
| `cache/` | Summary cache backends (`local_file`, `shared_stub`, `memory`) |
| `plugins/` | Explicit scorer, compressor, and token-estimator extension registry |
| `telemetry/` | Optional event sink abstraction |
| `schemas/` | Typed dataclasses: `FileRecord`, `RankedFile`, etc. |

---

## Artifact Model

The primary machine-readable artifact is `run.json`. It keeps a stable core shape and adds metadata blocks when features are active.

**Core fields:**
```json
{
  "command": "pack",
  "task": "...",
  "repo": "...",
  "max_tokens": 30000,
  "ranked_files": [...],
  "files_included": [...],
  "files_skipped": [...],
  "compressed_context": [...],
  "budget": {...},
  "generated_at": "..."
}
```

**Additive blocks (when active):**

| Block | When added |
|-------|------------|
| `cache` | Always |
| `summarizer` | Always |
| `token_estimator` | Always |
| `implementations` | Always (records active plugins) |
| `workspace` | Workspace runs |
| `scanned_repos` | Workspace runs |
| `selected_repos` | Workspace runs |
| `agent_middleware` | `prepare-context` and middleware API |
| `delta` | Incremental delta runs |
| `profile` | `profile_run()` API |
| `model_profile` | When `model_profile` is configured |

**Compatibility rule:** New features should add fields rather than replacing existing ones.

---

## Workspace Architecture

Workspace support is local-only.

`load_workspace(...)` parses one TOML file combining:
- Shared config sections (`[scan]`, `[budget]`, etc.)
- One or more `[[repos]]` entries

The workspace scan stage:
- Resolves repo paths relative to the workspace TOML
- Applies repo labels
- Applies repo-specific include and ignore rules
- Returns both scanned file records and per-repo scan summaries

Rendered plan and pack artifacts preserve repo provenance so downstream tools can tell which repos contributed selected files.

---

## Agent Middleware Architecture

`contextbudget/agents/middleware.py` adds an agent-facing boundary on top of `ContextBudgetEngine`.

**Responsibilities:**
1. Accept a task or typed request
2. Call engine-backed packing
3. Derive additive machine-readable metadata
4. Optionally enforce policy
5. Optionally record a combined artifact

`contextbudget/agents/adapters.py` defines the adapter abstraction for local integrations. `LocalDemoAgentAdapter` is a local simulation, not a vendor integration.

---

## Extension Strategy

Extension hooks are intentionally narrow:

| Extension | What it changes |
|-----------|-----------------|
| `ScorerPlugin` | File relevance scoring |
| `CompressorPlugin` | Context compression strategy |
| `TokenEstimatorPlugin` | Token counting |
| Summarizer adapters | Summary generation |
| Telemetry sinks | Event handling |
| Agent adapters | Local integration behavior |

This keeps feature additions aligned with the existing pipeline contract.
