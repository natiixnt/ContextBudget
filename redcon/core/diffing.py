from __future__ import annotations

"""Run-to-run diff analysis for Redcon artifacts."""

from typing import Any

from redcon.cache import normalize_cache_report


_RISK_ORDER = {
    "low": 1,
    "medium": 2,
    "high": 3,
}


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _risk_value(label: str) -> int:
    return _RISK_ORDER.get(str(label).lower(), 0)


def _ranked_map(data: dict[str, Any]) -> dict[str, float]:
    ranked = data.get("ranked_files", [])
    mapping: dict[str, float] = {}
    if not isinstance(ranked, list):
        return mapping
    for item in ranked:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path:
            continue
        mapping[path] = _to_float(item.get("score", 0.0))
    return mapping


def _score_change_rows(old_scores: dict[str, float], new_scores: dict[str, float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(set(old_scores) | set(new_scores)):
        has_old = path in old_scores
        has_new = path in new_scores
        old_score = old_scores.get(path)
        new_score = new_scores.get(path)

        if has_old and has_new:
            delta = round(float(new_score) - float(old_score), 3)
            if delta == 0:
                continue
            change_type = "changed"
        elif has_new:
            delta = round(float(new_score), 3)
            change_type = "added"
        else:
            delta = round(-float(old_score), 3)
            change_type = "removed"

        rows.append(
            {
                "path": path,
                "old_score": old_score,
                "new_score": new_score,
                "delta": delta,
                "change_type": change_type,
            }
        )

    rows.sort(key=lambda item: (-abs(_to_float(item.get("delta"))), item.get("path", "")))
    return rows


def diff_run_artifacts(
    old_run: dict[str, Any],
    new_run: dict[str, Any],
    old_label: str = "old",
    new_label: str = "new",
) -> dict[str, Any]:
    """Compute deterministic diff between two run artifacts."""

    old_task = str(old_run.get("task", ""))
    new_task = str(new_run.get("task", ""))

    old_budget = old_run.get("budget", {}) if isinstance(old_run.get("budget", {}), dict) else {}
    new_budget = new_run.get("budget", {}) if isinstance(new_run.get("budget", {}), dict) else {}

    old_included = set(old_run.get("files_included", []) if isinstance(old_run.get("files_included", []), list) else [])
    new_included = set(new_run.get("files_included", []) if isinstance(new_run.get("files_included", []), list) else [])

    files_added = sorted(new_included - old_included)
    files_removed = sorted(old_included - new_included)

    old_scores = _ranked_map(old_run)
    new_scores = _ranked_map(new_run)
    score_changes = _score_change_rows(old_scores, new_scores)

    old_input = _to_int(old_budget.get("estimated_input_tokens"))
    new_input = _to_int(new_budget.get("estimated_input_tokens"))
    old_saved = _to_int(old_budget.get("estimated_saved_tokens"))
    new_saved = _to_int(new_budget.get("estimated_saved_tokens"))

    old_risk = str(old_budget.get("quality_risk_estimate", "unknown"))
    new_risk = str(new_budget.get("quality_risk_estimate", "unknown"))
    old_risk_value = _risk_value(old_risk)
    new_risk_value = _risk_value(new_risk)

    old_cache_hits = _to_int(normalize_cache_report(old_run).get("hits"))
    new_cache_hits = _to_int(normalize_cache_report(new_run).get("hits"))

    return {
        "command": "diff",
        "old_run": old_label,
        "new_run": new_label,
        "task_diff": {
            "old_task": old_task,
            "new_task": new_task,
            "changed": old_task != new_task,
        },
        "context_diff": {
            "files_added": files_added,
            "files_removed": files_removed,
            "added_count": len(files_added),
            "removed_count": len(files_removed),
        },
        "ranked_score_changes": score_changes,
        "budget_delta": {
            "estimated_input_tokens": {
                "old": old_input,
                "new": new_input,
                "delta": new_input - old_input,
            },
            "estimated_saved_tokens": {
                "old": old_saved,
                "new": new_saved,
                "delta": new_saved - old_saved,
            },
            "quality_risk": {
                "old": old_risk,
                "new": new_risk,
                "delta_level": new_risk_value - old_risk_value,
            },
            "cache_hits": {
                "old": old_cache_hits,
                "new": new_cache_hits,
                "delta": new_cache_hits - old_cache_hits,
            },
        },
    }
