# ContextBudget

**Open-source infrastructure for token-aware coding-agent workflows.**

## Why this matters for coding agents

Most agent failures are not model failures. They are context failures:
- too much irrelevant code
- repeated file reads
- no budget guardrails
- no deterministic way to explain what got packed and why

ContextBudget helps agents stay focused, cheaper, and easier to operate in local workflows, CI, and future agent integrations.

## Problem

Teams still send huge repository slices to models by default. That wastes tokens, increases latency, and raises failure risk on long tasks.

You need deterministic context packing, explicit budgets, and machine-readable run artifacts.

## Before vs After

**Before**
- Send broad repo context to every request
- Re-read the same files across runs
- Discover token overruns too late

**After (ContextBudget)**
- Rank relevant files deterministically
- Compress context by strategy (`full`, `snippet`, `summary`)
- Reuse cached summaries and detect duplicate reads
- Enforce budget and policy thresholds in CI

## Demo Commands

```bash
# Install
python3 -m pip install -e .[dev]

# 1) Plan relevant context
contextbudget plan "add caching to search API" --repo .

# 2) Pack under a token budget
contextbudget pack "refactor auth middleware" --repo . --max-tokens 30000

# 3) Summarize a run artifact
contextbudget report run.json

# 4) Compare two runs
contextbudget diff old-run.json new-run.json

# 5) Evaluate strategy quality
contextbudget benchmark "add rate limiting to auth API" --repo .
```

## Example Output

`run.json` contains deterministic, machine-readable signals:

```json
{
  "command": "pack",
  "task": "refactor auth middleware",
  "files_included": ["src/auth.py", "src/middleware.py"],
  "files_skipped": ["docs/notes.md"],
  "budget": {
    "estimated_input_tokens": 1240,
    "estimated_saved_tokens": 3860,
    "duplicate_reads_prevented": 2,
    "quality_risk_estimate": "medium"
  },
  "compressed_context": [
    {
      "path": "src/auth.py",
      "strategy": "snippet",
      "chunk_strategy": "language-aware-python",
      "chunk_reason": "imports + function/class regions"
    }
  ]
}
```

Sample artifacts: `examples/sample-outputs/`.

## Architecture

```text
contextbudget/
  config.py      # contextbudget.toml loader + typed settings
  core/          # orchestration, rendering, policies, benchmark, diff
  stages/        # scan/score/pack/cache/render boundaries
  scanners/      # repository traversal + metadata
  scorers/       # deterministic relevance + import graph signals
  compressors/   # full/snippet/summary + language-aware chunking
  cache/         # summary cache + duplicate detection
  telemetry/     # optional sink abstraction (no-op by default)
  schemas/       # typed dataclasses + stable artifact shapes
```

Principles:
- deterministic heuristics first
- typed Python, lightweight dependencies
- no hidden data collection, no required cloud
- strong extension path for future model-backed summarization/plugins

## CI + Policy Gates

Use ContextBudget as a quality gate for agent context in CI.

```bash
# Fail build on policy violations
contextbudget pack "refactor auth middleware" --repo . --strict --policy examples/policy.toml

# Re-check an existing run artifact
contextbudget report run.json --policy examples/policy.toml
```

Supported policy checks:
- max estimated input tokens
- max files included
- max quality risk level
- minimum estimated savings percentage

Workflow example: `.github/workflows/contextbudget.yml`  
Policy example: `.github/contextbudget-policy.toml`

## Python API

```python
from contextbudget import BudgetGuard, ContextBudgetEngine

guard = BudgetGuard(max_tokens=30000)
result = guard.pack(task="add caching to search API", repo=".")

engine = ContextBudgetEngine()
plan = engine.plan(task="risky auth change", repo=".")
policy = engine.make_policy(max_files_included=12, max_quality_risk_level="medium")
policy_result = engine.evaluate_policy(result, policy=policy)
```

Public API:
- `ContextBudgetEngine.plan(...)`
- `ContextBudgetEngine.pack(...)`
- `ContextBudgetEngine.report(...)`
- `ContextBudgetEngine.evaluate_policy(...)`
- `BudgetGuard.pack(...)`

## Roadmap

See [ROADMAP.md](ROADMAP.md). Current direction:
- stronger drift analysis and benchmark coverage
- richer extension hooks for external summarizers
- deeper CI/agent integration patterns
- optional cloud analytics via explicit telemetry sinks (local-first by default)

## Contribution Guide

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## Documentation

See the docs index: [docs/index.md](docs/index.md).
