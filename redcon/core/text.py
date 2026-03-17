from __future__ import annotations

"""Text normalization helpers used by scoring/compression stages."""

import re


_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_STOP = {
    # English stopwords + action verbs
    "add", "fix", "for", "the", "with", "to", "and", "api",
    "refactor", "change", "update", "use", "from", "into",
    "make", "move", "also", "when", "that", "this", "based",
    # Common short English words that are not technical terms
    "its", "per", "non", "via", "any", "few", "too", "yet",
    "now", "how", "why", "was", "has", "had", "but", "not",
    "all", "can", "may", "our", "let", "old", "the",
    # Ubiquitous Python identifiers - appear in virtually every file
    "file", "time", "self", "none", "true", "data",
}


def task_keywords(task: str) -> list[str]:
    """Extract deduplicated scoring keywords from a task description.

    Handles CamelCase / PascalCase identifiers by splitting them into their
    component words, so "UserService" produces both "userservice" and the
    individual parts "user" and "service".  3-char acronyms (JWT, SQL, CLI,
    SDK …) are preserved; 1-2 char noise is filtered.
    """
    pool: list[str] = []
    for raw in _WORD_RE.findall(task):
        lower = raw.lower()
        pool.append(lower)
        # Split CamelCase/PascalCase and add the individual parts too
        parts = _CAMEL_RE.split(raw)
        if len(parts) > 1:
            pool.extend(p.lower() for p in parts if len(p) >= 3)

    unique: list[str] = []
    for term in pool:
        if len(term) < 3:
            continue
        if term in _STOP:
            continue
        if term not in unique:
            unique.append(term)
    return unique[:16]


def clamp(value: float, low: float, high: float) -> float:
    """Clamp a numeric value to a closed range."""

    return max(low, min(value, high))
