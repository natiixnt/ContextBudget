from __future__ import annotations

"""Local run-history persistence for deterministic score adjustments."""

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from contextbudget.schemas.models import RUN_HISTORY_FILE


HISTORY_FORMAT_VERSION = 1


@dataclass(slots=True)
class RunHistoryEntry:
    """Persisted metadata from a previous pack run."""

    generated_at: str
    task: str
    selected_files: list[str] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)
    token_usage: dict[str, int | str] = field(default_factory=dict)
    result_artifacts: dict[str, str] = field(default_factory=dict)
    repo: str = ""
    workspace: str = ""


def _history_path(repo_path: Path, history_file: str) -> Path:
    candidate = Path(history_file or RUN_HISTORY_FILE)
    if candidate.is_absolute():
        return candidate
    return repo_path / candidate


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _normalize_string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    output: dict[str, str] = {}
    for key, raw in value.items():
        text = str(raw)
        if not str(key).strip():
            continue
        output[str(key)] = text
    return output


def _normalize_token_usage(value: Any) -> dict[str, int | str]:
    if not isinstance(value, Mapping):
        return {}
    output: dict[str, int | str] = {}
    for key in ("max_tokens", "estimated_input_tokens", "estimated_saved_tokens"):
        try:
            output[key] = int(value.get(key, 0) or 0)
        except (AttributeError, TypeError, ValueError):
            output[key] = 0
    risk = str(value.get("quality_risk_estimate", "") or "")
    if risk:
        output["quality_risk_estimate"] = risk
    return output


def _empty_history_document() -> dict[str, Any]:
    return {
        "version": HISTORY_FORMAT_VERSION,
        "entries": [],
    }


def _load_history_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_history_document()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_history_document()
    if not isinstance(data, dict):
        return _empty_history_document()
    if int(data.get("version", 0) or 0) != HISTORY_FORMAT_VERSION:
        return _empty_history_document()
    raw_entries = data.get("entries", [])
    if not isinstance(raw_entries, list):
        raw_entries = []
    return {
        "version": HISTORY_FORMAT_VERSION,
        "entries": raw_entries,
    }


def load_run_history(
    repo_path: Path,
    *,
    enabled: bool = True,
    history_file: str = RUN_HISTORY_FILE,
    history_db: str = ".contextbudget/history.db",
    use_sqlite: bool = True,
) -> list[RunHistoryEntry]:
    """Load persisted run history for a repository or workspace root."""

    if not enabled:
        return []

    if use_sqlite:
        try:
            from contextbudget.cache.run_history_sqlite import load_run_history_sqlite
            return load_run_history_sqlite(
                repo_path,
                enabled=enabled,
                history_db=history_db,
            )
        except Exception:
            pass

    path = _history_path(repo_path.resolve(), history_file)
    document = _load_history_document(path)

    entries: list[RunHistoryEntry] = []
    for item in document.get("entries", []):
        if not isinstance(item, Mapping):
            continue
        generated_at = str(item.get("generated_at", "") or "").strip()
        task = str(item.get("task", "") or "").strip()
        if not generated_at or not task:
            continue
        entries.append(
            RunHistoryEntry(
                generated_at=generated_at,
                task=task,
                selected_files=_normalize_string_list(item.get("selected_files")),
                ignored_files=_normalize_string_list(item.get("ignored_files")),
                candidate_files=_normalize_string_list(item.get("candidate_files")),
                token_usage=_normalize_token_usage(item.get("token_usage")),
                result_artifacts=_normalize_string_map(item.get("result_artifacts")),
                repo=str(item.get("repo", "") or ""),
                workspace=str(item.get("workspace", "") or ""),
            )
        )
    return entries


def append_run_history_entry(
    repo_path: Path,
    entry: RunHistoryEntry,
    *,
    enabled: bool = True,
    history_file: str = RUN_HISTORY_FILE,
    history_db: str = ".contextbudget/history.db",
    max_entries: int = 200,
    use_sqlite: bool = True,
) -> bool:
    """Append a run-history entry using stable JSON serialization."""

    if not enabled:
        return False

    if use_sqlite:
        try:
            from contextbudget.cache.run_history_sqlite import append_run_history_entry_sqlite
            return append_run_history_entry_sqlite(
                repo_path,
                entry,
                enabled=enabled,
                history_db=history_db,
                max_entries=max_entries,
            )
        except Exception:
            pass

    path = _history_path(repo_path.resolve(), history_file)
    document = _load_history_document(path)
    entries = list(document.get("entries", []))
    entries.append(asdict(entry))
    if max_entries > 0:
        entries = entries[-max_entries:]
    document["entries"] = entries
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return False
    return True


def update_run_history_artifacts(
    repo_path: Path,
    *,
    generated_at: str,
    result_artifacts: Mapping[str, str],
    enabled: bool = True,
    history_file: str = RUN_HISTORY_FILE,
    history_db: str = ".contextbudget/history.db",
    use_sqlite: bool = True,
) -> bool:
    """Merge artifact paths into the most recent matching history entry."""

    if not enabled:
        return False

    if use_sqlite:
        try:
            from contextbudget.cache.run_history_sqlite import update_run_history_artifacts_sqlite
            return update_run_history_artifacts_sqlite(
                repo_path,
                generated_at=generated_at,
                result_artifacts=result_artifacts,
                enabled=enabled,
                history_db=history_db,
            )
        except Exception:
            pass

    normalized_generated_at = str(generated_at or "").strip()
    if not normalized_generated_at:
        return False

    normalized_artifacts = {
        str(key): str(value)
        for key, value in result_artifacts.items()
        if str(key).strip() and str(value).strip()
    }
    if not normalized_artifacts:
        return False

    path = _history_path(repo_path.resolve(), history_file)
    document = _load_history_document(path)
    entries = list(document.get("entries", []))
    updated = False
    for item in reversed(entries):
        if not isinstance(item, dict):
            continue
        if str(item.get("generated_at", "") or "").strip() != normalized_generated_at:
            continue
        merged = _normalize_string_map(item.get("result_artifacts"))
        merged.update(normalized_artifacts)
        item["result_artifacts"] = merged
        updated = True
        break
    if not updated:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return False
    return True
