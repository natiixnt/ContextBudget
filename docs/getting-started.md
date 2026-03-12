# Getting Started

## Install

```bash
python3 -m pip install -e .[dev]
```

## Core Workflow

```bash
# Rank relevant files
contextbudget plan "add caching to search API" --repo .

# Pack context under budget
contextbudget pack "add caching to search API" --repo . --max-tokens 30000

# Summarize run artifact
contextbudget report run.json
```

## Extended Workflow

```bash
# Compare two runs
contextbudget diff old-run.json new-run.json

# Compare packing strategies
contextbudget benchmark "add rate limiting to auth API" --repo .
```

## Example Repositories

See commands and fixtures in [`examples/README.md`](../examples/README.md).
