# ContextBudget

ContextBudget selects, compresses, and budgets repository context for coding-agent workflows. It is deterministic, local-first, and built to produce machine-readable artifacts that can be reused in CI, local tooling, and agent middleware.

## What It Does

- ranks repository files against a natural-language task
- plans step-by-step context usage across multi-step agent workflows
- packs relevant context under an explicit token budget
- records stable `run.json` and `run.md` artifacts
- aggregates historical `run.json` artifacts into file and directory heatmaps
- reuses cached summaries and an incremental scan index
- supports local multi-repo and monorepo-package workspaces
- exposes an adapter-ready middleware layer for external agent tools

## Quickstart

```bash
# Install
python3 -m pip install -e .[dev]

# Optional exact local tokenizer backend
python3 -m pip install -e .[tokenizers]

# Rank likely-relevant files
contextbudget plan "add caching to search API" --repo .

# Plan context across a multi-step agent workflow
contextbudget plan-agent "refactor auth middleware" --repo .

# Pack context for one repository
contextbudget pack "refactor auth middleware" --repo . --max-tokens 30000

# Pack context across multiple local repositories or packages
contextbudget pack "update auth flow across services" --workspace workspace.toml

# Summarize an existing run artifact
contextbudget report run.json

# Compare two runs
contextbudget diff old-run.json new-run.json

# Audit a pull request for context growth
contextbudget pr-audit --repo . --base origin/main --head HEAD

# Compare packing strategies
contextbudget benchmark "add rate limiting to auth API" --repo .

# Aggregate historical token hotspots
contextbudget heatmap .

# Refresh scan state once without entering watch mode
contextbudget watch --repo . --once
```

## Workspaces

Workspace files let one task span multiple local repositories or monorepo packages while keeping the same scan, score, and pack pipeline.

```toml
name = "backend-services"

[scan]
include_globs = ["**/*.py", "**/*.ts"]

[budget]
max_tokens = 28000
top_files = 24

[[repos]]
label = "auth-service"
path = "../auth-service"

[[repos]]
label = "billing-service"
path = "../billing-service"
ignore_globs = ["tests/fixtures/**"]
```

Workspace artifacts add provenance fields without changing single-repo flows:

- `workspace`
- `scanned_repos`
- `selected_repos`
- repo-qualified file paths such as `auth-service:src/auth.py`

See [docs/workspace.md](docs/workspace.md) and the examples in [`examples/workspaces/`](examples/workspaces/).

## Agent Middleware

The middleware layer sits on top of `ContextBudgetEngine`; it does not duplicate packing logic. It prepares context, optionally enforces policy, and records additive metadata for agent loops.

```python
from contextbudget import ContextBudgetEngine, enforce_budget, prepare_context, record_run

result = prepare_context(
    "update auth flow across services",
    workspace="workspace.toml",
    max_tokens=28000,
    metadata={"agent": "local-demo"},
)

policy = ContextBudgetEngine.make_policy(
    max_estimated_input_tokens=28000,
    max_quality_risk_level="medium",
)

checked = enforce_budget(result, policy=policy)
record_run(checked, "agent-run.json")
```

`LocalDemoAgentAdapter` is included as a local simulation of how an external tool can call the middleware without introducing any vendor API dependency.

## Extension Points

ContextBudget stays deterministic by default but exposes explicit hooks for local extensions:

- scorer plugins
- compressor plugins
- token-estimator plugins
- summarizer adapters
- telemetry sinks
- agent adapters

Artifacts record active implementations under `implementations`, along with additive cache, summarizer, token-estimator, workspace, and middleware metadata when those features are active.

## Migration Notes

Recent additions are additive rather than disruptive:

- existing single-repo CLI flows stay unchanged
- multi-repo analysis is opt-in through `--workspace <workspace.toml>` or `workspace=...`
- workspace TOML files can carry shared config plus `[[repos]]` entries
- the public Python API now exports `ContextBudgetMiddleware`, `AgentTaskRequest`, `prepare_context(...)`, `enforce_budget(...)`, `record_run(...)`, and `LocalDemoAgentAdapter`
- machine-readable artifacts can now include `workspace`, `scanned_repos`, `selected_repos`, `implementations`, `token_estimator`, `summarizer`, and `agent_middleware`

Detailed upgrade notes: [docs/migration.md](docs/migration.md).

## Documentation

- [Getting Started](docs/getting-started.md)
- [CLI Reference](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Workspace](docs/workspace.md)
- [Python API](docs/python-api.md)
- [Agent Integration](docs/agent-integration.md)
- [Plugins](docs/plugins.md)
- [Architecture](docs/architecture.md)
- [Migration Notes](docs/migration.md)

Examples and sample outputs: [examples/README.md](examples/README.md).
