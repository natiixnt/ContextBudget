# Configuration

ContextBudget loads `contextbudget.toml` from repo root by default.

Precedence:
1. CLI flags
2. `contextbudget.toml`
3. built-in defaults

## Sections

- `[scan]`
- `[budget]`
- `[score]`
- `[compression]`
- `[cache]`
- `[telemetry]`

## Example

```toml
[scan]
include_globs = ["**/*.py", "**/*.md"]
ignore_globs = ["**/generated/**"]
max_file_size_bytes = 1500000

[budget]
max_tokens = 30000
top_files = 25

[score]
critical_path_keywords = ["auth", "permissions", "billing"]

[compression]
summary_preview_lines = 10

[cache]
summary_cache_enabled = true
duplicate_hash_cache_enabled = true

[telemetry]
enabled = false
sink = "file"
file_path = ".contextbudget/telemetry.jsonl"
```

Telemetry remains disabled by default and sends no network traffic.
