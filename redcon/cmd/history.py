"""
SQLite-backed history of redcon_run invocations.

Writes one row per command compression so the dashboard (and any future
analytics) can answer questions like 'how many tokens did we save on git
diff this week?'. Lives in the same .redcon/history.db file as the file
pack history but in its own table - no schema migration to existing data.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from redcon.cmd.pipeline import CompressionReport

logger = logging.getLogger(__name__)


_DEFAULT_DB_PATH = Path(".redcon") / "history.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS run_history_cmd (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    schema TEXT NOT NULL,
    command TEXT NOT NULL,
    cwd TEXT NOT NULL,
    level TEXT NOT NULL,
    raw_tokens INTEGER NOT NULL,
    compressed_tokens INTEGER NOT NULL,
    reduction_pct REAL NOT NULL,
    must_preserve_ok INTEGER NOT NULL,
    cache_hit INTEGER NOT NULL,
    returncode INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    cache_digest TEXT NOT NULL,
    notes TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_history_cmd_generated_at
    ON run_history_cmd(generated_at);
CREATE INDEX IF NOT EXISTS idx_run_history_cmd_schema
    ON run_history_cmd(schema);
"""


def _resolve_db_path(repo_root: Path | None = None) -> Path:
    base = repo_root or Path.cwd()
    return (base / _DEFAULT_DB_PATH).resolve()


def ensure_schema(db_path: Path) -> None:
    """Create the table if it doesn't exist. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def record_run(
    report: CompressionReport,
    *,
    command: str,
    repo_root: Path | None = None,
    db_path: Path | None = None,
) -> int | None:
    """
    Insert one row capturing this redcon_run invocation. Returns the row id.

    Errors are swallowed and logged so a transient SQLite issue never breaks
    the agent's tool call. Returns None on any failure.
    """
    target = db_path or _resolve_db_path(repo_root)
    try:
        ensure_schema(target)
    except (sqlite3.Error, OSError) as e:
        logger.warning("cmd history schema init failed: %s", e)
        return None

    out = report.output
    payload = (
        dt.datetime.now(dt.timezone.utc).isoformat(),
        out.schema,
        command,
        report.cache_key.cwd,
        out.level.value,
        out.original_tokens,
        out.compressed_tokens,
        round(out.reduction_pct, 4),
        1 if out.must_preserve_ok else 0,
        1 if report.cache_hit else 0,
        report.returncode,
        round(report.duration_seconds, 6),
        report.cache_key.digest,
        json.dumps(list(out.notes)),
    )
    try:
        with closing(sqlite3.connect(str(target))) as conn:
            cursor = conn.execute(
                "INSERT INTO run_history_cmd "
                "(generated_at, schema, command, cwd, level, raw_tokens, "
                "compressed_tokens, reduction_pct, must_preserve_ok, "
                "cache_hit, returncode, duration_seconds, cache_digest, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
            conn.commit()
            return cursor.lastrowid
    except sqlite3.Error as e:
        logger.warning("cmd history insert failed: %s", e)
        return None


def recent_runs(
    *,
    limit: int = 50,
    schema: str | None = None,
    repo_root: Path | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Return the most recent rows for diagnostic / dashboard use."""
    target = db_path or _resolve_db_path(repo_root)
    if not target.exists():
        return []
    query = (
        "SELECT generated_at, schema, command, cwd, level, raw_tokens, "
        "compressed_tokens, reduction_pct, must_preserve_ok, cache_hit, "
        "returncode, duration_seconds FROM run_history_cmd "
    )
    params: tuple = ()
    if schema:
        query += "WHERE schema = ? "
        params = (schema,)
    query += "ORDER BY generated_at DESC LIMIT ?"
    params = (*params, limit)
    try:
        with closing(sqlite3.connect(str(target))) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(query, params)]
    except sqlite3.Error as e:
        logger.warning("cmd history read failed: %s", e)
        return []
    return rows


def aggregate_savings(
    *,
    repo_root: Path | None = None,
    db_path: Path | None = None,
) -> dict:
    """Compute total tokens saved across all logged cmd runs. Useful for dashboards."""
    target = db_path or _resolve_db_path(repo_root)
    if not target.exists():
        return {"runs": 0, "raw_tokens": 0, "compressed_tokens": 0, "saved_tokens": 0}
    try:
        with closing(sqlite3.connect(str(target))) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS runs, "
                "COALESCE(SUM(raw_tokens), 0) AS raw, "
                "COALESCE(SUM(compressed_tokens), 0) AS comp "
                "FROM run_history_cmd"
            ).fetchone()
    except sqlite3.Error:
        return {"runs": 0, "raw_tokens": 0, "compressed_tokens": 0, "saved_tokens": 0}
    runs, raw, comp = row
    return {
        "runs": int(runs or 0),
        "raw_tokens": int(raw or 0),
        "compressed_tokens": int(comp or 0),
        "saved_tokens": int((raw or 0) - (comp or 0)),
    }
