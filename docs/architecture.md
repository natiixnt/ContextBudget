# ContextBudget v0.2 Architecture

## Goals

v0.2 keeps CLI behavior stable while making stage boundaries explicit and introducing config-driven defaults.

Commands remain unchanged:
- `contextbudget plan`
- `contextbudget pack`
- `contextbudget report`
- `contextbudget diff`
- `contextbudget benchmark`

Strict policy enforcement is available for CI-style gating:
- `contextbudget pack ... --strict --policy policy.toml`
- `contextbudget report run.json --policy policy.toml`

## Stage Boundaries

Pipeline boundaries are now explicit in `contextbudget/stages/workflow.py`:
- scan stage: `run_scan_stage`
- score stage: `run_score_stage`
- cache stage: `run_cache_stage`
- pack/compression stage: `run_pack_stage`
- render/output stage: `run_render_stage`

`contextbudget/core/pipeline.py` remains the compatibility facade for higher-level callers.

## Library Layer

v0.2 also exposes a stable Python API for non-CLI integrations:
- `ContextBudgetEngine.plan(...)`
- `ContextBudgetEngine.pack(...)`
- `ContextBudgetEngine.report(...)`
- `ContextBudgetEngine.evaluate_policy(...)`

For higher-level integrations, `BudgetGuard` wraps packing defaults plus optional
strict policy enforcement.

The CLI remains a thin wrapper over this library surface.

Run-to-run analysis is implemented in `contextbudget/core/diffing.py` and powers:
- context drift analysis (`contextbudget diff old-run.json new-run.json`)
- future benchmark/longitudinal comparison modes

Compression now includes deterministic language-aware chunking heuristics for:
- Python
- TypeScript / JavaScript
- Go

Pack artifacts expose chunk metadata per compressed file:
- `chunk_strategy`
- `chunk_reason`
- `selected_ranges`

## Configuration System

ContextBudget now supports `contextbudget.toml` at repository root (or `--config <path>` in CLI).

Config is loaded through `contextbudget/config.py` and exposes typed settings:
- `scan`
- `budget`
- `score`
- `compression`
- `cache`

If no config file is present, defaults match v0.1 behavior.
Precedence rules are deterministic: `CLI flag > config value > built-in default`.

## Migration Notes

- Existing CLI invocations still work.
- Existing JSON/Markdown output schema is unchanged.
- New reusable library entry points are exported from `contextbudget`:
  - `ContextBudgetEngine`
  - `BudgetGuard`
  - `BudgetPolicyViolationError`
- Existing module APIs are backward-compatible where used by tests:
  - `scan_repository(...)`
  - `score_files(...)`
  - `compress_ranked_files(...)`
  - `run_plan(...)`
  - `run_pack(...)`
- New optional parameters were added for config-aware behavior.

## Example `contextbudget.toml`

```toml
[scan]
include_globs = ["**/*.py", "**/*.md"]
ignore_globs = ["**/generated/**"]

[budget]
max_tokens = 28000
top_files = 30

[score]
critical_path_keywords = ["auth", "permissions"]

[compression]
summary_preview_lines = 10

[cache]
summary_cache_enabled = true
duplicate_hash_cache_enabled = true
cache_file = ".contextbudget_cache.json"
```
