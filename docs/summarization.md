# Summarization

ContextBudget stays deterministic and local by default. Summary compression uses a built-in summarizer unless you explicitly select an external adapter.

## Default Behavior

- `backend = "deterministic"` is the default.
- Summaries are generated locally from repository content with no model call, no network dependency, and stable fallback behavior.
- This path is the safest choice for CI, policy gates, and reproducible comparisons.

## External Adapter Path

External summarization is opt-in and adapter-based. The OSS project does not ship any vendor client or hosted integration.

```toml
[summarization]
backend = "external"
adapter = "team-summary"
```

An external adapter must be registered by wrapper code before `pack` runs:

```python
from contextbudget import ExternalSummaryAdapter, register_external_summarizer_adapter


class TeamSummaryAdapter(ExternalSummaryAdapter):
    name = "team-summary"

    def summarize(self, request) -> str:
        return f"summary for {request.path}"


register_external_summarizer_adapter("team-summary", TeamSummaryAdapter())
```

If the adapter is missing, fails, or returns unusable output, ContextBudget automatically falls back to deterministic summarization.

## Artifact Signals

`run.json` includes a top-level `summarizer` block:

```json
{
  "summarizer": {
    "selected_backend": "external",
    "external_adapter": "team-summary",
    "effective_backend": "deterministic",
    "external_configured": true,
    "external_resolved": true,
    "fallback_used": true,
    "fallback_count": 1,
    "summary_count": 1,
    "logs": [
      "External summarizer adapter 'team-summary' failed for src/large.py (RuntimeError: boom). Falling back to deterministic summary."
    ]
  }
}
```

Markdown pack/report outputs include the same selection and fallback details.

## Trust Boundaries

External summarization changes the trust boundary. Even if the rest of ContextBudget stays local-first, the summary text may now depend on software outside the deterministic core.

- Use deterministic summarization when repository content must stay fully local.
- Only use an external adapter when the adapter operator is allowed to process the same source material as the developer or CI environment.
- Treat summaries as code-adjacent data. They can expose implementation details even when compressed.
- OSS ContextBudget includes no hidden telemetry and no built-in network summarizer.

## Reproducibility Tradeoffs

Deterministic summaries are easier to diff, cache, and policy-check. External summaries may improve quality, but they can reduce repeatability because output depends on adapter behavior outside the built-in heuristics.

- Deterministic path: strongest reproducibility, simplest cache semantics, easiest CI enforcement.
- External path: potentially better semantic compression on large or low-signal files, but output stability depends on the adapter implementation.
- Automatic fallback prevents hard failures, but a fallback run is not identical to a successful external-summary run. The artifact makes that explicit.

## When External Summarization Is Worth Using

Use an external adapter when:

- large files contain important behavior that deterministic leading-line previews miss
- a team already operates a trusted summarization component
- humans will inspect `run.json` or Markdown output and can tolerate some summary variability

Stay deterministic when:

- CI or policy enforcement depends on highly repeatable artifacts
- code cannot cross a broader trust boundary
- summary quality is already sufficient from built-in preview heuristics
