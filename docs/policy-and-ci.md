# Policy and CI

## Strict Policy Checks

Use strict mode when agent context quality must be gated.

```bash
contextbudget pack "tighten auth middleware token validation" \
  --repo . \
  --strict \
  --policy examples/policy.toml
```

Supported checks:
- max estimated input tokens
- max files included
- max quality risk level
- minimum estimated savings percentage

## Existing GitHub Action

This repository includes:
- `.github/workflows/contextbudget.yml`
- `.github/contextbudget-policy.toml`

The workflow can run in pull requests or `workflow_dispatch`, produce Markdown summaries, upload artifacts, and fail on strict policy violations.

See [github-action.md](github-action.md).
