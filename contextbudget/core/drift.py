from __future__ import annotations

"""Context drift detection: compare current context size against historical runs.

Drift analysis reads the local run history and measures how token usage,
file count, and context complexity have changed over a rolling window of
recent pack runs.  The report highlights which files are most responsible
for growth so teams can act before context budgets are exhausted.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contextbudget.cache.run_history import RunHistoryEntry, load_run_history
from contextbudget.schemas.models import RUN_HISTORY_FILE


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DriftSnapshot:
    """Token/file metrics for a single point in time."""

    generated_at: str
    task: str
    token_count: int
    file_count: int
    complexity: float  # avg tokens per file
    dep_depth: int = 0  # number of candidate files (proxy for dependency graph breadth)


@dataclass(slots=True)
class DriftMetrics:
    """Computed drift between baseline and current snapshots."""

    token_drift_pct: float
    file_drift_pct: float
    complexity_drift_pct: float
    dep_depth_drift_pct: float
    alert: bool
    verdict: str  # "none" | "low" | "moderate" | "significant" | "critical"


@dataclass(slots=True)
class DriftContributor:
    """A file that materially contributes to context drift."""

    file: str
    status: str          # "added" | "removed" | "persistent"
    recent_frequency: float   # fraction of recent-half entries containing file
    baseline_frequency: float # fraction of baseline-half entries containing file
    frequency_delta: float    # recent_frequency - baseline_frequency


@dataclass(slots=True)
class DriftReport:
    """Full drift analysis report."""

    command: str
    generated_at: str
    repo: str
    task_filter: str
    window: int
    threshold_pct: float
    entries_analyzed: int
    baseline: DriftSnapshot
    current: DriftSnapshot
    drift: DriftMetrics
    top_contributors: list[DriftContributor]
    trend: list[DriftSnapshot]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _timestamp_key(value: str) -> tuple[int, str]:
    if not value:
        return (1, "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return (0, value)
    return (0, parsed.astimezone(timezone.utc).isoformat())


def _token_count(entry: RunHistoryEntry) -> int:
    return int(entry.token_usage.get("estimated_input_tokens", 0) or 0)


def _file_count(entry: RunHistoryEntry) -> int:
    return len(entry.selected_files)


def _complexity(token_count: int, file_count: int) -> float:
    if file_count == 0:
        return 0.0
    return round(token_count / file_count, 2)


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return round((new - old) / old * 100, 2)


def _verdict(drift_pct: float, threshold_pct: float) -> str:
    abs_drift = abs(drift_pct)
    if abs_drift < threshold_pct:
        return "none"
    if abs_drift < 25.0:
        return "low"
    if abs_drift < 50.0:
        return "moderate"
    if abs_drift < 100.0:
        return "significant"
    return "critical"


def _snapshot(entry: RunHistoryEntry) -> DriftSnapshot:
    tc = _token_count(entry)
    fc = _file_count(entry)
    return DriftSnapshot(
        generated_at=entry.generated_at,
        task=entry.task,
        token_count=tc,
        file_count=fc,
        complexity=_complexity(tc, fc),
        dep_depth=len(entry.candidate_files),
    )


def _file_frequency(entries: list[RunHistoryEntry], file: str) -> float:
    if not entries:
        return 0.0
    count = sum(1 for e in entries if file in e.selected_files)
    return round(count / len(entries), 4)


def _compute_contributors(
    baseline_entries: list[RunHistoryEntry],
    recent_entries: list[RunHistoryEntry],
    *,
    top_n: int = 10,
) -> list[DriftContributor]:
    """Identify files whose inclusion pattern changed most between windows."""

    baseline_files: set[str] = set()
    recent_files: set[str] = set()
    for e in baseline_entries:
        baseline_files.update(e.selected_files)
    for e in recent_entries:
        recent_files.update(e.selected_files)

    all_files = baseline_files | recent_files
    contributors: list[DriftContributor] = []

    for file in all_files:
        rf = _file_frequency(recent_entries, file)
        bf = _file_frequency(baseline_entries, file)
        delta = round(rf - bf, 4)

        if file in recent_files and file not in baseline_files:
            status = "added"
        elif file in baseline_files and file not in recent_files:
            status = "removed"
        else:
            status = "persistent"

        # Only include files with a meaningful frequency change or new/removed
        if abs(delta) < 0.01 and status == "persistent":
            continue

        contributors.append(
            DriftContributor(
                file=file,
                status=status,
                recent_frequency=rf,
                baseline_frequency=bf,
                frequency_delta=delta,
            )
        )

    # Sort: added/removed first, then by absolute frequency delta descending
    contributors.sort(key=lambda c: (0 if c.status != "persistent" else 1, -abs(c.frequency_delta), c.file))
    return contributors[:top_n]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_drift(
    repo: Path,
    *,
    task: str | None = None,
    window: int = 20,
    threshold_pct: float = 10.0,
    history_file: str = RUN_HISTORY_FILE,
) -> dict[str, Any]:
    """Analyse context drift across recent pack history for *repo*.

    Parameters
    ----------
    repo:
        Repository root.  The history file is read from
        ``<repo>/.contextbudget/history.json`` (or *history_file*).
    task:
        Optional substring filter.  When provided only history entries
        whose task field contains this string (case-insensitive) are used.
    window:
        Maximum number of recent history entries to include in the analysis.
    threshold_pct:
        Absolute drift percentage at or above which ``alert`` is set and the
        verdict is at least ``"low"``.
    history_file:
        Override path to the history JSON file (relative to *repo* or
        absolute).

    Returns
    -------
    dict
        JSON-serialisable drift report.
    """

    if window < 2:
        raise ValueError("--window must be at least 2")
    if threshold_pct <= 0:
        raise ValueError("--threshold must be greater than 0")

    all_entries = load_run_history(repo, history_file=history_file)

    # Filter by task substring if requested
    task_filter = (task or "").strip()
    if task_filter:
        all_entries = [e for e in all_entries if task_filter.lower() in e.task.lower()]

    if not all_entries:
        raise ValueError(
            "No history entries found"
            + (f" matching task filter '{task_filter}'" if task_filter else "")
            + ". Run `contextbudget pack` first to record history."
        )

    # Entries are stored oldest-first; take the most recent `window` entries.
    entries = all_entries[-window:]
    entries_analyzed = len(entries)

    if entries_analyzed < 2:
        raise ValueError(
            f"Need at least 2 history entries to compute drift (found {entries_analyzed}). "
            "Run more packs to build up history."
        )

    baseline_entry = entries[0]
    current_entry = entries[-1]

    baseline_snap = _snapshot(baseline_entry)
    current_snap = _snapshot(current_entry)

    token_drift = _pct_change(baseline_snap.token_count, current_snap.token_count)
    file_drift = _pct_change(baseline_snap.file_count, current_snap.file_count)
    complexity_drift = _pct_change(baseline_snap.complexity, current_snap.complexity)
    dep_depth_drift = _pct_change(baseline_snap.dep_depth, current_snap.dep_depth)

    verdict = _verdict(token_drift, threshold_pct)
    alert = verdict != "none"

    drift_metrics = DriftMetrics(
        token_drift_pct=token_drift,
        file_drift_pct=file_drift,
        complexity_drift_pct=complexity_drift,
        dep_depth_drift_pct=dep_depth_drift,
        alert=alert,
        verdict=verdict,
    )

    # Split entries into baseline-half and recent-half for contributor analysis
    mid = max(1, entries_analyzed // 2)
    baseline_half = entries[:mid]
    recent_half = entries[mid:]

    top_contributors = _compute_contributors(baseline_half, recent_half, top_n=10)
    trend = [_snapshot(e) for e in entries]

    report = DriftReport(
        command="drift",
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo=str(repo),
        task_filter=task_filter,
        window=window,
        threshold_pct=threshold_pct,
        entries_analyzed=entries_analyzed,
        baseline=baseline_snap,
        current=current_snap,
        drift=drift_metrics,
        top_contributors=top_contributors,
        trend=trend,
    )
    return asdict(report)
