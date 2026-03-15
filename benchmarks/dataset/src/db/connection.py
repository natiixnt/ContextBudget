from __future__ import annotations

"""Database connection management.

Provides a simple connection factory backed by SQLite for the benchmark
dataset.  Production deployments would swap this for a pooled adapter.
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional


_local = threading.local()
_db_path: Optional[str] = None
_lock = threading.Lock()


def configure(db_path: str = ":memory:") -> None:
    """Set the database path once at startup."""
    global _db_path
    with _lock:
        _db_path = db_path


def _get_path() -> str:
    if _db_path is None:
        return ":memory:"
    return _db_path


def get_raw_connection() -> sqlite3.Connection:
    """Return (and cache per thread) a raw SQLite connection."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_get_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a database connection."""
    conn = get_raw_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def close_connection() -> None:
    """Close the per-thread connection if open."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def initialize_schema() -> None:
    """Create tables if they do not exist."""
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                is_active INTEGER NOT NULL DEFAULT 1,
                password_hash TEXT,
                last_login_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                owner_id TEXT NOT NULL REFERENCES users(id),
                assignee_id TEXT REFERENCES users(id),
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'medium',
                tags TEXT NOT NULL DEFAULT '[]',
                due_at TEXT,
                parent_id TEXT REFERENCES tasks(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
            """
        )
