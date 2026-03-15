from __future__ import annotations

"""Cost analytics engine for ContextBudget run artifacts.

Translates token savings reported in a run.json artifact into USD financial
savings using the same model pricing table used by the simulation engine.

Definitions
-----------
baseline_tokens
    Tokens that *would* have been sent without ContextBudget optimisation.
    Computed as ``estimated_input_tokens + estimated_saved_tokens`` from the
    run's budget report, i.e. the full uncompressed footprint before the
    compressor and file-skipping logic reduced it.

optimized_tokens
    Tokens actually sent — ``estimated_input_tokens`` from the budget report.

saved_tokens
    ``baseline_tokens - optimized_tokens`` (== ``estimated_saved_tokens``).

All costs are denominated in USD and use the input-token price only (context
is input; output tokens are the model's response, not tracked here).
"""

import json
from pathlib import Path
from typing import Optional

from contextbudget.core.agent_cost import (
    BUILTIN_MODEL_PRICING,
    ModelPricing,
    _tokens_to_usd,
    list_known_models,
    resolve_model_pricing,
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def load_run_data(run_json: str | Path) -> dict:
    """Load and return a run artifact from *run_json*."""
    path = Path(run_json)
    if not path.exists():
        raise FileNotFoundError(f"Run artifact not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _extract_budget(run_data: dict) -> dict:
    budget = run_data.get("budget", {})
    if not isinstance(budget, dict):
        budget = {}
    return budget


def _per_file_breakdown(run_data: dict, pricing: ModelPricing) -> list[dict]:
    """Return per-file cost breakdown from compressed_context entries."""
    rows: list[dict] = []
    for entry in run_data.get("compressed_context", []):
        if not isinstance(entry, dict):
            continue
        original = int(entry.get("original_tokens", 0) or 0)
        compressed = int(entry.get("compressed_tokens", 0) or 0)
        saved = max(0, original - compressed)
        rows.append({
            "path": entry.get("path", ""),
            "strategy": entry.get("strategy", ""),
            "original_tokens": original,
            "compressed_tokens": compressed,
            "saved_tokens": saved,
            "baseline_cost_usd": round(_tokens_to_usd(original, pricing.input_per_1m), 8),
            "optimized_cost_usd": round(_tokens_to_usd(compressed, pricing.input_per_1m), 8),
            "saved_cost_usd": round(_tokens_to_usd(saved, pricing.input_per_1m), 8),
        })
    return rows


def compute_cost_analysis(
    run_data: dict,
    *,
    model: str = "gpt-4o",
    price_per_1m_input: Optional[float] = None,
) -> dict:
    """Compute the cost analysis for a single run artifact.

    Parameters
    ----------
    run_data:
        Parsed run.json dict (from :func:`load_run_data`).
    model:
        Model name to look up input-token pricing for.
    price_per_1m_input:
        Override the input-token price (USD / 1 000 000 tokens).

    Returns
    -------
    dict with keys:
        model, provider, input_per_1m_usd,
        baseline_tokens, optimized_tokens, saved_tokens, savings_pct,
        baseline_cost_usd, optimized_cost_usd, saved_cost_usd,
        per_file (list of per-file breakdowns),
        run_meta (task, repo, generated_at, command, max_tokens),
        notes (list of str)
    """
    pricing = resolve_model_pricing(
        model,
        price_per_1m_input=price_per_1m_input,
        price_per_1m_output=None,
    )

    budget = _extract_budget(run_data)
    optimized_tokens = int(budget.get("estimated_input_tokens", 0) or 0)
    saved_tokens = int(budget.get("estimated_saved_tokens", 0) or 0)
    baseline_tokens = optimized_tokens + saved_tokens

    savings_pct = (
        round(saved_tokens / baseline_tokens * 100, 2) if baseline_tokens > 0 else 0.0
    )

    baseline_cost = _tokens_to_usd(baseline_tokens, pricing.input_per_1m)
    optimized_cost = _tokens_to_usd(optimized_tokens, pricing.input_per_1m)
    saved_cost = baseline_cost - optimized_cost

    per_file = _per_file_breakdown(run_data, pricing)

    notes: list[str] = []
    if pricing.notes:
        notes.append(pricing.notes)
    if saved_tokens == 0 and optimized_tokens == 0:
        notes.append("No token data found in budget report; costs are zero.")
    if baseline_tokens == optimized_tokens:
        notes.append("No savings recorded — context was not compressed or file skipping had no effect.")

    return {
        "model": model,
        "provider": pricing.provider,
        "input_per_1m_usd": pricing.input_per_1m,
        "baseline_tokens": baseline_tokens,
        "optimized_tokens": optimized_tokens,
        "saved_tokens": saved_tokens,
        "savings_pct": savings_pct,
        "baseline_cost_usd": round(baseline_cost, 6),
        "optimized_cost_usd": round(optimized_cost, 6),
        "saved_cost_usd": round(saved_cost, 6),
        "per_file": per_file,
        "run_meta": {
            "command": run_data.get("command", ""),
            "task": run_data.get("task", ""),
            "repo": run_data.get("repo", ""),
            "max_tokens": run_data.get("max_tokens", 0),
            "generated_at": run_data.get("generated_at", ""),
            "files_included": len(run_data.get("files_included", [])),
            "files_skipped": len(run_data.get("files_skipped", [])),
        },
        "notes": notes,
    }
