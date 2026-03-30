from __future__ import annotations

"""Local run-history persistence for deterministic score adjustments."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Maximum history file size to load (10 MB)
_MAX_HISTORY_FILE_SIZE = 10 * 1024 * 1024

from redcon.schemas.models import RUN_HISTORY_FILE


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
        file_size = path.stat().st_size
    except OSError:
        return _empty_history_document()
    if file_size > _MAX_HISTORY_FILE_SIZE:
        logger.warning(
            "History file %s exceeds 10 MB (%d bytes) - skipping load",
            path,
            file_size,
        )
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
    history_db: str = ".redcon/history.db",
    use_sqlite: bool = True,
) -> list[RunHistoryEntry]:
    """Load persisted run history for a repository or workspace root."""

    if not enabled:
        return []

    if use_sqlite:
        try:
            from redcon.cache.run_history_sqlite import load_run_history_sqlite
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
    entries.sort(key=lambda e: e.generated_at, reverse=True)
    return entries


def append_run_history_entry(
    repo_path: Path,
    entry: RunHistoryEntry,
    *,
    enabled: bool = True,
    history_file: str = RUN_HISTORY_FILE,
    history_db: str = ".redcon/history.db",
    max_entries: int = 200,
    use_sqlite: bool = True,
) -> bool:
    """Append a run-history entry using stable JSON serialization."""

    if not enabled:
        return False

    if use_sqlite:
        try:
            from redcon.cache.run_history_sqlite import append_run_history_entry_sqlite
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
    history_db: str = ".redcon/history.db",
    use_sqlite: bool = True,
) -> bool:
    """Merge artifact paths into the most recent matching history entry."""

    if not enabled:
        return False

    if use_sqlite:
        try:
            from redcon.cache.run_history_sqlite import update_run_history_artifacts_sqlite
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


def _parse_generated_at(value: str) -> datetime | None:
    """Try common ISO 8601 formats to parse a generated_at timestamp."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def prune(
    repo_path: Path,
    *,
    max_age_days: int = 30,
    enabled: bool = True,
    history_file: str = RUN_HISTORY_FILE,
    history_db: str = ".redcon/history.db",
    use_sqlite: bool = True,
) -> int:
    """Remove history entries older than *max_age_days* and return the count removed.

    Works with both the JSON file backend and the SQLite backend.
    """

    if not enabled or max_age_days <= 0:
        return 0

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
    removed = 0

    # --- SQLite path ---
    if use_sqlite:
        try:
            from redcon.cache.run_history_sqlite import _db_path, _ensure_schema
            import sqlite3

            resolved = repo_path.resolve()
            db = _db_path(resolved, history_db)
            if db.exists():
                with sqlite3.connect(str(db), timeout=5) as conn:
                    _ensure_schema(conn)
                    cursor = conn.execute(
                        "DELETE FROM run_history WHERE generated_at < ?",
                        (cutoff.isoformat(),),
                    )
                    removed = cursor.rowcount
                    conn.commit()
                return removed
        except Exception:
            pass

    # --- JSON fallback path ---
    path = _history_path(repo_path.resolve(), history_file)
    document = _load_history_document(path)
    entries = list(document.get("entries", []))
    kept: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        generated_at = str(item.get("generated_at", "") or "").strip()
        parsed = _parse_generated_at(generated_at)
        if parsed is not None:
            # Treat naive timestamps as UTC for comparison
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed < cutoff:
                removed += 1
                continue
        kept.append(item)
    if removed > 0:
        document["entries"] = kept
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            pass
    return removed
