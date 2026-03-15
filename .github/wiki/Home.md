# ContextBudget Wiki

**ContextBudget** selects, compresses, and budgets repository context for coding-agent workflows. It is deterministic, local-first, and produces stable machine-readable artifacts for reuse in CI, local tooling, and agent middleware.

---

## Quick Start

```bash
pip install -e .[dev]

# Rank relevant files
contextbudget plan "add caching to search API" --repo .

# Pack context under budget
contextbudget pack "add caching to search API" --repo . --max-tokens 30000

# Summarize the run artifact
contextbudget report run.json
```

---

## Key Features

| Feature | Description |
|---------|-------------|
| **File Ranking** | Deterministic relevance scoring against natural-language tasks |
| **Token Packing** | Packs context under explicit token budgets with quality-risk estimation |
| **Compression** | Snippet extraction, symbol extraction, language-aware chunking, and deterministic summaries |
| **Incremental / Delta** | Re-packs only changed files across agent loop iterations |
| **Workspace Support** | Multi-repo and monorepo-package scanning with repo provenance tracking |
| **Benchmarking** | Compares full-context, top-k, compressed, and cache-assisted strategies |
| **Policy Enforcement** | Strict-mode CI gates on token count, file count, and quality risk |
| **Agent Middleware** | Adapter-ready middleware layer for external agent tools |
| **Stable Artifacts** | `run.json` and `run.md` with additive metadata blocks |

---

## Pages

| Page | Description |
|------|-------------|
| [[Getting Started]] | Installation and core workflow |
| [[CLI Reference]] | All CLI commands and flags |
| [[Python API]] | `BudgetGuard` and `ContextBudgetEngine` reference |
| [[Configuration]] | `contextbudget.toml` schema and model profiles |
| [[Architecture]] | System layers, pipeline stages, and design goals |
| [[Agent Integration]] | Middleware, adapters, and multi-turn agent loops |
| [[Plugins]] | Custom scorers, compressors, and token estimators |
| [[Benchmarking and Diff]] | Strategy comparison and run diffing |
| [[Policy and CI]] | Budget policy enforcement and GitHub Actions |
| [[Workspace]] | Multi-repo and monorepo workspace configuration |

---

## Requirements

- Python 3.11+
- Install: `pip install -e .[dev]`
- Optional exact tokenization: `pip install -e .[tokenizers]`
