<div align="center">

# Redcon

**Deterministic context budgeting for AI coding agents**

Stop sending agents 200k tokens of irrelevant code. Redcon scores, compresses, and packs repo context so your agent gets what it actually needs.

[![Tests](https://github.com/natiixnt/ContextBudget/actions/workflows/test.yml/badge.svg)](https://github.com/natiixnt/ContextBudget/actions/workflows/test.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![VS Code Extension](https://img.shields.io/visual-studio-marketplace/v/redcon.redcon?label=VS%20Code)](https://marketplace.visualstudio.com/items?itemName=redcon.redcon)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Install](#install) - [Quick Start](#quick-start) - [How It Works](#how-it-works) - [Docs](docs/)

</div>

---

## The Problem

AI coding agents burn tokens on irrelevant context. You either:
- Dump the whole repo and pay for 200k input tokens per request, or
- Let the agent grep blindly and waste tool calls figuring out where to look

Redcon solves both. It ranks files by task relevance, compresses them with language-aware strategies (full, snippet, symbol extraction, summary), and packs the result under your token budget. Deterministic, local-first, no embeddings.

## Install

### Option 1: VS Code Extension (easiest)

1. Install [Redcon - Context Budget](https://marketplace.visualstudio.com/items?itemName=redcon.redcon) from the marketplace
2. Open the Redcon sidebar, click **Install & Set Up**
3. Reload window. Done.

The extension installs the CLI via pip, registers the MCP server for Claude Code, Cursor, and Windsurf, and gives you a sidebar with budget analytics, file rankings, and compression dashboards.

### Option 2: CLI + MCP Server

```bash
pip install "redcon[mcp]"
redcon init                      # creates redcon.toml + registers MCP
```

The `init` command auto-configures MCP so your AI agent can call `redcon_rank`, `redcon_search`, `redcon_compress`, and `redcon_budget` as native tools.

### Option 3: CLI only

```bash
pip install redcon
redcon init --no-mcp
```

## Quick Start

```bash
# Rank files relevant to a task
redcon plan "add rate limiting to auth API" --repo .

# Pack context under a token budget
redcon pack "refactor payment flow" --repo . --max-tokens 30000

# Compare compression strategies
redcon benchmark "add caching" --repo .

# Audit a PR for context growth
redcon pr-audit --repo . --base origin/main --head HEAD
```

Output goes to `run.json` (machine-readable) and `run.md` (human-readable). Use them in CI, or feed the compressed context directly into your agent.

## How It Works

```
task: "add rate limiting to auth"
       |
       v
  [1] scan    - incremental scan of repo files (cached)
       |
       v
  [2] rank    - score each file: keyword match, imports, file role, git history
       |
       v
  [3] compress - per-file strategy: full / snippet / symbol extraction / summary
       |
       v
  [4] pack    - fit top-N compressed files under token budget, drop the rest
       |
       v
  run.json + run.md + compressed_context ready for your agent
```

Every step is deterministic. Same input, same output. No embeddings, no random chunking.

## MCP Integration (Pull Model)

Instead of pushing a 30k-token blob to your agent, Redcon exposes 6 MCP tools the agent calls on demand:

| Tool | What it does |
|------|--------------|
| `redcon_rank` | Top-K files with scores and reasons - call this first |
| `redcon_overview` | Lightweight repo map grouped by directory |
| `redcon_compress` | Compressed single-file view for cheap inspection |
| `redcon_search` | Regex search scoped to ranked files or full repo |
| `redcon_budget` | Plan fitting files within a token budget |
| `redcon_run` | Run a shell command, return its output compressed |

Typical agent flow uses ~5k tokens for exploration instead of 30k for a blob. The agent itself decides what to read in full.

Config gets written automatically to:
- `.mcp.json` (Claude Code)
- `.cursor/mcp.json` (Cursor)
- `~/.codeium/windsurf/mcp_config.json` (Windsurf)

## Command Output Compression

Source files are only half the bloat. The other half is command output: `git diff`, `pytest`, `cargo test`, `grep`, `ls -R`. Redcon's `redcon_run` MCP tool (and `redcon run` CLI) wraps the call, parses the output, and returns a budget-aware compressed view that preserves every fact the agent actually needs.

Headline reductions on representative inputs:

| Compressor | Fixture | Raw tokens | Compact | Ultra |
|------------|---------|-----------:|---------|-------|
| `git diff` | 12 files, 240 hunks | 8,078 | **97.0%** | 99.5% |
| `pytest` | 30 failures + 200 passes | 2,555 | **73.8%** | 99.2% |
| `grep`/`rg` | 600 matches across 50 files | 7,015 | **76.9%** | 99.9% |
| `find` | 500 paths | 3,398 | **81.3%** | 99.8% |
| `ls -R` | 30 dirs x 15 files | 1,543 | **33.5%** | 99.0% |
| `kubectl events` | 200-row CrashLoopBackOff | ~5,000 | **91.5%** | 99.5% |
| `py-spy collapsed` | 200 stacks | 2,385 | **90.0%** | 99.0% |
| `json-line log` | 200 NDJSON records | 6,038 | **91.1%** | 98.0% |
| `coverage report` | 50-file grid | 738 | **73.2%** | 95.0% |
| `psql EXPLAIN ANALYZE` | 11-node Postgres plan | 435 | **71.3%** | 93.3% |

Quality is enforced separately. Every compressor declares `must_preserve_patterns` (file paths in a diff, failing test names in pytest, branch name in `git status`, slowest node operator in EXPLAIN); the M8 quality harness rejects any compressor whose compact output drops a fact present in the raw input. Run it as a CI step:

```bash
redcon cmd-quality   # exits non-zero if any compressor regressed
redcon cmd-bench     # markdown table; --json for CI baselines
redcon run "git diff" --quality-floor compact --max-output-tokens 4000
```

**Fifteen compressors** ship today: `git_diff`, `git_status`, `git_log`, `pytest`, `cargo_test`, `npm_test` (vitest+jest), `go_test`, `grep`, `ls`, `tree`, `find`, `lint` (ruff+mypy), `docker`, `pkg_install` (pip+npm+yarn), `kubectl_get`/`kubectl_events`, `profiler` (py-spy+perf), `json_log`, `coverage`, `sql_explain` (Postgres+MySQL TREE). Full per-schema benchmarks: [`docs/benchmarks/cmd/`](docs/benchmarks/cmd/).

### Cross-call dimension

Beyond per-call compression, three layers compose across an agent session:

- **Session-scoped path aliases** (V41): repeated paths like `redcon/cmd/pipeline.py` collapse to `f001` on later mentions. Lazy first-use, never net-negative.
- **Snapshot delta vs prior call** (V47): when the same argv runs twice, ship only the delta. Schema-aware renderers for `pytest` (set-diff over failure names), `git_diff` (file-set with per-file +/- counts), and `coverage` (per-file pp moves) win meaningfully over generic line-diff. Always picks `min(cost_delta, cost_abs)` so non-regressive by construction.
- **Invariant cert** (V93): every COMPACT/VERBOSE output stamps `mp_sha=<16hex>` over the sorted multiset of `(pattern, capture)` extracted from raw. Auditors recompute the cert against the compressed text to detect spurious additions or capture thinning - upgrades the existing must-preserve boolean to set-equality.

Together these turn the 15 stateless compressors into a session-aware pipeline that compounds to 30-50pp additional reduction on repeat-heavy workflows beyond per-call savings.

## VS Code Extension

Once installed you get:

- **Sidebar chat**: type a task, send, watch the pack run live
- **Dashboard**: donut/pie/bar charts for budget, strategies, token impact per file
- **Status bar**: current budget usage with risk indicator
- **CodeLens**: compression strategy and token count shown above each file
- **File decorations**: relevance score badges on files in the explorer
- **History**: browse past runs, diff them, export to clipboard

Branding: red->navy gradient with triple chevron mark, glass-style UI.

## Workspaces (Multi-Repo)

One task can span multiple repos:

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

Artifacts include `workspace`, `scanned_repos`, `selected_repos`, and repo-qualified paths like `auth-service:src/auth.py`.

See [docs/workspace.md](docs/workspace.md).

## Python API

```python
from redcon import RedconEngine

engine = RedconEngine()

# Rank files
plan = engine.plan(task="add user auth", repo=".", top_files=15)

# Pack context
result = engine.pack(
    task="add user auth",
    repo=".",
    max_tokens=30000,
    top_files=25,
)
print(f"Used {result['budget']['estimated_input_tokens']} of {result['max_tokens']} tokens")
print(f"Risk: {result['budget']['quality_risk_estimate']}")

for file in result["compressed_context"]:
    print(f"{file['path']}: {file['strategy']} ({file['compressed_tokens']} tokens)")
```

Full reference: [docs/python-api.md](docs/python-api.md).

## Features

- **Deterministic scoring**: keyword match, import graph, file role (test/docs/prod), git history
- **Language-aware compression**: Python, TypeScript, JavaScript, Go, Rust, Java, and more
- **Command output compression**: 11 compressors covering git, test runners, grep/rg, ls/tree/find with 70-99% reduction at compact level
- **Incremental scanning**: cached file metadata with git-aware change detection
- **Multi-repo workspaces**: single task, multiple repos, shared config
- **Budget policies**: enforce max tokens, quality risk levels, file counts in CI
- **Quality harness**: must-preserve regex assertions per compressor, deterministic, robust to truncated/binary input
- **Streaming runner**: chunked Popen reader with bounded memory and early SIGTERM/SIGKILL when output cap is hit
- **Run history**: SQLite-backed artifact store for both file packs and command runs, diff/heatmap/drift analysis
- **Cost analysis**: estimate token costs across GPT-4o, Claude, and other models
- **PR auditing**: detect context growth in pull requests
- **Plugin system**: custom scorers, compressors, token estimators, summarizers
- **Cache backends**: in-memory, local file, Redis
- **Doctor command**: diagnose environment, Python version, disk space, git availability

## Documentation

- [Getting Started](docs/getting-started.md) - first pack in 60 seconds
- [CLI Reference](docs/cli.md) - all commands and flags
- [Configuration](docs/configuration.md) - redcon.toml fields
- [Workspaces](docs/workspace.md) - multi-repo setup
- [Python API](docs/python-api.md) - programmatic usage
- [Agent Integration](docs/agent-integration.md) - middleware layer
- [Plugins](docs/plugins.md) - custom extensions
- [Architecture](docs/architecture.md) - how it all fits together
- [Migration Notes](docs/migration.md) - upgrading between versions

## License

Dual-licensed. Open-source core + proprietary cloud/enterprise layer.

| Component | License |
|-----------|---------|
| Core engine, CLI, plugins, cache | [MIT](LICENSE) |
| Gateway, control plane, agent middleware, LLM integrations, runtime | [Proprietary](LICENSE-COMMERCIAL) |

Commercial licensing: natjiks@gmail.com
