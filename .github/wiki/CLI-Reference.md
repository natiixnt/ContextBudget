# CLI Reference

## Commands Overview

| Command | Purpose |
|---------|---------|
| `plan` | Rank relevant files for a task |
| `plan-agent` | Plan multi-step agent workflow |
| `pack` | Build compressed context package |
| `profile` | Explain token savings by stage |
| `read-profiler` | Analyze agent read patterns |
| `report` | Render summary from run artifact |
| `diff` | Compare two run artifacts |
| `pr-audit` | Audit PR for context growth |
| `prepare-context` | Full middleware pipeline |
| `benchmark` | Compare packing strategies |
| `heatmap` | Aggregate historical token hotspots |
| `watch` | Monitor incremental scan index |

---

## `plan`

Rank relevant files for a natural-language task.

```bash
redcon plan <task> --repo <path>
redcon plan <task> --workspace <workspace.toml>
```

Automatically maintains `.redcon/scan-index.json` for incremental reuse.

---

## `plan-agent`

Plan context usage across a multi-step agent workflow. The artifact includes step order, context assigned per step, token estimates per step, shared context, and total token estimates.

```bash
redcon plan-agent <task> --repo <path>
redcon plan-agent <task> --workspace <workspace.toml>
```

---

## `pack`

Build a compressed context package and write `run.json` + `run.md`.

```bash
redcon pack <task> --repo <path> [--max-tokens N] [--top-files N]
```

**Incremental (delta) mode** — only changed context is emitted:

```bash
redcon pack <task> --repo <path> --delta <previous-run.json>
```

The `delta` block records:
- added files, removed files, changed files and slices, changed symbols
- original tokens, delta tokens, and tokens saved

**Workspace mode:**

```bash
redcon pack <task> --workspace <workspace.toml> [--max-tokens N]
```

---

## `profile`

Explain where token savings came from in a pack run.

```bash
redcon pack "add caching" --repo . --max-tokens 20000
redcon profile run.json [--out-prefix <prefix>]
```

Outputs `<prefix>.json` + `<prefix>.md` with savings broken down by stage:

| Stage | What it captures |
|-------|-----------------|
| `cache_reuse` | Files whose summaries were reused from the summary cache |
| `symbol_extraction` | Files reduced to named symbols (classes, functions, types) |
| `slicing` | Files reduced via language-aware import/dependency slicing |
| `compression` | Files replaced by deterministic or external summaries |
| `snippet` | Files reduced to keyword-window snippets |
| `delta` | Savings from an incremental delta pack |
| `full` | Files included without reduction |

**Sample output:**

```
| Metric                     | Tokens |
|----------------------------|--------|
| Tokens before optimization | 14200  |
| Tokens after optimization  |  8900  |
| Total tokens saved         |  5300  |
| Savings                    | 37.3%  |
```

---

## `read-profiler`

Analyze how a coding agent read repository files. Detects access pattern problems and quantifies wasted tokens.

```bash
redcon read-profiler run.json [--out-prefix <prefix>]
```

**Detects:**

| Flag | Condition |
|------|-----------|
| duplicate read | Same file path appears more than once |
| unnecessary read | Low relevance score (≤ 1.0) and file costs ≥ 50 tokens |
| high token-cost read | File's original token count ≥ 500 |

---

## `report`

Render a summary report from a run artifact.

```bash
redcon report <run.json> [--out <path>] [--policy <policy.toml>]
```

---

## `diff`

Compare two run artifacts.

```bash
redcon diff old-run.json new-run.json
```

Inspects: task differences, files added/removed, ranked score changes, token/savings/risk/cache deltas.

---

## `pr-audit`

Analyze a pull request diff directly from git.

```bash
redcon pr-audit --repo <path> [--base <ref>] [--head <ref>]
```

Outputs:
- `<prefix>.json`
- `<prefix>.md`
- `<prefix>.comment.md` — ready-to-post PR comment

Estimates changed-file token cost before and after the PR, flags files that grew, detects newly introduced dependencies, highlights context-complexity increases.

**CI gates:**
- `--max-token-increase N`
- `--max-token-increase-pct PCT`

**GitHub Actions example:**

```bash
redcon pr-audit \
  --repo . \
  --base "${{ github.event.pull_request.base.sha }}" \
  --head "${{ github.event.pull_request.head.sha }}" \
  --out-prefix redcon-pr
```

---

## `prepare-context`

Run the full middleware pipeline: pack context, optionally enforce a budget policy, and write a machine-readable artifact with an additive `agent_middleware` block.

```bash
redcon prepare-context "add caching to search API" --repo . --max-tokens 20000
```

**With delta mode:**

```bash
redcon prepare-context "add caching" --repo . --delta previous-run.json
```

**With strict policy enforcement:**

```bash
redcon prepare-context "large refactor" --repo . --strict --policy policy.toml
```

Returns non-zero when `--strict` is set and a policy violation is detected.

---

## `benchmark`

Compare deterministic strategies for one task:
- naive full-context
- top-k selection
- compressed pack
- cache-assisted pack

```bash
redcon benchmark "add rate limiting to auth API" --repo .
redcon benchmark <task> --workspace <workspace.toml>
```

Outputs: terminal summary, JSON artifact, Markdown report, and estimator comparison on local samples.

---

## `heatmap`

Aggregate historical pack artifacts into file and directory token heatmaps.

```bash
redcon heatmap [<history> ...] [--limit N] [--out-prefix <path>]
```

Directories are scanned recursively for `*.json` files; non-pack artifacts are skipped.

---

## `watch`

Refresh the incremental scan index and print file-change summaries.

```bash
redcon watch --repo <path> [--poll-interval S] [--once]
```

**Sample output:**

```
Watching repository: /repo
Polling interval: 1.00s
Scan index: /repo/.redcon/scan-index.json
Initial scan: repo=/repo tracked=12 included=10 reused=0 added=12 updated=0 removed=0
added[src/auth.py, src/cache.py, docs/notes.md]
Scan change: repo=/repo tracked=12 included=10 reused=11 added=0 updated=1 removed=0
updated[src/auth.py]
```

---

## Global Flags

| Flag | Description |
|------|-------------|
| `--config <path>` | Load a custom `redcon.toml` |
| `--policy <path>` | Apply a policy TOML file |
| `--strict` | Return non-zero on policy violations |
| `--max-tokens N` | Override token budget |
| `--top-files N` | Override max candidate files |
| `--delta <run.json>` | Enable incremental delta mode |
| `--workspace <path>` | Use workspace TOML instead of `--repo` |
| `--out-prefix <prefix>` | Control output file name prefix |

---

## Incremental Scan Index

`plan`, `pack`, and `benchmark` automatically maintain `.redcon/scan-index.json`. Unchanged files reuse prior scan metadata; changed and deleted files are refreshed incrementally.
