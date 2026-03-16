# Getting Started

## Install

```bash
python3 -m pip install -e .[dev]
```

For exact tokenization via `tiktoken`:

```bash
python3 -m pip install -e .[tokenizers]
```

---

## Core Workflow

```bash
# 1. Rank relevant files for a task
redcon plan "add caching to search API" --repo .

# 2. Pack context under a token budget
redcon pack "add caching to search API" --repo . --max-tokens 30000

# 3. Summarize the generated run artifact
redcon report run.json
```

---

## Extended Workflow

```bash
# Compare two run artifacts
redcon diff old-run.json new-run.json

# Compare packing strategies side-by-side
redcon benchmark "add rate limiting to auth API" --repo .

# Analyze token savings by compression stage
redcon profile run.json

# Detect duplicate or unnecessary file reads
redcon read-profiler run.json
```

---

## Multi-step Agent Planning

```bash
# Plan context usage across a multi-step agent workflow
redcon plan-agent "refactor auth middleware" --repo .
```

---

## Workspace (Multi-repo)

Create a `workspace.toml`:

```toml
name = "backend-services"

[scan]
include_globs = ["**/*.py"]

[budget]
max_tokens = 28000
top_files = 24

[[repos]]
label = "auth-service"
path = "../auth-service"

[[repos]]
label = "billing-service"
path = "../billing-service"
```

Then run across all repos:

```bash
redcon pack "add caching" --workspace workspace.toml
```

---

## Generated Artifacts

Every `pack` run writes:

- `run.json` - machine-readable artifact with ranked files, compressed context, budget stats, cache info
- `run.md` - human-readable Markdown summary

---

## Python API Quickstart

```python
from redcon import BudgetGuard

guard = BudgetGuard(max_tokens=30000)
result = guard.pack_context(task="add caching", repo=".")

budget = result["budget"]
print(f"tokens: {budget['estimated_input_tokens']} / {guard.max_tokens}")
print(f"saved:  {budget['estimated_saved_tokens']}")
print(f"risk:   {budget['quality_risk_estimate']}")

# Build a prompt from the compressed context
prompt = "\n".join(f["text"] for f in result["compressed_context"])
```

See [[Python API]] for the full reference.

---

## Next Steps

- [[CLI Reference]] - complete command documentation
- [[Configuration]] - `redcon.toml` settings
- [[Agent Integration]] - embedding Redcon in agent loops
- [[Benchmarking and Diff]] - strategy comparison
