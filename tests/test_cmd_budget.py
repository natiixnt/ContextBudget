"""Tests for redcon.cmd.budget - deterministic compression-level selection."""

from __future__ import annotations

import pytest

from redcon.cmd.budget import BudgetHint, select_level
from redcon.cmd.types import CompressionLevel


@pytest.mark.parametrize(
    "raw,remaining,cap,expected",
    [
        # Tiny output fits verbose anywhere.
        (100, 10_000, 4_000, CompressionLevel.VERBOSE),
        # 5k raw -> verbose=5000 doesn't fit cap 4000, but compact=750 does.
        (5_000, 10_000, 4_000, CompressionLevel.COMPACT),
        # 50k raw -> compact=7500 still over cap=4000, falls to ULTRA.
        (50_000, 10_000, 4_000, CompressionLevel.ULTRA),
        # Zero remaining is the always-ULTRA edge case.
        (50_000, 0, 4_000, CompressionLevel.ULTRA),
    ],
)
def test_select_level_basic(raw: int, remaining: int, cap: int, expected: CompressionLevel):
    hint = BudgetHint(remaining_tokens=remaining, max_output_tokens=cap)
    assert select_level(raw, hint) == expected


def test_quality_floor_clamps_up():
    # raw is huge -> would normally pick ULTRA, but floor=VERBOSE keeps quality.
    hint = BudgetHint(
        remaining_tokens=10_000,
        max_output_tokens=4_000,
        quality_floor=CompressionLevel.VERBOSE,
    )
    assert select_level(50_000, hint) == CompressionLevel.VERBOSE


def test_quality_floor_does_not_downgrade():
    # raw is tiny, naturally VERBOSE; floor=COMPACT must not weaken it to COMPACT.
    hint = BudgetHint(
        remaining_tokens=10_000,
        max_output_tokens=4_000,
        quality_floor=CompressionLevel.COMPACT,
    )
    assert select_level(100, hint) == CompressionLevel.VERBOSE


def test_max_output_tokens_caps_verbose():
    # Even when 30% of remaining would allow verbose, max_output is the hard cap.
    hint = BudgetHint(remaining_tokens=10_000, max_output_tokens=50)
    # raw 1000 > 30% of 50 -> not verbose; > 4 * 10% of 50 -> ultra.
    assert select_level(1_000, hint) == CompressionLevel.ULTRA


def test_zero_remaining_returns_floor_or_ultra():
    hint = BudgetHint(
        remaining_tokens=0,
        max_output_tokens=100,
        quality_floor=CompressionLevel.COMPACT,
    )
    # remaining 0 -> ULTRA, but floor pulls it up to COMPACT.
    assert select_level(123, hint) == CompressionLevel.COMPACT
