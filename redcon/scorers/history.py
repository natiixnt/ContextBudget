from __future__ import annotations

"""Deterministic history-backed score adjustments."""

from dataclasses import dataclass
from typing import Protocol

from redcon.cache.run_history import RunHistoryEntry
from redcon.config import ScoreSettings
from redcon.core.text import clamp, task_keywords
from redcon.schemas.models import FileRecord


class TaskSimilarityCallable(Protocol):
    """Extensible task-similarity hook for future embedding-backed scoring."""

    def __call__(self, *, task: str, other_task: str) -> float: ...


@dataclass(frozen=True, slots=True)
class HistoricalScoreAdjustment:
    """Historical score delta and explanatory reasons for a file."""

    score: float = 0.0
    reasons: tuple[str, ...] = ()


def keyword_overlap_similarity(*, task: str, other_task: str) -> float:
    """Compute deterministic task similarity from keyword overlap."""

    current_keywords = set(task_keywords(task))
    other_keywords = set(task_keywords(other_task))
    if not current_keywords or not other_keywords:
        return 0.0
    union = current_keywords | other_keywords
    if not union:
        return 0.0
    return len(current_keywords & other_keywords) / len(union)


def compute_historical_adjustments(
    task: str,
    files: list[FileRecord],
    settings: ScoreSettings,
    history_entries: list[RunHistoryEntry] | None = None,
    similarity: TaskSimilarityCallable | None = None,
) -> dict[str, HistoricalScoreAdjustment]:
    """Build per-file historical score adjustments from similar prior runs."""

    entries = list(history_entries or [])
    if not entries:
        return {}

    similarity_fn = similarity if similarity is not None else keyword_overlap_similarity
    recent_entries = entries[-settings.history_entry_limit :] if settings.history_entry_limit > 0 else entries

    selected_weights: dict[str, float] = {}
    ignored_weights: dict[str, float] = {}
    selected_counts: dict[str, int] = {}
    ignored_counts: dict[str, int] = {}

    for entry in recent_entries:
        score = similarity_fn(task=task, other_task=entry.task)
        if score < settings.history_task_similarity_threshold:
            continue

        for path in entry.selected_files:
            selected_weights[path] = selected_weights.get(path, 0.0) + score
            selected_counts[path] = selected_counts.get(path, 0) + 1

        for path in entry.ignored_files:
            ignored_weights[path] = ignored_weights.get(path, 0.0) + score
            ignored_counts[path] = ignored_counts.get(path, 0) + 1

    adjustments: dict[str, HistoricalScoreAdjustment] = {}
    for record in files:
        boost = clamp(
            selected_weights.get(record.path, 0.0) * settings.history_selected_file_boost,
            0.0,
            settings.history_score_cap,
        )
        penalty = clamp(
            ignored_weights.get(record.path, 0.0) * settings.history_ignored_file_penalty,
            0.0,
            settings.history_score_cap,
        )
        score = round(boost - penalty, 3)
        if score == 0:
            continue

        reasons: list[str] = []
        selected_count = selected_counts.get(record.path, 0)
        ignored_count = ignored_counts.get(record.path, 0)
        if selected_count:
            suffix = "s" if selected_count != 1 else ""
            reasons.append(f"history selected in {selected_count} similar run{suffix}")
        if ignored_count:
            suffix = "s" if ignored_count != 1 else ""
            reasons.append(f"history ignored in {ignored_count} similar run{suffix}")
        adjustments[record.path] = HistoricalScoreAdjustment(score=score, reasons=tuple(reasons))
    return adjustments
