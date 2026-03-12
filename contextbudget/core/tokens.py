from __future__ import annotations

"""Token estimation helpers."""

import math


# Simple deterministic heuristic: 1 token ~= 4 chars.
def estimate_tokens(text: str) -> int:
    """Estimate approximate token count for text."""

    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
