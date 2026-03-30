from __future__ import annotations

"""SQLite-backed run-history persistence for deterministic score adjustments."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from redcon.cache.run_history import (
    RunHistoryEntry,
    _normalize_string_list,
    _normalize_string_map,
    _normalize_token_usage,
)
from redcon.schemas.models import RUN_HISTORY_FILE


SQLITE_HISTORY_FORMAT_VERSION = 2

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL DEFAULT 2
);
INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 2);
CREATE TABLE IF NOT EXISTS run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    task TEXT NOT NULL,
    repo TEXT NOT NULL DEFAULT '',
    workspace TEXT NOT NULL DEFAULT '',
    selected_files TEXT NOT NULL DEFAULT '[]',
    ignored_files TEXT NOT NULL DEFAULT '[]',
    candidate_files TEXT NOT NULL DEFAULT '[]',
    token_usage TEXT NOT NULL DEFAULT '{}',
    result_artifacts TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_run_history_generated_at ON run_history(generated_at);
CREATE INDEX IF NOT EXISTS idx_run_history_repo ON run_history(repo);
"""


def _db_path(repo_path: Path, history_db: str) -> Path:
    candidate = Path(history_db if history_db is not None else ".redcon/history.db")
    if candidate.is_absolute():
        return candidate
    return repo_path / candidate


def _json_path(repo_path: Path) -> Path:
    candidate = Path(RUN_HISTORY_FILE)
    if candidate.is_absolute():
        return candidate
    return repo_path / candidate


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_DDL)
    conn.commit()


def _row_to_entry(row: sqlite3.Row) -> RunHistoryEntry | None:
    try:
        generated_at = str(row["generated_at"] or "").strip()
        task = str(row["task"] or "").strip()
        if not generated_at or not task:
            return None
        return RunHistoryEntry(
            generated_at=generated_at,
            task=task,
            selected_files=_normalize_string_list(json.loads(row["selected_files"] or "[]")),
            ignored_files=_normalize_string_list(json.loads(row["ignored_files"] or "[]")),
            candidate_files=_normalize_string_list(json.loads(row["candidate_files"] or "[]")),
            token_usage=_normalize_token_usage(json.loads(row["token_usage"] or "{}")),
            result_artifacts=_normalize_string_map(json.loads(row["result_artifacts"] or "{}")),
            repo=str(row["repo"] or ""),
            workspace=str(row["workspace"] or ""),
        )
    except Exception:
        return None


def _migrate_from_json(db_path: Path, json_path: Path) -> None:
    """Import entries from legacy JSON history file into SQLite, then rename it."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return
    raw_entries = data.get("entries", [])
    if not isinstance(raw_entries, list):
        return

    try:
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            for item in raw_entries:
                if not isinstance(item, Mapping):
                    continue
                generated_at = str(item.get("generated_at", "") or "").strip()
                task = str(item.get("task", "") or "").strip()
                if not generated_at or not task:
                    continue
                conn.execute(
                    """
                    INSERT INTO run_history
                        (generated_at, task, repo, workspace,
                         selected_files, ignored_files, candidate_files,
                         token_usage, result_artifacts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generated_at,
                        task,
                        str(item.get("repo", "") or ""),
                        str(item.get("workspace", "") or ""),
                        json.dumps(_normalize_string_list(item.get("selected_files")), sort_keys=True),
                        json.dumps(_normalize_string_list(item.get("ignored_files")), sort_keys=True),
                        json.dumps(_normalize_string_list(item.get("candidate_files")), sort_keys=True),
                        json.dumps(_normalize_token_usage(item.get("token_usage")), sort_keys=True),
                        json.dumps(_normalize_string_map(item.get("result_artifacts")), sort_keys=True),
                    ),
                )
            conn.commit()
    except Exception:
        # Remove the partially-initialised DB so the next call can retry migration.
        # Without this, db_path.exists() would return True and migration would be skipped,
        # leaving an empty SQLite DB while the JSON data is unreachable.
        try:
            db_path.unlink(missing_ok=True)
        except OSError:
            pass
        return

    try:
        migrated_path = json_path.with_suffix(".json.migrated")
        json_path.rename(migrated_path)
    except Exception:
        pass


def load_run_history_sqlite(
    repo_path: Path,
    *,
    enabled: bool = True,
    history_db: str = ".redcon/history.db",
    limit: int = 200,
) -> list[RunHistoryEntry]:
    """Load persisted run history from SQLite for a repository or workspace root."""

    if not enabled:
        return []

    resolved = repo_path.resolve()
    db = _db_path(resolved, history_db)

    # Trigger migration from JSON if needed
    if not db.exists():
        json_path = _json_path(resolved)
        if json_path.exists():
            try:
                db.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                return []
            _migrate_from_json(db, json_path)
        else:
            # No history at all yet — create empty db lazily on first write
            return []

    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            effective_limit = limit if limit > 0 else 200
            # Return in ascending order (oldest first) to match the JSON backend
            # which stores and returns entries in append/insertion order.
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM run_history ORDER BY generated_at DESC LIMIT ?
                ) ORDER BY generated_at ASC
                """,
                (effective_limit,),
            ).fetchall()
    except Exception:
        return []

    entries: list[RunHistoryEntry] = []
    for row in rows:
        entry = _row_to_entry(row)
        if entry is not None:
            entries.append(entry)
    return entries


def append_run_history_entry_sqlite(
    repo_path: Path,
    entry: RunHistoryEntry,
    *,
    enabled: bool = True,
    history_db: str = ".redcon/history.db",
    max_entries: int = 200,
) -> bool:
    """Insert a run-history entry into SQLite and trim to max_entries."""

    if not enabled:
        return False

    resolved = repo_path.resolve()
    db = _db_path(resolved, history_db)

    # Trigger migration from JSON if db doesn't exist yet
    if not db.exists():
        json_path = _json_path(resolved)
        try:
            db.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        if json_path.exists():
            _migrate_from_json(db, json_path)

    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO run_history
                    (generated_at, task, repo, workspace,
                     selected_files, ignored_files, candidate_files,
                     token_usage, result_artifacts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.generated_at,
                    entry.task,
                    entry.repo,
                    entry.workspace,
                    json.dumps(entry.selected_files, sort_keys=True),
                    json.dumps(entry.ignored_files, sort_keys=True),
                    json.dumps(entry.candidate_files, sort_keys=True),
                    json.dumps(entry.token_usage, sort_keys=True),
                    json.dumps(entry.result_artifacts, sort_keys=True),
                ),
            )
            # Trim to max_entries by removing oldest rows beyond the limit
            if max_entries > 0:
                conn.execute(
                    """
                    DELETE FROM run_history WHERE id NOT IN (
                        SELECT id FROM run_history ORDER BY generated_at DESC LIMIT ?
                    )
                    """,
                    (max_entries,),
                )
            conn.commit()
    except Exception:
        return False

    return True


def update_run_history_artifacts_sqlite(
    repo_path: Path,
    *,
    generated_at: str,
    result_artifacts: Mapping[str, str],
    enabled: bool = True,
    history_db: str = ".redcon/history.db",
) -> bool:
    """Merge artifact paths into the most recent matching SQLite history entry."""

    if not enabled:
        return False

    normalized_generated_at = str(generated_at or "").strip()
    if not normalized_generated_at:
        return False

    normalized_artifacts: dict[str, str] = {
        str(key): str(value)
        for key, value in result_artifacts.items()
        if str(key).strip() and str(value).strip()
    }
    if not normalized_artifacts:
        return False

    resolved = repo_path.resolve()
    db = _db_path(resolved, history_db)

    if not db.exists():
        return False

    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT id, result_artifacts FROM run_history WHERE generated_at = ? ORDER BY id DESC LIMIT 1",
                (normalized_generated_at,),
            ).fetchone()
            if row is None:
                return False
            existing: dict[str, Any] = {}
            try:
                existing = json.loads(row["result_artifacts"] or "{}")
            except Exception:
                pass
            if not isinstance(existing, dict):
                existing = {}
            merged = _normalize_string_map(existing)
            merged.update(normalized_artifacts)
            conn.execute(
                "UPDATE run_history SET result_artifacts = ? WHERE id = ?",
                (json.dumps(merged, sort_keys=True), row["id"]),
            )
            conn.commit()
    except Exception:
        return False

    return True
