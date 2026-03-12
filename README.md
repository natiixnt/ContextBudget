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
- Duplicate read detection (content-hash based)
- Summary cache for reuse across runs
- Budget guard for max token limits
- Savings + quality risk reporting
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
```

Outputs:
- `contextbudget-plan-<task>.json`
- `contextbudget-plan-<task>.md`
- `run.json`
- `run.md`
- `run.report.md` (or `--out`)

## Architecture

```text
contextbudget/
  core/         # orchestration, rendering, token helpers
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
