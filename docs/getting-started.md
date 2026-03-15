# Getting Started

## Install

```bash
python3 -m pip install -e .[dev]
```

## Core Workflow

```bash
# Rank relevant files
redcon plan "add caching to search API" --repo .

# Pack context under budget
redcon pack "add caching to search API" --repo . --max-tokens 30000

# Summarize run artifact
redcon report run.json
```

## Extended Workflow

```bash
# Compare two runs
redcon diff old-run.json new-run.json

# Compare packing strategies
redcon benchmark "add rate limiting to auth API" --repo .
```

## Example Repositories

See commands and fixtures in [`examples/README.md`](../examples/README.md).
