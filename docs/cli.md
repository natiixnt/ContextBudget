# CLI Reference

## Commands

### `contextbudget plan <task> --repo <path>`
Rank relevant files for a natural-language task.

### `contextbudget pack <task> --repo <path> [--max-tokens N] [--top-files N]`
Build compressed context package and write `run.json` + `run.md` by default.

### `contextbudget report <run.json> [--out <path>] [--policy <policy.toml>]`
Render summary report from run artifact.

### `contextbudget diff <old-run.json> <new-run.json>`
Compare two run artifacts and emit JSON + Markdown delta outputs.

### `contextbudget benchmark <task> --repo <path>`
Compare deterministic strategies:
- naive full-context
- top-k selection
- compressed pack
- cache-assisted pack

## Strict Policy Mode

```bash
contextbudget pack "refactor auth middleware" --repo . --strict --policy examples/policy.toml
```

Strict mode returns non-zero on policy violations.

## Config Override

Each command supports `--config <path>` to load a custom `contextbudget.toml`.
