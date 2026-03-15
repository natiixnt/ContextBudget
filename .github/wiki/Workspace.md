# Workspace

Workspace mode lets you scan, rank, and pack context across multiple local repositories or monorepo packages in a single run.

---

## Workspace TOML

Create a `workspace.toml` file:

```toml
name = "backend-services"

[scan]
include_globs = ["**/*.py", "**/*.ts"]
ignore_globs = ["**/generated/**"]

[budget]
max_tokens = 28000
top_files = 30

[[repos]]
label = "auth-service"
path = "../auth-service"

[[repos]]
label = "billing-service"
path = "../billing-service"
ignore_globs = ["tests/fixtures/**"]

[[repos]]
label = "gateway"
path = "../platform/packages/gateway"
include_globs = ["src/**/*.ts"]
```

**Rules:**
- `path` is resolved relative to the workspace TOML file
- `label` must be unique; it namespaces artifact paths like `auth-service:src/auth.py`
- Repo-specific `include_globs` replace shared `scan.include_globs` for that repo
- Repo-specific `ignore_globs` are added on top of shared `scan.ignore_globs`

---

## CLI Usage

All main commands accept `--workspace` in place of `--repo`:

```bash
# Rank files across all repos
redcon plan "add caching" --workspace workspace.toml

# Plan multi-step workflow across repos
redcon plan-agent "add caching" --workspace workspace.toml

# Pack context across all repos
redcon pack "add caching" --workspace workspace.toml --max-tokens 28000

# Compare strategies across repos
redcon benchmark "add auth" --workspace workspace.toml

# Full middleware pipeline
redcon prepare-context "add caching" --workspace workspace.toml
```

---

## Python API

```python
from redcon import RedconEngine

engine = RedconEngine()

# Pack across workspace
run = engine.pack(
    task="update auth flow",
    workspace="workspace.toml",
    max_tokens=24000,
)

# Plan agent workflow across workspace
plan = engine.plan_agent(
    task="update auth flow across services",
    workspace="workspace.toml",
    top_files=4,
)
```

With `BudgetGuard`:

```python
from redcon import BudgetGuard

guard = BudgetGuard(max_tokens=28000)
result = guard.pack_context(task="add caching", workspace="workspace.toml")

print(result["workspace"])           # workspace name
print(result["scanned_repos"])       # repos that were scanned
print(result["selected_repos"])      # repos that contributed selected files
```

With middleware helpers:

```python
from redcon import prepare_context

result = prepare_context(
    "update auth flow across services",
    workspace="workspace.toml",
    max_tokens=28000,
)
print(result.metadata["selected_repos"])
```

---

## Workspace Artifacts

Pack artifacts from workspace runs include additional fields:

| Field | Description |
|-------|-------------|
| `workspace` | Workspace name |
| `scanned_repos` | All repos that were scanned with file counts |
| `selected_repos` | Repos that contributed at least one selected file |

File paths in `ranked_files`, `files_included`, and `compressed_context` are prefixed with the repo label:

```json
{
  "files_included": [
    "auth-service:src/auth.py",
    "billing-service:src/payment.py"
  ]
}
```

---

## Workspace Architecture

- `load_workspace(...)` parses the workspace TOML and resolves all repo paths
- The workspace scan stage iterates `[[repos]]` entries and tags each file with its repo label
- Scoring is cross-repository: all files are ranked together
- Import-graph resolution stays repo-local to avoid collisions between repos with identical relative paths
- Per-repo scan summaries are available for provenance tracking
