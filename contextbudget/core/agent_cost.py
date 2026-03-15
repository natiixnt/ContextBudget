from __future__ import annotations

"""Agent workflow cost estimation.

Maps model names to published input/output token prices and computes USD cost
estimates for simulated agent runs.

Prices are in **USD per 1 000 000 tokens** (i.e. per-MTok) — the standard
unit used by all major providers.  You can override any model's pricing or
supply fully custom prices via the ``price_per_1m_input`` / ``price_per_1m_output``
parameters of :func:`compute_step_cost` and :func:`compute_workflow_cost`.

Adding a new model is a single-line change to ``BUILTIN_MODEL_PRICING``.
"""

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Pricing table
# Each entry: (input_usd_per_1m_tokens, output_usd_per_1m_tokens)
# Sources: public provider pricing pages, approximate as of early 2026.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_per_1m: float   # USD per 1 000 000 input tokens
    output_per_1m: float  # USD per 1 000 000 output tokens
    provider: str = ""
    notes: str = ""


BUILTIN_MODEL_PRICING: dict[str, ModelPricing] = {
    # --- Anthropic ---
    "claude-opus-4-6":          ModelPricing(15.00,  75.00, provider="anthropic"),
    "claude-opus-4-5":          ModelPricing(15.00,  75.00, provider="anthropic"),
    "claude-opus-4":            ModelPricing(15.00,  75.00, provider="anthropic"),
    "claude-sonnet-4-6":        ModelPricing( 3.00,  15.00, provider="anthropic"),
    "claude-sonnet-4-5":        ModelPricing( 3.00,  15.00, provider="anthropic"),
    "claude-sonnet-4":          ModelPricing( 3.00,  15.00, provider="anthropic"),
    "claude-3-7-sonnet":        ModelPricing( 3.00,  15.00, provider="anthropic"),
    "claude-3-5-sonnet":        ModelPricing( 3.00,  15.00, provider="anthropic"),
    "claude-haiku-4-5":         ModelPricing( 0.80,   4.00, provider="anthropic"),
    "claude-3-5-haiku":         ModelPricing( 0.80,   4.00, provider="anthropic"),
    "claude-3-haiku":           ModelPricing( 0.25,   1.25, provider="anthropic"),
    # --- OpenAI ---
    "gpt-4o":                   ModelPricing( 2.50,  10.00, provider="openai"),
    "gpt-4o-mini":              ModelPricing( 0.15,   0.60, provider="openai"),
    "gpt-4.1":                  ModelPricing( 2.00,   8.00, provider="openai"),
    "gpt-4.1-mini":             ModelPricing( 0.40,   1.60, provider="openai"),
    "gpt-4.1-nano":             ModelPricing( 0.10,   0.40, provider="openai"),
    "o3":                       ModelPricing(10.00,  40.00, provider="openai"),
    "o4-mini":                  ModelPricing( 1.10,   4.40, provider="openai"),
    "o3-mini":                  ModelPricing( 1.10,   4.40, provider="openai"),
    "gpt-4-turbo":              ModelPricing(10.00,  30.00, provider="openai"),
    # --- Google ---
    "gemini-2-0-flash":         ModelPricing( 0.10,   0.40, provider="google"),
    "gemini-2-5-pro":           ModelPricing( 1.25,  10.00, provider="google"),
    "gemini-1-5-pro":           ModelPricing( 1.25,   5.00, provider="google"),
    "gemini-1-5-flash":         ModelPricing( 0.075,  0.30, provider="google"),
    # --- Mistral ---
    "mistral-large":            ModelPricing( 2.00,   6.00, provider="mistral"),
    "mistral-small":            ModelPricing( 0.20,   0.60, provider="mistral"),
    "codestral":                ModelPricing( 0.20,   0.60, provider="mistral"),
}

# Aliases / common short names
_ALIASES: dict[str, str] = {
    "opus":        "claude-opus-4-6",
    "sonnet":      "claude-sonnet-4-6",
    "haiku":       "claude-haiku-4-5",
    "claude":      "claude-sonnet-4-6",
    "claude-3":    "claude-3-5-sonnet",
    "claude-3-5":  "claude-3-5-sonnet",
    "claude-3-7":  "claude-3-7-sonnet",
    "gpt4o":       "gpt-4o",
    "gpt-4o-mini-latest": "gpt-4o-mini",
    "o1":          "o3",          # treat deprecated o1 as o3-tier
}


def resolve_model_pricing(
    model: str,
    *,
    price_per_1m_input: Optional[float] = None,
    price_per_1m_output: Optional[float] = None,
) -> ModelPricing:
    """Return a ModelPricing for *model*, with optional per-call overrides.

    Resolution order:
      1. If both price_per_1m_input and price_per_1m_output are given, use them.
      2. Exact key match in BUILTIN_MODEL_PRICING.
      3. Alias lookup.
      4. Prefix / substring match (first match wins, sorted by key length desc).
      5. Default: gpt-4o pricing with a note.
    """
    if price_per_1m_input is not None and price_per_1m_output is not None:
        return ModelPricing(
            input_per_1m=float(price_per_1m_input),
            output_per_1m=float(price_per_1m_output),
            provider="custom",
        )

    key = model.lower().strip()

    if key in BUILTIN_MODEL_PRICING:
        pricing = BUILTIN_MODEL_PRICING[key]
    elif key in _ALIASES:
        pricing = BUILTIN_MODEL_PRICING[_ALIASES[key]]
    else:
        # Try prefix/substring: find all table entries that appear in the model name
        candidates = sorted(
            (k for k in BUILTIN_MODEL_PRICING if k in key or key in k),
            key=len,
            reverse=True,  # longest match first
        )
        if candidates:
            pricing = BUILTIN_MODEL_PRICING[candidates[0]]
        else:
            pricing = ModelPricing(
                input_per_1m=2.50,
                output_per_1m=10.00,
                provider="unknown",
                notes=f"model '{model}' not in pricing table; defaulted to gpt-4o rates",
            )

    # Apply partial overrides
    if price_per_1m_input is not None or price_per_1m_output is not None:
        return ModelPricing(
            input_per_1m=float(price_per_1m_input) if price_per_1m_input is not None else pricing.input_per_1m,
            output_per_1m=float(price_per_1m_output) if price_per_1m_output is not None else pricing.output_per_1m,
            provider=pricing.provider,
            notes=pricing.notes + " (partially overridden)" if pricing.notes else "(partially overridden)",
        )
    return pricing


def _tokens_to_usd(tokens: int, price_per_1m: float) -> float:
    return (tokens / 1_000_000) * price_per_1m


def compute_step_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    pricing: ModelPricing,
) -> dict:
    """Return a per-step cost breakdown dict (all USD values)."""
    input_cost = _tokens_to_usd(input_tokens, pricing.input_per_1m)
    output_cost = _tokens_to_usd(output_tokens, pricing.output_per_1m)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_cost_usd": round(input_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "step_cost_usd": round(input_cost + output_cost, 6),
    }


def compute_workflow_cost(
    *,
    model: str = "gpt-4o",
    steps: list[dict],
    total_tokens: int,
    total_output_tokens: int,
    price_per_1m_input: Optional[float] = None,
    price_per_1m_output: Optional[float] = None,
) -> dict:
    """Compute cost breakdown for the full workflow simulation.

    Parameters
    ----------
    model:
        Model name used to look up pricing.
    steps:
        Per-step simulation dicts (as produced by simulate_agent_workflow).
        Each must have ``step_total_tokens``, ``output_tokens``,
        ``context_tokens``, ``prompt_tokens``.
    total_tokens:
        Pre-computed sum across all steps.
    total_output_tokens:
        Pre-computed sum of output tokens across all steps.
    price_per_1m_input / price_per_1m_output:
        Optional custom overrides (USD per 1M tokens).

    Returns
    -------
    dict with fields:
        model, provider, input_per_1m, output_per_1m,
        total_input_tokens, total_output_tokens,
        total_cost_usd, total_input_cost_usd, total_output_cost_usd,
        min_step_cost_usd, max_step_cost_usd, avg_step_cost_usd,
        steps_cost (list of per-step cost dicts),
        notes (list of str)
    """
    pricing = resolve_model_pricing(
        model,
        price_per_1m_input=price_per_1m_input,
        price_per_1m_output=price_per_1m_output,
    )

    total_input_tokens = total_tokens - total_output_tokens
    steps_cost: list[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        s_out = int(step.get("output_tokens", 0) or 0)
        s_total = int(step.get("step_total_tokens", 0) or 0)
        s_in = max(0, s_total - s_out)
        step_cost = compute_step_cost(
            input_tokens=s_in,
            output_tokens=s_out,
            pricing=pricing,
        )
        step_cost["step_id"] = str(step.get("id", ""))
        step_cost["step_title"] = str(step.get("title", ""))
        steps_cost.append(step_cost)

    total_input_cost = _tokens_to_usd(total_input_tokens, pricing.input_per_1m)
    total_output_cost = _tokens_to_usd(total_output_tokens, pricing.output_per_1m)
    total_cost = total_input_cost + total_output_cost

    step_costs_usd = [s["step_cost_usd"] for s in steps_cost]
    min_step_cost = min(step_costs_usd) if step_costs_usd else 0.0
    max_step_cost = max(step_costs_usd) if step_costs_usd else 0.0
    avg_step_cost = total_cost / len(step_costs_usd) if step_costs_usd else 0.0

    notes: list[str] = []
    if pricing.notes:
        notes.append(pricing.notes)

    return {
        "model": model,
        "provider": pricing.provider,
        "input_per_1m_usd": pricing.input_per_1m,
        "output_per_1m_usd": pricing.output_per_1m,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_input_cost_usd": round(total_input_cost, 6),
        "total_output_cost_usd": round(total_output_cost, 6),
        "total_cost_usd": round(total_cost, 6),
        "min_step_cost_usd": round(min_step_cost, 6),
        "max_step_cost_usd": round(max_step_cost, 6),
        "avg_step_cost_usd": round(avg_step_cost, 6),
        "steps_cost": steps_cost,
        "notes": notes,
    }


def list_known_models() -> list[dict]:
    """Return a sorted list of all models in the built-in pricing table."""
    rows = []
    for name, p in sorted(BUILTIN_MODEL_PRICING.items()):
        rows.append({
            "model": name,
            "provider": p.provider,
            "input_per_1m_usd": p.input_per_1m,
            "output_per_1m_usd": p.output_per_1m,
        })
    return rows
