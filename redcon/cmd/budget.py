"""
Deterministic compression-level selection.

Given a budget hint and the raw token count of an output, pick the highest-quality
compression level that fits. Pure function, no heuristics that drift between runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from redcon.cmd.types import CompressionLevel


@dataclass(frozen=True, slots=True)
class BudgetHint:
    """
    How much budget the agent has left and the floor of acceptable quality.

    `quality_floor` defaults to ULTRA (no guarantee) so the library picks
    whichever level fits. Higher-level callers (MCP, CLI) explicitly raise
    the floor when they want to protect quality.

    `prefer_compact_output` when True asks the runner to rewrite known
    argv to runner-native compact flags (e.g. pytest --tb=line) before
    spawning. Trades a small amount of detail for ~60-80% upstream
    reduction on test-failure runs.
    """

    remaining_tokens: int
    max_output_tokens: int
    quality_floor: CompressionLevel = CompressionLevel.ULTRA
    prefer_compact_output: bool = False


# Approximate output size as a fraction of raw tokens for each level.
# Calibrated against typical git/test/grep outputs - tuned in M9 benchmarks.
_VERBOSE_RATIO = 1.0
_COMPACT_RATIO = 0.15
# Fraction of remaining-budget we are willing to spend on a single command output.
_BUDGET_SHARE = 0.30


def select_level(raw_tokens: int, hint: BudgetHint) -> CompressionLevel:
    """
    Choose a compression level deterministically.

    For each level we estimate the output size (raw * level_ratio) and pick
    the most verbose level whose estimated output fits both:
      - the per-call hard cap (`max_output_tokens`)
      - the per-call share of remaining context budget (`_BUDGET_SHARE * remaining`)

    ULTRA is the always-fits fallback. The result is then clamped UP to
    `quality_floor` so callers can guarantee a minimum detail level.
    """
    if raw_tokens <= 0:
        return _at_least(CompressionLevel.VERBOSE, hint.quality_floor)
    if hint.remaining_tokens <= 0 or hint.max_output_tokens <= 0:
        return _at_least(CompressionLevel.ULTRA, hint.quality_floor)

    budget_cap = max(1, int(hint.remaining_tokens * _BUDGET_SHARE))
    hard_cap = hint.max_output_tokens

    if _fits(raw_tokens, _VERBOSE_RATIO, budget_cap, hard_cap):
        chosen = CompressionLevel.VERBOSE
    elif _fits(raw_tokens, _COMPACT_RATIO, budget_cap, hard_cap):
        chosen = CompressionLevel.COMPACT
    else:
        chosen = CompressionLevel.ULTRA

    return _at_least(chosen, hint.quality_floor)


def _fits(raw_tokens: int, ratio: float, budget_cap: int, hard_cap: int) -> bool:
    estimated = max(1, int(raw_tokens * ratio))
    return estimated <= budget_cap and estimated <= hard_cap


_LEVEL_RANK = {
    CompressionLevel.ULTRA: 0,
    CompressionLevel.COMPACT: 1,
    CompressionLevel.VERBOSE: 2,
}


def _at_least(chosen: CompressionLevel, floor: CompressionLevel) -> CompressionLevel:
    """Return whichever level is more verbose."""
    if _LEVEL_RANK[chosen] >= _LEVEL_RANK[floor]:
        return chosen
    return floor
