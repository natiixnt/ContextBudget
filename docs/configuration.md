# Configuration

ContextBudget loads `contextbudget.toml` from repo root by default.
Workspace runs load shared config directly from the workspace TOML unless `--config` overrides it.

Precedence:
1. CLI flags
2. `contextbudget.toml`
3. built-in defaults

## Sections

- top-level `model_profile`
- `[scan]`
- `[budget]`
- `[score]`
- `[model]`
- `[compression]`
- `[summarization]`
- `[tokens]`
- `[plugins]`
- `[cache]`
- `[telemetry]`

## Example

```toml
model_profile = "gpt-4.1"

[scan]
include_globs = ["**/*.py", "**/*.md"]
ignore_globs = ["**/generated/**"]
max_file_size_bytes = 1500000

[budget]
max_tokens = 30000
top_files = 25

[score]
critical_path_keywords = ["auth", "permissions", "billing"]

[model]
# Optional overrides for custom/self-hosted profiles.
# tokenizer = "llama-bpe"
# context_window = 65536
# recommended_compression_strategy = "aggressive"
# output_reserve_tokens = 8192

[compression]
summary_preview_lines = 10

[summarization]
backend = "deterministic"
adapter = ""

[tokens]
backend = "heuristic"
model = "gpt-4o-mini"
encoding = ""
fallback_backend = "heuristic"

[plugins]
scorer = "builtin.relevance"
compressor = "builtin.default"
token_estimator = "builtin.char4"

[cache]
backend = "local_file"
summary_cache_enabled = true
cache_file = ".contextbudget_cache.json"
duplicate_hash_cache_enabled = true

[telemetry]
enabled = false
sink = "file"
file_path = ".contextbudget/telemetry.jsonl"
```

Telemetry remains disabled by default and sends no network traffic.

Plugin selection and explicit registration are documented in [Plugins](plugins.md).

## Model Profiles

`model_profile` enables built-in model-aware packing defaults. ContextBudget currently ships profile coverage for:

- GPT models
- Claude models
- Mistral models
- local/self-hosted LLMs

Each resolved profile carries:

- tokenizer assumption
- context window
- recommended compression strategy

When a profile is active, ContextBudget automatically:

- selects a matching token-estimation backend unless `[tokens]` or `[plugins].token_estimator` is explicitly set
- scales the default `max_tokens` budget to the model context window, leaving deterministic output headroom
- clamps oversized budgets to the resolved context window
- adjusts compression defaults toward `expanded`, `balanced`, or `aggressive` packing unless `[compression]` overrides are explicit

Examples:

```toml
model_profile = "gpt-4.1"
```

```toml
model_profile = "claude-sonnet-4"
```

```toml
model_profile = "mistral-small"
```

```toml
model_profile = "local-llm"

[model]
tokenizer = "llama-bpe"
context_window = 65536
recommended_compression_strategy = "aggressive"
output_reserve_tokens = 8192
```

Artifacts and Markdown reports include a `model_profile` block so downstream tooling can see the active assumptions.

## Token Estimation

- `backend = "heuristic"`: default char/4 estimator. Fastest and deterministic.
- `backend = "model_aligned"`: deterministic model-family approximation using `model`.
- `backend = "exact"`: exact local tokenization through `tiktoken` when available.
- `model`: target model family for `model_aligned` or `exact`.
- `encoding`: optional explicit `tiktoken` encoding name for `exact`.
- `fallback_backend`: safe deterministic fallback when `exact` is selected but unavailable.

When `[tokens]` is present and `[plugins].token_estimator` is not explicitly set, ContextBudget
automatically selects the matching built-in token-estimator plugin.

## Workspace TOML

Workspace files are local-only and can describe multiple repositories or monorepo packages.
Top-level config sections are shared across the workspace, and `[[repos]]` entries define scan roots plus optional per-repo include/exclude rules.

```toml
name = "backend-services"

[scan]
include_globs = ["**/*.py", "**/*.ts"]
ignore_globs = ["**/generated/**"]

[budget]
max_tokens = 28000
top_files = 30

[[repos]]
label = "auth-service"
path = "../auth-service"

[[repos]]
label = "billing-service"
path = "../billing-service"
ignore_globs = ["tests/fixtures/**"]

[[repos]]
label = "gateway"
path = "../platform/packages/gateway"
include_globs = ["src/**/*.ts"]
```

Rules:
- `path` is resolved relative to the workspace TOML file.
- `label` must be unique and is used to namespace artifact paths like `auth-service:src/auth.py`.
- Repo-specific `include_globs` replace shared `scan.include_globs` for that repo.
- Repo-specific `ignore_globs` are added on top of shared `scan.ignore_globs`.

## Incremental Scan Index

ContextBudget automatically stores a scan index at `.contextbudget/scan-index.json`.
The index records file path, size, mtime, content hash, and scan classification metadata
so unchanged files can reuse prior scan results across `plan`, `pack`, `benchmark`, and `watch`.
Deleted files are pruned from the index on the next refresh.

## Summarization

- `backend = "deterministic"`: default local summarizer with stable, reproducible output.
- `backend = "external"`: opt-in adapter path. Requires a registered adapter named by `adapter`.
- `adapter`: logical adapter name used for lookup and artifact reporting.

If an external adapter is selected but missing or failing, ContextBudget falls back automatically to deterministic summarization and records that fallback in `run.json` and Markdown reports.

## Cache Backends

- `backend = "local_file"`: default persistent cache stored in the repository.
- `backend = "shared_stub"`: no-op shared-cache stub for architecture testing. It performs no network calls and persists nothing.
- `backend = "memory"`: process-local backend intended for tests and embedders.
