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

- **tokens before optimization** - raw token count across all packed files
- **tokens after optimization** - token count actually sent to the model
- **savings per stage** - how much each optimization stage contributed
- **total savings** - absolute tokens removed and percentage reduction

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

### `contextbudget read-profiler <run.json> [--out-prefix <prefix>]`

Analyze how a coding agent read repository files in a pack run.  Detects access
pattern problems and quantifies tokens wasted.

**Detects:**

| Flag | Condition |
|------|-----------|
| duplicate read | Same file path appears more than once in the context pack |
| unnecessary read | Low relevance score (≤ 1.0) **and** file costs ≥ 50 tokens |
| high token-cost read | File's original token count ≥ 500 |

**Output includes:**

- Files read (total and unique)
- Duplicate reads detected vs. prevented-by-packer
- Unnecessary reads count
- High token-cost reads count
- Tokens wasted (duplicates + unnecessary)
- Per-file breakdown table with flags
- Separate tables for duplicate, unnecessary, and high-cost files

**Example:**

```bash
contextbudget pack "add caching" --repo . --max-tokens 20000
contextbudget read-profiler run.json
```

**Sample output (`run-read-profile.md`):**

```markdown
# ContextBudget Agent Read Profile

## Summary

| Metric | Value |
|--------|-------|
| Files read (total)                | 9  |
| Unique files read                 | 8  |
| Duplicate reads detected          | 1  |
| Duplicate reads prevented (packer)| 0  |
| Unnecessary reads                 | 2  |
| High token-cost reads             | 3  |
| Tokens wasted (duplicates)        | 340 |
| Tokens wasted (unnecessary)       | 680 |
| Total tokens wasted               | 1020 |

## Duplicate Reads

| File              | Read Count | Tokens/Read | Tokens Wasted |
|-------------------|-----------|------------|---------------|
| `src/router.py`   | 2          | 340         | 340           |
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

### `contextbudget prepare-context <task> --repo <path> [--max-tokens N] [--top-files N]`

Run the full middleware pipeline: pack context, optionally enforce a budget policy, and
write a machine-readable artifact with an additive `agent_middleware` block.

```bash
contextbudget prepare-context "add caching to search API" --repo . --max-tokens 20000
```

**With delta mode:**

```bash
contextbudget prepare-context "add caching" --repo . --delta previous-run.json
```

**With strict policy enforcement:**

```bash
contextbudget prepare-context "large refactor" --repo . --strict --policy policy.toml
```

Returns non-zero when `--strict` is set and a policy violation is detected.

The output artifact (`prepare-context-run.json`) includes the full pack artifact plus
an `agent_middleware` block with file counts, token estimates, quality risk, cache stats,
and the original request parameters. Use `--out-prefix` to control the output file name.

---

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

---

## Observability and Analytics Commands

These commands turn raw run artifacts into actionable developer intelligence.

### `contextbudget observe <run.json> [--format human|json]`

Extract and store observability metrics from a `pack` run artifact.

Reads a `run.json` produced by `pack` and computes:

- **total_tokens** / **tokens_saved** / **baseline_tokens**
- **files_read** / **unique_files_read** / **duplicate_reads**
- **cache_hits** / **run_duration_ms**

Metrics are persisted to `.contextbudget/observe-history.json` for trend tracking.

**Flags:**

| Flag | Description |
|------|-------------|
| `--no-store` | Skip persisting to history |
| `--export-history` | Also dump the full history store to `<prefix>-history.json` |
| `--base-dir` | Root used to locate the `.contextbudget/` directory |
| `--out-prefix` | Output file prefix (default: `<run>-observe`) |
| `--format human\|json` | `human` prints the markdown report; `json` prints raw JSON to stdout |

**Example:**

```bash
# After a pack run
contextbudget pack "add caching" --repo . --max-tokens 20000
contextbudget observe run.json

# Machine-readable for scripting
contextbudget observe run.json --format json | jq '.total_tokens'

# Export full history
contextbudget observe run.json --export-history
```

**Outputs:** `<prefix>.json`, `<prefix>.md`, optionally `<prefix>-history.json`

---

### `contextbudget simulate-agent <task> --repo <path> [--format human|json]`

Estimate token costs and USD spend for a multi-step agent workflow **before** running it.

Models three context accumulation modes and prices tokens against known model rates.

**Context modes:**

| Mode | Description |
|------|-------------|
| `isolated` | Each workflow step has independent context (default) |
| `rolling` | Two-step sliding window — context from the previous step carries forward |
| `full` | Context grows across all steps (cumulative) |

**Key flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--context-mode` | `isolated` | Context accumulation strategy |
| `--model` | `gpt-4o` | Model for cost estimation |
| `--prompt-overhead` | `800` | Estimated system + user prompt tokens per step |
| `--output-tokens` | `600` | Estimated model output tokens per step |
| `--price-input` | — | Custom input price (USD / 1M tokens), overrides built-in model table |
| `--price-output` | — | Custom output price (USD / 1M tokens) |
| `--list-models` | — | Print all supported models and exit |
| `--format human\|json` | `human` | `json` prints raw JSON to stdout |

**Example:**

```bash
# Estimate costs with rolling context for Claude Sonnet
contextbudget simulate-agent "implement OAuth2" \
  --repo . \
  --model claude-3-5-sonnet-20241022 \
  --context-mode rolling

# List all supported models
contextbudget simulate-agent --list-models

# JSON output for CI integration
contextbudget simulate-agent "add caching" --repo . --format json \
  | jq '.cost_estimate.total_cost_usd'
```

**Outputs:** `<prefix>.json`, `<prefix>.md`

---

### `contextbudget drift [--repo <path>] [--format human|json]`

Detect token usage growth trends across historical `pack` runs and alert when context is expanding.

Reads `.contextbudget/history.json`, splits entries into a baseline window and a recent window, then computes drift across three dimensions:

| Metric | Description |
|--------|-------------|
| `token_drift_pct` | % change in estimated input tokens |
| `file_drift_pct` | % change in files included per run |
| `dep_depth_drift_pct` | % change in average dependency depth |

Returns **exit code 2** when drift exceeds the threshold (useful in CI).

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--window` | `20` | Number of recent history entries to analyze |
| `--threshold` | `10.0` | Drift % that triggers an alert |
| `--task` | — | Filter history by task substring |
| `--out-prefix` | `contextbudget-drift` | Output file prefix |
| `--format human\|json` | `human` | `json` prints raw JSON to stdout |

**Example:**

```bash
# Detect drift (exits 2 if alert, 0 if clean)
contextbudget drift --repo . --threshold 15

# Filter to a specific task area
contextbudget drift --repo . --task "auth"

# CI gate
contextbudget drift --repo . && echo "Context stable" || echo "DRIFT DETECTED"

# JSON for dashboards
contextbudget drift --repo . --format json | jq '.drift.token_drift_pct'
```

**Outputs:** `contextbudget-drift.json`, `contextbudget-drift.md`

---

### `contextbudget advise [--repo <path>] [--format human|json]`

Analyze a repository's import graph and suggest architecture improvements to reduce context bloat.

Detects three categories of problem:

| Category | Signal | Default threshold |
|----------|--------|------------------|
| `split_file` | File is too large | ≥ 500 tokens |
| `extract_module` | File has very high fan-in (many importers) | ≥ 5 importers |
| `reduce_dependencies` | File has very high fan-out (imports many files) | ≥ 10 imports |

Each suggestion includes an `estimated_token_impact` showing how many tokens could be saved.

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--history` | — | Pack JSON files to compute inclusion-frequency signals |
| `--large-file-tokens` | `500` | Token threshold for "large file" |
| `--high-fanin` | `5` | Min importer count for fan-in flag |
| `--high-fanout` | `10` | Min outgoing imports for fan-out flag |
| `--high-frequency-rate` | `0.5` | Min pack inclusion rate (0–1) for frequency flag |
| `--top` | `25` | Max suggestions to emit |
| `--format human\|json` | `human` | `json` prints raw JSON to stdout |

**Example:**

```bash
# Basic analysis
contextbudget advise --repo .

# With pack history for frequency signals
contextbudget advise --repo . --history run*.json

# JSON for tooling integration
contextbudget advise --repo . --format json \
  | jq '[.suggestions[] | select(.suggestion == "split_file")]'
```

**Outputs:** `contextbudget-advise.json`, `contextbudget-advise.md`

---

### `contextbudget visualize [--repo <path>] [--html] [--format human|json]`

Build and export a repository dependency graph annotated with token counts and historical inclusion frequency.

Each graph node carries:
- `estimated_tokens` — token cost of the file
- `inclusion_count` / `inclusion_rate` — how often this file appears in pack runs
- `in_degree` / `out_degree` — import graph connectivity
- `is_entrypoint` — whether the file is a module root

**Flags:**

| Flag | Description |
|------|-------------|
| `--history` | Pack JSON files or directories to compute inclusion-frequency annotations |
| `--html` | Also write a self-contained interactive HTML visualization |
| `--out-prefix` | Output file prefix (default: `contextbudget-graph`) |
| `--format human\|json` | `human` prints a summary; `json` prints raw JSON to stdout |

**Example:**

```bash
# Build the graph
contextbudget visualize --repo .

# With history + interactive HTML
contextbudget visualize --repo . --history run*.json --html

# JSON for external graph tools
contextbudget visualize --repo . --format json > graph.json
```

**Outputs:** `contextbudget-graph.json`, `contextbudget-graph.md`, optionally `contextbudget-graph.html`

---

### `contextbudget dashboard [<paths>...] [--port N] [--export] [--format human|json]`

Start a local web UI to browse and compare all run artifacts interactively, or export the aggregated data.

Scans directories and JSON artifact files for pack, benchmark, simulate-agent, plan, heatmap, and profile runs. Aggregates them into a single data view displayed at `http://localhost:<port>`.

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `7842` | Port for the local server |
| `--no-open` | false | Don't auto-open the browser |
| `--export` | false | Export aggregated data as JSON and exit (no server) |
| `--out-prefix` | `contextbudget-dashboard` | File prefix for `--export` mode |
| `--format human\|json` | `human` | `json` prints dashboard data to stdout and exits |

**Example:**

```bash
# Start the dashboard
contextbudget dashboard

# Scan specific directories
contextbudget dashboard ./runs/ ../other-project/

# Export data without starting the server
contextbudget dashboard --export --out-prefix ./reports/dashboard

# Pipe to jq
contextbudget dashboard --format json | jq '.summary'
```

---

### `contextbudget read-profiler <run.json> [--format human|json]`

Detect duplicate and unnecessary file reads in a pack run and quantify the tokens wasted.

**Flags:**

| Flag | Description |
|------|-------------|
| `--out-prefix` | Output file prefix (default: `<run>-read-profile`) |
| `--format human\|json` | `human` prints full report; `json` prints raw JSON to stdout |

**Example:**

```bash
contextbudget pack "add caching" --repo . --max-tokens 20000
contextbudget read-profiler run.json

# JSON for CI checks
contextbudget read-profiler run.json --format json \
  | jq '.tokens_wasted_total'
```

**Outputs:** `<prefix>.json`, `<prefix>.md`

---

### `contextbudget dataset <tasks.toml> --repo <path> [--format human|json]`

Build a reproducible benchmark dataset from a TOML task list and export per-task token reduction metrics.

The TOML file must contain a `[[tasks]]` array:

```toml
[[tasks]]
name = "Add caching"
task = "add Redis caching to the search API"

[[tasks]]
name = "Add authentication"
task = "add JWT authentication to user routes"
```

Use `contextbudget build-dataset` to run the same pipeline with built-in tasks (no TOML required).

**Flags:**

| Flag | Description |
|------|-------------|
| `--max-tokens` | Token budget for each benchmark run |
| `--top-files` | Top-files limit for each run |
| `--out-prefix` | Output file prefix (default: `contextbudget-dataset`) |
| `--format human\|json` | `human` prints per-task summary; `json` prints raw JSON to stdout |

**Example:**

```bash
# Run the benchmark suite
contextbudget dataset tasks.toml --repo .

# JSON output
contextbudget dataset tasks.toml --repo . --format json \
  | jq '.aggregate.avg_reduction_pct'

# Built-in task suite (no TOML required)
contextbudget build-dataset --repo .
```

**Outputs:** `contextbudget-dataset.json`, `contextbudget-dataset.md`

---

## JSON Output Mode

All analytics commands support `--format json` to print the raw data structure to stdout instead of the human-readable summary. Files are still written to disk in both modes.

This is useful for:
- Piping into `jq` for field extraction
- Feeding into CI gates and dashboards
- Integrating with external tooling

```bash
# Extract a single field
contextbudget observe run.json --format json | jq '.total_tokens'

# Chain with other tools
contextbudget drift --repo . --format json \
  | jq '{alert: .drift.alert, token_drift: .drift.token_drift_pct}'
```

---

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
