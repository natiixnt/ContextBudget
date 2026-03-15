from __future__ import annotations

"""Agent Observability Layer — analyze and record agent run metrics.

Reads a pack run artifact (run.json) and produces a structured report
covering tokens used, files read, duplicate reads, cache hits, context
size, and run duration.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentRunMetrics:
    """Metrics recorded for a single agent run."""

    # Identity
    command: str
    run_json: str
    generated_at: str

    # Token metrics
    total_tokens: int
    """Estimated input tokens for this run."""
    tokens_saved: int
    """Tokens saved by packing/optimisation (baseline − packed)."""
    baseline_tokens: int
    """Baseline full-context token estimate (unpacked)."""

    # File read metrics
    files_read: int
    """Total file entries in the packed context (including duplicates)."""
    unique_files_read: int
    """Number of distinct file paths in the packed context."""
    duplicate_reads: int
    """Extra reads beyond the first occurrence of each path."""
    duplicate_reads_prevented: int
    """Duplicate reads that the packer already deduplicated."""

    # Cache metrics
    cache_hits: int
    """Cache hits reported by the caching layer."""
    cache_tokens_saved: int
    """Tokens saved via cache (avoids recompressing unchanged files)."""

    # Context size
    context_size_files: int
    """Number of files included in the final packed context."""
    max_tokens: int
    """Token budget limit applied during packing (0 = unlimited)."""

    # Duration
    run_duration_ms: int
    """Wall-clock duration of the agent run in milliseconds (0 if unavailable)."""

    # Raw task info
    task: str
    repo: str

    # Per-file breakdown (optional, for JSON export)
    files: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _build_file_index(run_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return path -> first-occurrence entry dict from compressed_context."""
    index: dict[str, dict[str, Any]] = {}
    for entry in run_data.get("compressed_context", []) or []:
        if not isinstance(entry, dict):
            continue
        path = _str(entry.get("path"))
        if path and path not in index:
            index[path] = entry
    return index


def _read_counts(run_data: dict[str, Any]) -> dict[str, int]:
    """Return path -> occurrence count from compressed_context."""
    counts: dict[str, int] = {}
    for entry in run_data.get("compressed_context", []) or []:
        if not isinstance(entry, dict):
            continue
        path = _str(entry.get("path"))
        if path:
            counts[path] = counts.get(path, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_observe_report(
    run_data: dict[str, Any],
    *,
    run_json: str = "",
) -> AgentRunMetrics:
    """Build an AgentRunMetrics report from a pack run artifact.

    Parameters
    ----------
    run_data:
        Deserialized run.json dict produced by ``redcon pack``.
    run_json:
        Optional path string recorded in the report for traceability.
    """
    budget = run_data.get("budget") or {}
    cache_report = run_data.get("cache") or {}

    total_tokens = _int(run_data.get("estimated_input_tokens"))
    baseline_tokens = _int(run_data.get("baseline_full_context_tokens"))
    tokens_saved = _int(run_data.get("estimated_saved_tokens"))
    # Fall back: compute savings from baseline when field is absent
    if not tokens_saved and baseline_tokens and total_tokens:
        tokens_saved = max(0, baseline_tokens - total_tokens)

    max_tokens = _int(budget.get("max_tokens") or run_data.get("max_tokens"))
    duplicate_reads_prevented = _int(budget.get("duplicate_reads_prevented"))

    # Cache stats
    cache_hits = _int(cache_report.get("hits"))
    cache_tokens_saved = _int(cache_report.get("tokens_saved"))

    # File read analysis
    file_index = _build_file_index(run_data)
    counts = _read_counts(run_data)
    total_reads = sum(counts.values())
    duplicate_reads = sum(max(0, c - 1) for c in counts.values())

    # Build per-file breakdown
    files: list[dict[str, Any]] = []
    for path, entry in file_index.items():
        read_count = counts.get(path, 1)
        files.append(
            {
                "path": path,
                "original_tokens": _int(entry.get("original_tokens")),
                "compressed_tokens": _int(entry.get("compressed_tokens")),
                "strategy": _str(entry.get("strategy")),
                "read_count": read_count,
                "is_duplicate": read_count > 1,
            }
        )
    # Sort heaviest first
    files.sort(key=lambda f: f["original_tokens"], reverse=True)

    # Duration — stored as run_duration_ms in some artifacts, else 0
    run_duration_ms = _int(run_data.get("run_duration_ms") or run_data.get("elapsed_ms"))

    return AgentRunMetrics(
        command="observe",
        run_json=run_json,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_tokens=total_tokens,
        tokens_saved=tokens_saved,
        baseline_tokens=baseline_tokens,
        files_read=total_reads,
        unique_files_read=len(file_index),
        duplicate_reads=duplicate_reads,
        duplicate_reads_prevented=duplicate_reads_prevented,
        cache_hits=cache_hits,
        cache_tokens_saved=cache_tokens_saved,
        context_size_files=len(file_index),
        max_tokens=max_tokens,
        run_duration_ms=run_duration_ms,
        task=_str(run_data.get("task")),
        repo=_str(run_data.get("repo")),
        files=files,
    )


def observe_as_dict(report: AgentRunMetrics) -> dict[str, Any]:
    """Convert an AgentRunMetrics report to a JSON-serialisable dict."""
    return asdict(report)


__all__ = [
    "AgentRunMetrics",
    "build_observe_report",
    "observe_as_dict",
]
