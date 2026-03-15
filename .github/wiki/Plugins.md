# Plugins

Plugins let you change scoring, compression, and token estimation without changing the engine, CLI, or agent middleware. The plugin layer is explicit, local, and deterministic by default.

---

## Supported Plugin Types

| Type | Built-in default |
|------|-----------------|
| `ScorerPlugin` | `builtin.relevance` |
| `CompressorPlugin` | `builtin.default` |
| `TokenEstimatorPlugin` | `builtin.char4` |

---

## Registration Model

Redcon does not scan entry points or auto-discover packages. Every plugin must be registered in `redcon.toml` with a concrete `package.module:attribute` target.

```toml
[plugins]
scorer = "example.path_glob_bonus"
compressor = "example.leading_summary"
token_estimator = "builtin.char4"

[tokens]
backend = "heuristic"

[[plugins.registrations]]
target = "redcon.plugins.examples:path_glob_bonus_scorer"
options = { path_patterns = ["docs/**"], bonus = 8.0 }

[[plugins.registrations]]
target = "redcon.plugins.examples:leading_summary_compressor"
options = { preview_lines = 3 }
```

**Selection rules:**
- `[plugins].scorer` — chooses the active scorer
- `[plugins].compressor` — chooses the active compressor
- `[plugins].token_estimator` — chooses the active token estimator
- `[[plugins.registrations]]` — provides the import target plus plugin-specific `options`
- `[tokens]` can auto-select the matching built-in token estimator when `[plugins].token_estimator` is not set explicitly

---

## Scorer Plugins

```python
from redcon.plugins import ScorerPlugin


def score_custom(*, task, files, settings, options, estimate_tokens):
    # task: str — the natural-language task
    # files: list[FileRecord] — scanned files
    # settings: ScoreSettings
    # options: dict — from [[plugins.registrations]] options
    # estimate_tokens: callable
    # Returns: list[RankedFile]
    ...


custom_scorer = ScorerPlugin(
    name="acme.custom_scorer",
    score=score_custom,
    description="Custom deterministic scorer.",
)
```

Scorers receive the task, scanned `FileRecord` values, `ScoreSettings`, registration options, and the active token estimator. They must return `list[RankedFile]`.

---

## Compressor Plugins

```python
from redcon.plugins import CompressorPlugin


def compress_custom(
    *,
    task,
    repo,
    ranked_files,
    max_tokens,
    cache,
    settings,
    summarization_settings,
    options,
    estimate_tokens,
    duplicate_hash_cache_enabled,
):
    # ranked_files: list[RankedFile]
    # max_tokens: int — token budget
    # cache: summary cache backend
    # settings: CompressionSettings
    # summarization_settings: SummarizationSettings
    # Returns: CompressionResult
    ...


custom_compressor = CompressorPlugin(
    name="acme.custom_compressor",
    compress=compress_custom,
    description="Custom compression strategy.",
)
```

Compressors receive ranked files, the selected budget, the active cache backend, compression settings, summarization settings, registration options, and the active token estimator. They must return `CompressionResult`.

---

## Token Estimator Plugins

```python
from redcon.plugins import TokenEstimatorPlugin


def estimate_custom(*, text, options):
    return max(1, len(text.split()))


word_estimator = TokenEstimatorPlugin(
    name="acme.word_count",
    estimate=estimate_custom,
    description="Example word-count estimator.",
)
```

Token estimators can optionally expose a `describe(...)` callable so artifacts include backend metadata such as selected backend, effective backend, uncertainty, and fallback behavior.

---

## Built-in Examples

Redcon ships two minimal reference plugins in `redcon.plugins.examples`:

- `path_glob_bonus_scorer` — adds a score bonus to files matching specified path patterns
- `leading_summary_compressor` — compresses files to their leading N lines

These are intended as examples of the contract, not recommended production defaults.

---

## Artifact Recording

The engine records active implementations so downstream tooling can tell which extension path produced an artifact.

`plan` artifacts include:

```json
{
  "implementations": {
    "scorer": "builtin.relevance",
    "token_estimator": "builtin.char4"
  }
}
```

`pack` and `benchmark` artifacts include:

```json
{
  "implementations": {
    "scorer": "builtin.relevance",
    "compressor": "builtin.default",
    "token_estimator": "builtin.char4"
  }
}
```

Because agent middleware and workspace flows call the same engine, plugin selection behaves the same in CLI, library, and middleware usage.
