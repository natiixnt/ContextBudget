from __future__ import annotations

"""SQLite-backed persistence for control plane entities."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from contextbudget.control_plane.models import AgentRun, Organization, Project, Repository

_DDL = """\
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS organizations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(org_id, slug)
);

CREATE TABLE IF NOT EXISTS repositories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    path       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id      INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    task         TEXT NOT NULL DEFAULT '',
    token_usage  INTEGER NOT NULL DEFAULT 0,
    tokens_saved INTEGER NOT NULL DEFAULT 0,
    context_size INTEGER NOT NULL DEFAULT 0,
    cache_hits   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlPlaneStore:
    """Thin SQLite wrapper for control plane analytics data."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()

    def _bootstrap(self) -> None:
        self._conn.executescript(_DDL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Organizations
    # ------------------------------------------------------------------

    def create_org(self, name: str, slug: str) -> Organization:
        cur = self._conn.execute(
            "INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
            (name, slug, _now()),
        )
        self._conn.commit()
        return self.get_org(cur.lastrowid)  # type: ignore[arg-type]

    def get_org(self, org_id: int) -> Organization | None:
        row = self._conn.execute(
            "SELECT * FROM organizations WHERE id = ?", (org_id,)
        ).fetchone()
        return _row_to_org(row) if row else None

    def list_orgs(self) -> list[Organization]:
        rows = self._conn.execute(
            "SELECT * FROM organizations ORDER BY id"
        ).fetchall()
        return [_row_to_org(r) for r in rows]

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def create_project(self, org_id: int, name: str, slug: str) -> Project:
        cur = self._conn.execute(
            "INSERT INTO projects (org_id, name, slug, created_at) VALUES (?, ?, ?, ?)",
            (org_id, name, slug, _now()),
        )
        self._conn.commit()
        return self.get_project(cur.lastrowid)  # type: ignore[arg-type]

    def get_project(self, project_id: int) -> Project | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return _row_to_project(row) if row else None

    def list_projects(self, org_id: int | None = None) -> list[Project]:
        if org_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM projects WHERE org_id = ? ORDER BY id", (org_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM projects ORDER BY id"
            ).fetchall()
        return [_row_to_project(r) for r in rows]

    # ------------------------------------------------------------------
    # Repositories
    # ------------------------------------------------------------------

    def create_repo(self, project_id: int, name: str, path: str = "") -> Repository:
        cur = self._conn.execute(
            "INSERT INTO repositories (project_id, name, path, created_at) VALUES (?, ?, ?, ?)",
            (project_id, name, path, _now()),
        )
        self._conn.commit()
        return self.get_repo(cur.lastrowid)  # type: ignore[arg-type]

    def get_repo(self, repo_id: int) -> Repository | None:
        row = self._conn.execute(
            "SELECT * FROM repositories WHERE id = ?", (repo_id,)
        ).fetchone()
        return _row_to_repo(row) if row else None

    def list_repos(self, project_id: int | None = None) -> list[Repository]:
        if project_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM repositories WHERE project_id = ? ORDER BY id",
                (project_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM repositories ORDER BY id"
            ).fetchall()
        return [_row_to_repo(r) for r in rows]

    # ------------------------------------------------------------------
    # AgentRuns
    # ------------------------------------------------------------------

    def create_run(
        self,
        repo_id: int,
        *,
        task: str = "",
        token_usage: int = 0,
        tokens_saved: int = 0,
        context_size: int = 0,
        cache_hits: int = 0,
    ) -> AgentRun:
        cur = self._conn.execute(
            """INSERT INTO agent_runs
               (repo_id, task, token_usage, tokens_saved, context_size, cache_hits, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (repo_id, task, token_usage, tokens_saved, context_size, cache_hits, _now()),
        )
        self._conn.commit()
        return self.get_run(cur.lastrowid)  # type: ignore[arg-type]

    def get_run(self, run_id: int) -> AgentRun | None:
        row = self._conn.execute(
            "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, repo_id: int | None = None) -> list[AgentRun]:
        if repo_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM agent_runs WHERE repo_id = ? ORDER BY id", (repo_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM agent_runs ORDER BY id"
            ).fetchall()
        return [_row_to_run(r) for r in rows]


# ------------------------------------------------------------------
# Row converters
# ------------------------------------------------------------------


def _row_to_org(row: sqlite3.Row) -> Organization:
    return Organization(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        created_at=row["created_at"],
    )


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        slug=row["slug"],
        created_at=row["created_at"],
    )


def _row_to_repo(row: sqlite3.Row) -> Repository:
    return Repository(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        path=row["path"],
        created_at=row["created_at"],
    )


def _row_to_run(row: sqlite3.Row) -> AgentRun:
    return AgentRun(
        id=row["id"],
        repo_id=row["repo_id"],
        task=row["task"],
        token_usage=row["token_usage"],
        tokens_saved=row["tokens_saved"],
        context_size=row["context_size"],
        cache_hits=row["cache_hits"],
        created_at=row["created_at"],
    )
