# Workspace

Workspace support lets one task span multiple local repositories or monorepo packages while keeping the existing Redcon scan, score, and pack flow.

The first version is intentionally simple:

- local-only
- no remote checkout or repository fetching
- additive to existing single-repo flows

## When To Use It

Use a workspace when a change crosses repository or package boundaries, for example:

- two backend services that share an auth flow
- an application plus a shared library
- a monorepo package that must be scored separately from the root

## Workspace File Format

Workspace configuration is TOML. It combines shared config with one or more `[[repos]]` entries.

```toml
name = "backend-services"

[scan]
include_globs = ["**/*.py", "**/*.ts"]
ignore_globs = ["**/generated/**"]

[budget]
max_tokens = 28000
top_files = 24

[score]
critical_path_keywords = ["auth", "session", "permissions"]

[[repos]]
label = "auth-service"
path = "../auth-service"

[[repos]]
label = "billing-service"
path = "../billing-service"
ignore_globs = ["tests/fixtures/**"]
```

Shared config sections use the same schema as `redcon.toml`, including:

- `[scan]`
- `[budget]`
- `[score]`
- `[compression]`
- `[summarization]`
- `[tokens]`
- `[plugins]`
- `[cache]`
- `[telemetry]`

## Repo Rules

- `path` is resolved relative to the workspace TOML file
- `label` must be unique
- if `label` is omitted, the repo directory name is used
- repo `include_globs` replace shared `scan.include_globs` for that repo
- repo `ignore_globs` are added on top of shared `scan.ignore_globs`

If a repo path does not exist, workspace loading fails early.

## CLI Usage

Plan across a workspace:

```bash
redcon plan "update auth flow across services" --workspace workspace.toml
```

Pack across a workspace:

```bash
redcon pack "update auth flow across services" --workspace workspace.toml
```

Benchmark across a workspace:

```bash
redcon benchmark "update auth flow across services" --workspace workspace.toml
```

Single-repo commands still work exactly as before:

```bash
redcon pack "update auth flow" --repo .
```

## Python API Usage

```python
from redcon import RedconEngine

engine = RedconEngine()
plan = engine.plan(task="update auth flow across services", workspace="workspace.toml")
run = engine.pack(task="update auth flow across services", workspace="workspace.toml", max_tokens=28000)
```

Agent middleware uses the same workspace support:

```python
from redcon import prepare_context

result = prepare_context("update auth flow across services", workspace="workspace.toml")
```

## Artifact Provenance

Workspace artifacts add repo provenance so machine consumers can inspect where the context came from:

```json
{
  "workspace": "/path/to/workspace.toml",
  "scanned_repos": [
    {"label": "auth-service", "path": "/path/to/auth-service", "scanned_files": 18},
    {"label": "billing-service", "path": "/path/to/billing-service", "scanned_files": 12}
  ],
  "selected_repos": ["auth-service", "billing-service"],
  "files_included": ["auth-service:src/auth.py", "billing-service:src/auth.py"]
}
```

`scanned_repos` answers "what was searched." `selected_repos` answers "what contributed packed context."

## Scoring Behavior

Relevance scoring works across repository boundaries because all scanned files are ranked together for the task.

Import-graph signals remain repo-local. This is intentional:

- it keeps duplicate relative paths from different repos separate
- it avoids inventing cross-repo import relationships that are not explicit in the local code

## Included Examples

- [`examples/workspaces/two-service-backend.toml`](../examples/workspaces/two-service-backend.toml)
- [`examples/workspaces/app-shared-library.toml`](../examples/workspaces/app-shared-library.toml)
