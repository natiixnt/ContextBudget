"""
Snapshot-delta framework for compressor outputs (V47).

When the agent runs the same argv twice in one process and the second
result is similar to the first (cache MISS but high overlap), we ship
only the delta against the first.

The framework is a process-local registry keyed on
(rewriter-canonical-argv, cwd-canonical-string) and storing the most
recent (raw_text, formatted_text). Both are kept so a delta renderer
can choose either: line-level diff over the formatted text is the
default; subclasses can do schema-aware deltas.

The pipeline always picks `min(cost_delta, cost_abs)` so the feature is
non-regressive by construction. Determinism is preserved: the registry
is process-local and the output for any single call only depends on
the prior baseline (deterministic by argv+cwd).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Empirical Jaccard floor; below this the lines diverged enough that the
# delta header overhead beats any byte saving and we should ship absolute.
_MIN_JACCARD_FOR_DELTA = 0.30

# Schema-specific delta renderers. Each takes (baseline_raw, current_raw)
# and returns the structured delta text. Falling back to line-delta when
# the schema is not registered is intentional - the min-gate guards
# regressions either way.
_SCHEMA_RENDERERS: dict[str, Callable[[str, str], str]] = {}


@dataclass(frozen=True, slots=True)
class DeltaBaseline:
    raw_text: str
    formatted_text: str
    schema: str


_BASELINE: dict[tuple[tuple[str, ...], str], DeltaBaseline] = {}


def reset_baselines() -> None:
    """Drop all process-local snapshot baselines. Used by tests."""
    _BASELINE.clear()


def _key(argv: tuple[str, ...], cwd: Path) -> tuple[tuple[str, ...], str]:
    return argv, str(cwd.resolve())


def get_baseline(argv: tuple[str, ...], cwd: Path) -> DeltaBaseline | None:
    return _BASELINE.get(_key(argv, cwd))


def store_baseline(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    raw_text: str,
    formatted_text: str,
    schema: str,
) -> None:
    _BASELINE[_key(argv, cwd)] = DeltaBaseline(
        raw_text=raw_text,
        formatted_text=formatted_text,
        schema=schema,
    )


def render_line_delta(
    schema: str,
    *,
    baseline_formatted: str,
    current_formatted: str,
) -> str:
    """Compact line-level delta between two compact-tier outputs.

    Format:
      delta vs prior <schema>:
      = <unchanged-count> lines unchanged
      + <inserted line>
      - <removed line>

    Always emits the head; the body lists only added/removed lines.
    Lines surviving in both are summarised by count.
    """
    base_lines = baseline_formatted.splitlines()
    cur_lines = current_formatted.splitlines()
    base_set = set(base_lines)
    cur_set = set(cur_lines)
    added = [ln for ln in cur_lines if ln not in base_set]
    removed = [ln for ln in base_lines if ln not in cur_set]
    unchanged = len(cur_lines) - len(added)
    parts = [f"delta vs prior {schema}: {unchanged} unchanged"]
    if added:
        parts.append(f"+{len(added)}:")
        parts.extend(f"+ {ln}" for ln in added[:30])
        if len(added) > 30:
            parts.append(f"+ ... ({len(added) - 30} more)")
    if removed:
        parts.append(f"-{len(removed)}:")
        parts.extend(f"- {ln}" for ln in removed[:30])
        if len(removed) > 30:
            parts.append(f"- ... ({len(removed) - 30} more)")
    return "\n".join(parts)


def jaccard(a: str, b: str) -> float:
    """Line-set Jaccard of two formatted outputs. Cheap diff-vs-noise gate."""
    sa = set(a.splitlines())
    sb = set(b.splitlines())
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def jaccard_above_floor(a: str, b: str) -> bool:
    return jaccard(a, b) >= _MIN_JACCARD_FOR_DELTA


def register_schema_renderer(
    schema: str, renderer: Callable[[str, str], str]
) -> None:
    """Wire a schema-specific delta renderer. Idempotent."""
    _SCHEMA_RENDERERS[schema] = renderer


def render_delta_for_schema(
    schema: str,
    *,
    baseline: DeltaBaseline,
    current_formatted: str,
    current_raw: str,
) -> str:
    """Pick a structured renderer for the schema or fall back to line-delta.

    Lazily ensures known structured renderers are registered before
    consulting the registry; otherwise the fallback is the generic
    line-level diff.
    """
    _ensure_default_renderers()
    fn = _SCHEMA_RENDERERS.get(schema)
    if fn is not None:
        try:
            structured = fn(baseline.raw_text, current_raw)
        except Exception:  # pragma: no cover - defensive
            structured = ""
        # Convention: a structured renderer returns "" when its parser
        # could not extract anything useful (e.g. caller passed formatted
        # text instead of raw, or the schema is malformed). Fall through
        # to line-delta on that signal.
        if structured:
            return structured
    return render_line_delta(
        schema,
        baseline_formatted=baseline.formatted_text,
        current_formatted=current_formatted,
    )


_RENDERERS_LOADED = False


def _ensure_default_renderers() -> None:
    global _RENDERERS_LOADED
    if _RENDERERS_LOADED:
        return
    _RENDERERS_LOADED = True
    # Lazy import: each compressor module registers its own renderer
    # at module import time. We trigger imports here so the registry
    # is populated even if the compressor was loaded lazily before.
    from redcon.cmd.compressors import git_diff as _gd  # noqa: F401
    from redcon.cmd.compressors import pytest_compressor as _pc  # noqa: F401
    from redcon.cmd.compressors import coverage_compressor as _cc  # noqa: F401
