"""
Skeleton-based clustering helper, generic over T.

Used by failure-template grouping (test_format) and stack-trace
clustering (profiler). The idea: turn each item into a 'skeleton'
string by masking out values (numbers, hex ids, quoted strings, paths,
addresses) so semantically-equivalent items collapse to the same key.
First-seen order is preserved so output is deterministic.

Mask rules are tuples of (compiled_regex, replacement_str) applied
in order. Two prebuilt sets:
  - PYTHON_TRACE_MASK: hex addresses, ids, large ints, floats, quoted strings
  - SCALAR_MASK: a strict subset for short numeric IDs only

Callers can compose their own by passing a custom tuple.
"""

from __future__ import annotations

import re
from typing import Callable, TypeVar

T = TypeVar("T")

PYTHON_TRACE_MASK: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<id>"),
    (re.compile(r"\b\d+\.\d+\b"), "<f>"),
    (re.compile(r"\b\d{2,}\b"), "<n>"),
    (re.compile(r"'[^']*'"), "'<s>'"),
    (re.compile(r'"[^"]*"'), '"<s>"'),
)

SCALAR_MASK: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{2,}\b"), "<n>"),
)


def mask(text: str, rules: tuple[tuple[re.Pattern[str], str], ...]) -> str:
    out = text
    for pattern, repl in rules:
        out = pattern.sub(repl, out)
    return out


def cluster_by_skeleton(
    items: list[T],
    key: Callable[[T], str],
    rules: tuple[tuple[re.Pattern[str], str], ...] = PYTHON_TRACE_MASK,
) -> list[list[T]]:
    """Group items into buckets where mask(key(item)) is identical.

    Preserves first-seen order both for the buckets themselves and for
    items within a bucket. Two consecutive runs on identical input
    produce byte-identical output.
    """
    bucket: dict[str, list[T]] = {}
    order: list[str] = []
    for item in items:
        skel = mask(key(item), rules)
        if skel not in bucket:
            bucket[skel] = []
            order.append(skel)
        bucket[skel].append(item)
    return [bucket[k] for k in order]
