# Migration Notes

Recent releases added workspace support, plugin selection, token-estimator controls, telemetry, and agent middleware. The migration path is additive: existing single-repo CLI flows continue to work.

## CLI And Config

No existing `--repo` workflow needs to change.

New optional entry points:

- `--workspace <workspace.toml>` for `plan`, `pack`, and `benchmark`
- `--config <path>` for loading an alternate config file

New config areas that may appear in `redcon.toml` or workspace TOML:

- `[summarization]`
- `[tokens]`
- `[plugins]`
- `[telemetry]`

Workspace files also support `[[repos]]` entries.

Legacy config sections such as `[pack]` and `[output]` are still read for compatibility, but new configuration should use `[budget]` and `[compression]`.

## Artifact Consumers

If you parse `run.json`, expect additive fields rather than shape replacement.

New fields can include:

- `cache`
- `summarizer`
- `token_estimator`
- `implementations`
- `workspace`
- `scanned_repos`
- `selected_repos`
- `agent_middleware`

Consumers should ignore unknown keys rather than assuming a closed schema.

## Python API

Existing engine calls remain valid:

```python
from redcon import RedconEngine

engine = RedconEngine()
run = engine.pack(task="refactor auth middleware", repo=".")
```

New additive API surfaces:

```python
from redcon import (
    AgentTaskRequest,
    RedconMiddleware,
    LocalDemoAgentAdapter,
    prepare_context,
)

result = prepare_context("update auth flow", workspace="workspace.toml")
```

New public exports include:

- `RedconMiddleware`
- `AgentTaskRequest`
- `AgentMiddlewareResult`
- `prepare_context(...)`
- `enforce_budget(...)`
- `record_run(...)`
- `AgentAdapter`
- `LocalDemoAgentAdapter`

## Recommended Upgrade Pattern

- keep existing single-repo automation unchanged
- adopt workspace TOML only for cross-repo tasks
- adopt middleware only when integrating Redcon into an agent loop
- treat new artifact fields as additive metadata, not a breaking change
