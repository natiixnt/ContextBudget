# ContextBudget

**Stop sending your whole repo to the model.**

## Problem

Coding-agent workflows often burn tokens on low-value context:
- too many files sent up front
- repeated reads of the same code
- no hard token budget
- poor visibility into waste vs useful context

Result: higher cost, slower iterations, and noisier model reasoning.

ContextBudget reduces this waste by selecting relevant files, compressing low-priority context, caching summaries, and enforcing token limits.

## Before vs After

**Before**
- Agent reads broad repository slices for each task.
- Duplicate files/functions are repeatedly re-read.
- Prompt context exceeds practical token budgets.

**After (with ContextBudget)**
- Task-scoped files are ranked by deterministic relevance.
- Lower-priority files are summarized/snippet-compressed.
- Previously seen context is reused from cache.
- Pack output stays within a configured token budget.

## Demo

```bash
# 1) Rank likely-relevant files
contextbudget plan "add caching to search API" --repo .

# 2) Build a budget-constrained context package
contextbudget pack "refactor auth middleware" --repo . --max-tokens 30000

# 3) Summarize an existing run artifact
contextbudget report run.json
```

## Features

- Repository scan for text/code files
- Relevance scoring for task-file matching
- Context compression (`full`, `snippet`, `summary`)
- Language-aware chunking for Python, TypeScript/JavaScript, and Go
- Duplicate read detection (content-hash based)
- Summary cache for reuse across runs
- Budget guard for max token limits
- Savings + quality risk reporting
- Run-to-run delta analysis (`contextbudget diff`)
- Multi-strategy benchmark mode (`contextbudget benchmark`)
- JSON and Markdown outputs
- Designed for coding-agent workflows

## Example Output

`run.json` (pack result) includes:
- `ranked_files`
- `compressed_context`
- `budget.estimated_input_tokens`
- `budget.estimated_saved_tokens`
- `files_included`
- `files_skipped`
- `budget.duplicate_reads_prevented`
- `budget.quality_risk_estimate`
- `cache_hits`
- `compressed_context[].chunk_strategy`
- `compressed_context[].chunk_reason`
- `compressed_context[].selected_ranges`

Sample artifacts are available under `examples/sample-outputs/`.

## Install

```bash
python3 -m pip install -e .
```

For development:

```bash
python3 -m pip install -e .[dev]
```

## Quickstart

```bash
# Plan
contextbudget plan "add caching to search API" --repo .

# Pack under budget (writes run.json + run.md by default)
contextbudget pack "refactor auth middleware" --repo . --max-tokens 30000

# Report
contextbudget report run.json

# Optional: custom config path
contextbudget pack "refactor auth middleware" --repo . --config ./contextbudget.toml

# Diff two runs
contextbudget diff old-run.json new-run.json

# Strict enforcement in CI
contextbudget pack "refactor auth middleware" --repo . --strict --policy examples/policy.toml
contextbudget report run.json --policy examples/policy.toml

# Benchmark strategies
contextbudget benchmark "add rate limiting to auth API" --repo .
```

Optional repo-level config (`contextbudget.toml`):

```toml
[scan]
include_globs = ["**/*.py", "**/*.md", "pyproject.toml"]
ignore_globs = ["**/migrations/**", "**/*.generated.*"]
max_file_size_bytes = 1500000

[budget]
max_tokens = 24000
top_files = 30

[score]
critical_path_keywords = ["auth", "permissions", "billing"]

[compression]
summary_preview_lines = 10

[cache]
summary_cache_enabled = true
duplicate_hash_cache_enabled = true
```

Precedence: CLI flags override `contextbudget.toml`, and config overrides built-in defaults.
An end-to-end sample file is available at `examples/contextbudget.toml`.

Outputs:
- `contextbudget-plan-<task>.json`
- `contextbudget-plan-<task>.md`
- `run.json`
- `run.md`
- `run.report.md` (or `--out`)
- `<old>-vs-<new>.diff.json` (for `diff`)
- `<old>-vs-<new>.diff.md` (for `diff`)
- `contextbudget-benchmark-<task>.json` (for `benchmark`)
- `contextbudget-benchmark-<task>.md` (for `benchmark`)

## Library API

ContextBudget is reusable as a Python library for agent integrations and services.

```python
from contextbudget import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.pack(task="add caching to search API", repo=".")
print(result["budget"]["estimated_input_tokens"])
```

```python
from contextbudget import ContextBudgetEngine

engine = ContextBudgetEngine()
plan = engine.plan(task="refactor auth middleware", repo=".")
run = engine.pack(task="refactor auth middleware", repo=".", max_tokens=24000)
summary = engine.report(run)

policy = engine.make_policy(max_files_included=12, max_quality_risk_level="medium")
policy_result = engine.evaluate_policy(run, policy=policy)
```

Public API surface:
- `ContextBudgetEngine.plan(...)`
- `ContextBudgetEngine.pack(...)`
- `ContextBudgetEngine.report(...)`
- `ContextBudgetEngine.evaluate_policy(...)`
- `BudgetGuard.pack(...)` with optional strict policy enforcement

## Strict Policy Enforcement

Use strict mode when CI or automated agents must fail on budget-policy violations.

Example `policy.toml`:

```toml
[policy]
max_estimated_input_tokens = 30000
max_files_included = 12
max_quality_risk_level = "medium"
min_estimated_savings_percentage = 10.0
```

Starter policy file: `examples/policy.toml`.

Commands:

```bash
# Enforce during pack (returns non-zero on violation)
contextbudget pack "refactor auth middleware" --repo . --strict --policy policy.toml

# Enforce against an existing run artifact
contextbudget report run.json --policy policy.toml
```

Policy checks currently support:
- max estimated input tokens
- max files included
- max quality risk level
- minimum estimated savings percentage

Default behavior is unchanged unless strict checks are explicitly requested:
- `pack`: use `--strict` (optionally with `--policy`)
- `report`: use `--policy`

## GitHub Action Integration

ContextBudget includes a CI workflow example:
- `.github/workflows/contextbudget.yml`
- `.github/contextbudget-policy.toml`

The workflow:
1. Runs ContextBudget from a task or changed-files input.
2. Generates a Markdown summary in GitHub Actions job summary.
3. Uploads JSON/Markdown artifacts.
4. Fails the workflow when strict mode is enabled and policy is violated.

Manual dispatch supports inputs:
- `task`
- `changed_files`
- `strict_mode`
- `policy_path`

See [docs/github-action.md](docs/github-action.md) for setup details.

## Benchmark Mode

Benchmark mode compares deterministic strategies:
- naive full-context strategy
- top-k file selection
- compressed pack strategy
- cache-assisted strategy

Command:

```bash
contextbudget benchmark "add rate limiting to auth API" --repo . --out-prefix benchmark-auth
```

It generates:
- terminal summary
- `benchmark-auth.json`
- `benchmark-auth.md`

Screenshot placeholders:
- Before (naive full-context): `[placeholder: docs/assets/benchmark-before.png]`
- After (compressed/cache-assisted): `[placeholder: docs/assets/benchmark-after.png]`

## Architecture

```text
contextbudget/
  config.py     # contextbudget.toml loader + typed settings
  core/         # orchestration, rendering, token helpers
  stages/       # explicit scan/score/pack/cache/render boundaries
  scanners/     # repository traversal + file metadata
  scorers/      # deterministic relevance heuristics
  compressors/  # full/snippet/summary context packing
  cache/        # summary cache persistence
  schemas/      # typed dataclasses + constants
examples/
tests/
```

Design principles:
- typed Python
- lightweight dependencies
- deterministic heuristics first
- no fake model integration
- easy extension path for future LLM-backed summarization

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned milestones, including:
- language-aware chunking and scoring
- plugin interfaces
- incremental indexing
- optional LLM-assisted summarization with strict fallback

## Contribution Guide

See [CONTRIBUTING.md](CONTRIBUTING.md).

High-priority backlog items are tracked in [`.github/ISSUES_BACKLOG.md`](.github/ISSUES_BACKLOG.md).
