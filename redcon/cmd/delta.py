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

# Empirical Jaccard floor; below this the lines diverged enough that the
# delta header overhead beats any byte saving and we should ship absolute.
_MIN_JACCARD_FOR_DELTA = 0.30


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
