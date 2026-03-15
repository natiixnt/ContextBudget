# Configuration

Redcon loads `redcon.toml` from the repo root by default. Workspace runs load shared config directly from the workspace TOML unless `--config` overrides it.

**Precedence:**
1. CLI flags
2. `redcon.toml`
3. Built-in defaults

---

## Full Example

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
enable_import_graph_signals = true

[model]
# Optional overrides for custom/self-hosted profiles:
# tokenizer = "llama-bpe"
# context_window = 65536
# recommended_compression_strategy = "aggressive"
# output_reserve_tokens = 8192

[compression]
summary_preview_lines = 10
symbol_extraction_enabled = true
snippet_score_threshold = 2.5

[summarization]
backend = "deterministic"   # or "external"
adapter = ""

[tokens]
backend = "heuristic"       # "heuristic", "model_aligned", or "exact"
model = "gpt-4o-mini"
encoding = ""
fallback_backend = "heuristic"

[plugins]
scorer = "builtin.relevance"
compressor = "builtin.default"
token_estimator = "builtin.char4"

[[plugins.registrations]]
target = "example.custom:scorer"
options = { bonus = 8.0 }

[cache]
backend = "local_file"      # "local_file", "shared_stub", or "memory"
summary_cache_enabled = true
cache_file = ".redcon_cache.json"
duplicate_hash_cache_enabled = true

[telemetry]
enabled = false
sink = "file"
file_path = ".redcon/telemetry.jsonl"
```

---

## Sections

### `[scan]`

| Key | Description |
|-----|-------------|
| `include_globs` | File patterns to include (e.g. `["**/*.py", "**/*.ts"]`) |
| `ignore_globs` | File patterns to exclude |
| `max_file_size_bytes` | Maximum file size to scan |
| `ignore_dirs` | Directory names to skip entirely |
| `binary_extensions` | Extensions treated as binary and skipped |

### `[budget]`

| Key | Description |
|-----|-------------|
| `max_tokens` | Default token budget for packing |
| `top_files` | Maximum candidate files per scoring pass |

### `[score]`

| Key | Description |
|-----|-------------|
| `critical_path_keywords` | Keywords that boost file scores |
| `enable_import_graph_signals` | Use import graph for scoring (default: `true`) |

### `[model]`

Optional overrides for custom or self-hosted LLM profiles.

| Key | Description |
|-----|-------------|
| `tokenizer` | Tokenizer assumption (e.g. `"llama-bpe"`) |
| `context_window` | Context window size |
| `recommended_compression_strategy` | `"expanded"`, `"balanced"`, or `"aggressive"` |
| `output_reserve_tokens` | Tokens reserved for model output |

### `[compression]`

| Key | Description |
|-----|-------------|
| `summary_preview_lines` | Lines shown in deterministic summaries |
| `symbol_extraction_enabled` | Extract named symbols (classes, functions) |
| `snippet_score_threshold` | Minimum score for snippet-only inclusion |

### `[summarization]`

| Key | Values | Description |
|-----|--------|-------------|
| `backend` | `"deterministic"`, `"external"` | Summary generation backend |
| `adapter` | string | Adapter name when `backend = "external"` |

### `[tokens]`

| Key | Values | Description |
|-----|--------|-------------|
| `backend` | `"heuristic"`, `"model_aligned"`, `"exact"` | Token estimation backend |
| `model` | string | Target model for `model_aligned` or `exact` |
| `encoding` | string | Explicit `tiktoken` encoding name for `exact` |
| `fallback_backend` | string | Safe fallback when `exact` is unavailable |

### `[cache]`

| Key | Values | Description |
|-----|--------|-------------|
| `backend` | `"local_file"`, `"shared_stub"`, `"memory"` | Cache backend |
| `summary_cache_enabled` | bool | Enable summary caching |
| `cache_file` | path | Path to cache file for `local_file` backend |
| `duplicate_hash_cache_enabled` | bool | Enable duplicate-read deduplication |

### `[telemetry]`

| Key | Description |
|-----|-------------|
| `enabled` | `false` by default; no network traffic emitted |
| `sink` | `"file"` |
| `file_path` | Path to write telemetry JSONL |

---

## Model Profiles

Set `model_profile` at the top level to enable model-aware packing defaults.

```toml
model_profile = "gpt-4.1"
```

Available profiles:

| Profile | Description |
|---------|-------------|
| `gpt-4.1` | GPT-4.1 / GPT-4o family |
| `claude-sonnet-4` | Claude Sonnet 4 family |
| `mistral-small` | Mistral models |
| `local-llm` | Self-hosted LLMs (customize via `[model]`) |

When a profile is active, Redcon automatically:
- selects a matching token-estimation backend
- scales the default `max_tokens` to the model context window
- clamps oversized budgets to the context window
- adjusts compression defaults (`expanded`, `balanced`, or `aggressive`)

**Custom local LLM:**

```toml
model_profile = "local-llm"

[model]
tokenizer = "llama-bpe"
context_window = 65536
recommended_compression_strategy = "aggressive"
output_reserve_tokens = 8192
```

---

## Token Estimation Backends

| Backend | Description |
|---------|-------------|
| `heuristic` | Default char/4 estimator. Fastest and deterministic. |
| `model_aligned` | Deterministic model-family approximation. |
| `exact` | Exact local tokenization via `tiktoken`. Requires `pip install -e .[tokenizers]`. |

---

## Incremental Scan Index

Redcon automatically stores a scan index at `.redcon/scan-index.json`. The index records file path, size, mtime, content hash, and scan classification metadata so unchanged files can reuse prior scan results across `plan`, `pack`, `benchmark`, and `watch`. Deleted files are pruned on the next refresh.

---

## Workspace TOML

Workspace files describe multiple repositories or monorepo packages. Top-level config sections are shared across the workspace; `[[repos]]` entries define scan roots with optional per-repo overrides.

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

**Rules:**
- `path` is resolved relative to the workspace TOML file.
- `label` must be unique; it namespaces artifact paths like `auth-service:src/auth.py`.
- Repo-specific `include_globs` replace shared `scan.include_globs` for that repo.
- Repo-specific `ignore_globs` are added on top of shared `scan.ignore_globs`.
