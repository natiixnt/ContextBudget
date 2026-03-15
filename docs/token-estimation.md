# Token Estimation

ContextBudget uses token estimates for:

- pack budget enforcement
- policy checks
- benchmark comparisons

The default path stays lightweight and deterministic. No network access is required.

## Backends

### `heuristic`

- implementation: fixed `1 token ~= 4 chars`
- speed: fastest
- accuracy: lowest
- recommended when:
  - you want stable local automation
  - budget checks are coarse guardrails, not billing-grade accounting

### `model_aligned`

- implementation: deterministic character-ratio profiles tuned by model family
- speed: near-heuristic
- accuracy: usually closer than plain `char/4`, still approximate
- recommended when:
  - policy thresholds should better reflect a target model family
  - you need more trust than the baseline heuristic without adding an optional dependency

### `exact_tiktoken`

- implementation: exact local counts through optional `tiktoken`
- speed: slowest of the built-ins, but still local-only
- accuracy: highest when the model or encoding is supported
- recommended when:
  - token limits are tight
  - you want benchmark artifacts to reflect a specific tokenizer exactly
  - you are comfortable installing the optional tokenizer extra

If `exact_tiktoken` is selected and unavailable, ContextBudget falls back to `fallback_backend`
and records:

- selected backend
- effective backend
- fallback reason
- uncertainty level

## Config

```toml
[tokens]
backend = "heuristic"
model = "gpt-4o-mini"
encoding = ""
fallback_backend = "heuristic"
```

Examples:

```toml
[tokens]
backend = "model_aligned"
model = "gpt-4.1-mini"
```

```toml
[tokens]
backend = "exact"
model = "gpt-4o-mini"
fallback_backend = "model_aligned"
```

Install the optional exact tokenizer backend with:

```bash
python3 -m pip install -e .[tokenizers]
```

## Artifact Reporting

Plan, pack, report, and benchmark artifacts include a `token_estimator` block:

```json
{
  "token_estimator": {
    "selected_backend": "exact_tiktoken",
    "effective_backend": "model_aligned",
    "uncertainty": "approximate",
    "model": "gpt-4o-mini",
    "available": false,
    "fallback_used": true,
    "fallback_reason": "Optional dependency \"tiktoken\" is not installed."
  }
}
```

Artifacts also keep the resolved implementation name under `implementations.token_estimator`.

## Benchmark Samples

`contextbudget benchmark` now includes a compact `estimator_samples` section that compares
the built-in estimators on local sample text from the current run:

- the task text
- the top-ranked file
- the packed context payload

This is intended for relative comparison, not as a universal accuracy claim.
