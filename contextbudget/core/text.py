from __future__ import annotations

"""Text normalization helpers used by scoring/compression stages."""

import re


_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{1,}")


def task_keywords(task: str) -> list[str]:
    """Extract deduplicated scoring keywords from a task description."""

    terms = [w.lower() for w in _WORD_RE.findall(task)]
    stop = {
        "add",
        "fix",
        "for",
        "the",
        "with",
        "to",
        "and",
        "api",
        "refactor",
        "change",
        "update",
        "use",
        "from",
        "into",
    }
    unique: list[str] = []
    for term in terms:
        if term in stop:
            continue
        if term not in unique:
            unique.append(term)
    return unique[:12]


def clamp(value: float, low: float, high: float) -> float:
    """Clamp a numeric value to a closed range."""

    return max(low, min(value, high))
