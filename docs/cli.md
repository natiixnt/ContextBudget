# CLI Reference

## Commands

### `contextbudget plan <task> --repo <path>`
Rank relevant files for a natural-language task.

### `contextbudget plan <task> --workspace <workspace.toml>`
Rank relevant files across multiple local repositories or monorepo packages.

### `contextbudget plan-agent <task> --repo <path>`
Plan context usage across a multi-step agent workflow. The artifact includes step order,
context assigned per step, token estimates per step, shared context, and total token estimates.

### `contextbudget plan-agent <task> --workspace <workspace.toml>`
Plan the same lifecycle-aware workflow across multiple local repositories or monorepo packages.

### `contextbudget pack <task> --repo <path> [--max-tokens N] [--top-files N]`
Build compressed context package and write `run.json` + `run.md` by default.

### `contextbudget pack <task> --repo <path> --delta <previous-run.json>`
Build the normal current pack artifact plus a `delta` block that contains only the
changes relative to the previous run. The delta package records:
- added files
- removed files
- changed files and slices
- changed symbols
- original tokens, delta tokens, and tokens saved

### `contextbudget pack <task> --workspace <workspace.toml> [--max-tokens N] [--top-files N]`
Build compressed context across a local workspace while recording scanned and selected repos.

### `contextbudget profile <run.json> [--out-prefix <prefix>]`

Explain where token savings came from in a pack run.  Reads a `run.json` artifact
produced by `pack` and emits a `<prefix>.json` + `<prefix>.md` breakdown.

The profile shows:

- **tokens before optimization** — raw token count across all packed files
- **tokens after optimization** — token count actually sent to the model
- **savings per stage** — how much each optimization stage contributed
- **total savings** — absolute tokens removed and percentage reduction

**Stages tracked:**

| Stage | What it captures |
|-------|-----------------|
| `cache_reuse` | Files whose summaries were reused from the summary cache |
| `symbol_extraction` | Files reduced to named symbols (classes, functions, types) |
| `slicing` | Files reduced via language-aware import/dependency slicing |
| `compression` | Files replaced by deterministic or external summaries |
| `snippet` | Files reduced to keyword-window snippets |
| `delta` | Savings from an incremental delta pack (skipped context carried over) |
| `full` | Files included without reduction |

**Example:**

```bash
contextbudget pack "add caching" --repo . --max-tokens 20000
contextbudget profile run.json
```

**Sample output (`run-profile.md`):**

```markdown
# ContextBudget Token Savings Profile

## Summary

| Metric | Tokens |
|--------|--------|
| Tokens before optimization | 14200 |
| Tokens after optimization  |  8900 |
| Total tokens saved         |  5300 |
| Savings                    |  37.3% |

## Savings by Stage

| Stage            | Files | Tokens Saved | % of Total Savings |
|------------------|-------|-------------|---------------------|
| Symbol Extraction|     4 |        3100 |              58.5% |
| Compression      |     2 |        1800 |              34.0% |
| Cache Reuse      |     1 |         400 |               7.5% |
```

### `contextbudget report <run.json> [--out <path>] [--policy <policy.toml>]`
Render summary report from run artifact.

### `contextbudget diff <old-run.json> <new-run.json>`
Compare two run artifacts and emit JSON + Markdown delta outputs.

### `contextbudget pr-audit --repo <path> [--base <ref>] [--head <ref>]`
Analyze a pull request diff directly from git and emit:
- `<prefix>.json`
- `<prefix>.md`
- `<prefix>.comment.md`

The audit estimates changed-file token cost before and after the PR, flags files that grew, detects newly introduced dependencies, highlights context-complexity increases, and produces a ready-to-post PR comment.

Useful CI gates:
- `--max-token-increase N`
- `--max-token-increase-pct PCT`

In GitHub Actions, prefer explicit SHAs from the pull-request event:

```bash
contextbudget pr-audit \
  --repo . \
  --base "${{ github.event.pull_request.base.sha }}" \
  --head "${{ github.event.pull_request.head.sha }}" \
  --out-prefix contextbudget-pr
```

### `contextbudget benchmark <task> --repo <path>`
Compare deterministic strategies:
- naive full-context
- top-k selection
- compressed pack
- cache-assisted pack

Benchmark artifacts also record the active token-estimator backend and a small estimator comparison
on local sample text from the run.

`benchmark` also accepts `--workspace <workspace.toml>` for multi-repo/local-package runs.

### `contextbudget heatmap [<history> ...] [--limit N] [--out-prefix <path>]`
Aggregate historical `pack` artifacts into file and directory token heatmaps.
Directories are scanned recursively for `*.json` files and non-pack artifacts are skipped.

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

When `--delta` is used, policy evaluation applies to the effective delta package size
instead of the full current baseline.

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
