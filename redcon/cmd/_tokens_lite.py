"""
Slim heuristic token estimator.

Every cmd compressor only needs the chars-per-4 heuristic that
`redcon.core.tokens.estimate_tokens` exposes. Importing the full
`tokens.py` pulls in extra logic (model-aligned profiles, optional
tiktoken plumbing, schema reports) that the compressor hot path never
touches. This module is the minimal surface kept on the cold path so
adding a new compressor doesn't drag in `redcon.core.tokens`.

API contract: `estimate_tokens(text)` returns the same value as
`redcon.core.tokens.estimate_tokens(text)` for any string. If the two
ever diverge a regression test will catch it.
"""

from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    """Heuristic token count: ceil(len / 4), with empty string -> 0."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
