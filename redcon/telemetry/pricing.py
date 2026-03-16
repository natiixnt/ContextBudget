# SPDX-License-Identifier: LicenseRef-Redcon-Commercial
# Copyright (c) 2026 nai. All rights reserved.
# See LICENSE-COMMERCIAL for terms.

from __future__ import annotations

"""Model pricing tables and token cost computation.

Pricing is expressed as USD per 1 million input tokens (the dominant cost
driver when measuring context optimisation savings).  Output token pricing is
included for completeness but is not used in savings calculations because
Redcon only optimises the *input* context.

Prices are approximate list prices as of early 2026.  Teams operating under
negotiated or volume-discount pricing can override the defaults by passing a
custom ``model_pricing`` dict to :func:`compute_run_costs`.
"""

from typing import Any

# ---------------------------------------------------------------------------
# Pricing table
# ---------------------------------------------------------------------------

#: input_per_million  – USD per 1 M input tokens
#: output_per_million – USD per 1 M output tokens (informational only)
#: display_name       – human-readable model name shown in reports
MODEL_PRICING: dict[str, dict[str, Any]] = {
    # ── Anthropic Claude ─────────────────────────────────────────────────────
    "claude-opus-4-6": {
        "input_per_million": 15.00,
        "output_per_million": 75.00,
        "display_name": "Claude Opus 4.6",
    },
    "claude-sonnet-4-6": {
        "input_per_million": 3.00,
        "output_per_million": 15.00,
        "display_name": "Claude Sonnet 4.6",
    },
    "claude-haiku-4-5-20251001": {
        "input_per_million": 0.80,
        "output_per_million": 4.00,
        "display_name": "Claude Haiku 4.5",
    },
    "claude-3-5-sonnet-20241022": {
        "input_per_million": 3.00,
        "output_per_million": 15.00,
        "display_name": "Claude 3.5 Sonnet",
    },
    "claude-3-5-haiku-20241022": {
        "input_per_million": 0.80,
        "output_per_million": 4.00,
        "display_name": "Claude 3.5 Haiku",
    },
    "claude-3-opus-20240229": {
        "input_per_million": 15.00,
        "output_per_million": 75.00,
        "display_name": "Claude 3 Opus",
    },
    "claude-3-haiku-20240307": {
        "input_per_million": 0.25,
        "output_per_million": 1.25,
        "display_name": "Claude 3 Haiku",
    },
    # ── OpenAI GPT ────────────────────────────────────────────────────────────
    "gpt-4o": {
        "input_per_million": 2.50,
        "output_per_million": 10.00,
        "display_name": "GPT-4o",
    },
    "gpt-4o-mini": {
        "input_per_million": 0.15,
        "output_per_million": 0.60,
        "display_name": "GPT-4o mini",
    },
    "gpt-4-turbo": {
        "input_per_million": 10.00,
        "output_per_million": 30.00,
        "display_name": "GPT-4 Turbo",
    },
    "gpt-4": {
        "input_per_million": 30.00,
        "output_per_million": 60.00,
        "display_name": "GPT-4",
    },
    "gpt-3.5-turbo": {
        "input_per_million": 0.50,
        "output_per_million": 1.50,
        "display_name": "GPT-3.5 Turbo",
    },
    "o1": {
        "input_per_million": 15.00,
        "output_per_million": 60.00,
        "display_name": "OpenAI o1",
    },
    "o1-mini": {
        "input_per_million": 3.00,
        "output_per_million": 12.00,
        "display_name": "OpenAI o1-mini",
    },
    # ── Google Gemini ─────────────────────────────────────────────────────────
    "gemini-1.5-pro": {
        "input_per_million": 1.25,
        "output_per_million": 5.00,
        "display_name": "Gemini 1.5 Pro",
    },
    "gemini-1.5-flash": {
        "input_per_million": 0.075,
        "output_per_million": 0.30,
        "display_name": "Gemini 1.5 Flash",
    },
    "gemini-2.0-flash": {
        "input_per_million": 0.10,
        "output_per_million": 0.40,
        "display_name": "Gemini 2.0 Flash",
    },
    "gemini-2.5-pro": {
        "input_per_million": 1.25,
        "output_per_million": 10.00,
        "display_name": "Gemini 2.5 Pro",
    },
}

DEFAULT_MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------


def get_pricing(model: str, custom: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return the pricing entry for *model*, falling back to the default model.

    Parameters
    ----------
    model:
        Model identifier (key in :data:`MODEL_PRICING`).
    custom:
        Optional dict of additional or override pricing entries in the same
        shape as :data:`MODEL_PRICING`.  Takes precedence over built-ins.

    Returns
    -------
    dict
        ``{"input_per_million": float, "output_per_million": float, "display_name": str}``
    """
    table = {**MODEL_PRICING, **(custom or {})}
    entry = table.get(model) or table.get(DEFAULT_MODEL) or {}
    return {
        "input_per_million": float(entry.get("input_per_million", 3.00)),
        "output_per_million": float(entry.get("output_per_million", 15.00)),
        "display_name": str(entry.get("display_name", model or DEFAULT_MODEL)),
    }


def tokens_to_usd(tokens: int, input_per_million: float) -> float:
    """Convert a token count to USD using the given per-million-token rate."""
    if tokens <= 0 or input_per_million <= 0:
        return 0.0
    return tokens / 1_000_000 * input_per_million


def compute_run_costs(
    baseline_tokens: int,
    optimized_tokens: int,
    model: str = DEFAULT_MODEL,
    custom_pricing: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute baseline cost, optimised cost, and savings for a single run.

    Parameters
    ----------
    baseline_tokens:
        Token count for the full, un-optimised context.
    optimized_tokens:
        Token count after Redcon optimisation.
    model:
        Model identifier used to look up the per-token price.
    custom_pricing:
        Optional override pricing table (same shape as :data:`MODEL_PRICING`).

    Returns
    -------
    dict
        ``{baseline_cost_usd, optimized_cost_usd, savings_usd, savings_pct,
           model, input_per_million, baseline_tokens, optimized_tokens,
           tokens_saved}``
    """
    pricing = get_pricing(model, custom_pricing)
    rate = pricing["input_per_million"]

    baseline_cost = tokens_to_usd(baseline_tokens, rate)
    optimized_cost = tokens_to_usd(optimized_tokens, rate)
    savings_usd = max(0.0, baseline_cost - optimized_cost)
    tokens_saved = max(0, baseline_tokens - optimized_tokens)
    savings_pct = round(savings_usd / baseline_cost, 6) if baseline_cost > 0 else 0.0

    return {
        "model": model,
        "display_name": pricing["display_name"],
        "input_per_million": rate,
        "baseline_tokens": baseline_tokens,
        "optimized_tokens": optimized_tokens,
        "tokens_saved": tokens_saved,
        "baseline_cost_usd": round(baseline_cost, 8),
        "optimized_cost_usd": round(optimized_cost, 8),
        "savings_usd": round(savings_usd, 8),
        "savings_pct": savings_pct,
    }


__all__ = [
    "DEFAULT_MODEL",
    "MODEL_PRICING",
    "compute_run_costs",
    "get_pricing",
    "tokens_to_usd",
]
