# CLI Reference

## Commands

### `contextbudget plan <task> --repo <path>`
Rank relevant files for a natural-language task.

### `contextbudget plan <task> --workspace <workspace.toml>`
Rank relevant files across multiple local repositories or monorepo packages.

### `contextbudget pack <task> --repo <path> [--max-tokens N] [--top-files N]`
Build compressed context package and write `run.json` + `run.md` by default.

### `contextbudget pack <task> --workspace <workspace.toml> [--max-tokens N] [--top-files N]`
Build compressed context across a local workspace while recording scanned and selected repos.

### `contextbudget report <run.json> [--out <path>] [--policy <policy.toml>]`
Render summary report from run artifact.

### `contextbudget diff <old-run.json> <new-run.json>`
Compare two run artifacts and emit JSON + Markdown delta outputs.

### `contextbudget benchmark <task> --repo <path>`
Compare deterministic strategies:
- naive full-context
- top-k selection
- compressed pack
- cache-assisted pack

Benchmark artifacts also record the active token-estimator backend and a small estimator comparison
on local sample text from the run.

`benchmark` also accepts `--workspace <workspace.toml>` for multi-repo/local-package runs.

### `contextbudget watch --repo <path> [--poll-interval S] [--once]`
Refresh the incremental scan index and print concise file-change summaries.

Example:

```bash
contextbudget watch --repo .
contextbudget watch --repo . --once
```

Sample output:

```text
Watching repository: /repo
Polling interval: 1.00s
Scan index: /repo/.contextbudget/scan-index.json
Initial scan: repo=/repo tracked=12 included=10 reused=0 added=12 updated=0 removed=0
added[src/auth.py, src/cache.py, docs/notes.md]
Scan change: repo=/repo tracked=12 included=10 reused=11 added=0 updated=1 removed=0
updated[src/auth.py]
```

## Strict Policy Mode

```bash
contextbudget pack "refactor auth middleware" --repo . --strict --policy examples/policy.toml
```

Strict mode returns non-zero on policy violations.

## Config Override

Each command supports `--config <path>` to load a custom `contextbudget.toml`.

## Workspace Config

`--workspace` points to a TOML file with shared config plus one or more `[[repos]]` entries:

```toml
[scan]
include_globs = ["**/*.py", "**/*.ts"]

[[repos]]
label = "auth-service"
path = "../auth-service"

[[repos]]
label = "billing-service"
path = "../billing-service"
ignore_globs = ["**/generated/**"]
```

## Incremental Scan Index

`plan`, `pack`, and `benchmark` automatically maintain `.contextbudget/scan-index.json`.
Unchanged files reuse prior scan metadata; changed and deleted files are refreshed incrementally.
