"""Control plane CRUD — orgs, projects, repos, API keys, audit log, policy versions."""
from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.auth import generate_api_key


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

async def create_org(pool: asyncpg.Pool, slug: str, display_name: str) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO orgs (slug, display_name) VALUES ($1, $2)"
            " RETURNING id, slug, display_name, created_at",
            slug, display_name,
        )
    return dict(row)


async def get_org(pool: asyncpg.Pool, org_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, slug, display_name, created_at FROM orgs WHERE id = $1",
            org_id,
        )
    return dict(row) if row else None


async def list_orgs(pool: asyncpg.Pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, slug, display_name, created_at FROM orgs ORDER BY created_at",
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

async def create_project(
    pool: asyncpg.Pool, org_id: int, slug: str, display_name: str
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO projects (org_id, slug, display_name) VALUES ($1, $2, $3)"
            " RETURNING id, org_id, slug, display_name, created_at",
            org_id, slug, display_name,
        )
    return dict(row)


async def get_project(pool: asyncpg.Pool, project_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, org_id, slug, display_name, created_at FROM projects WHERE id = $1",
            project_id,
        )
    return dict(row) if row else None


async def list_projects(pool: asyncpg.Pool, org_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, org_id, slug, display_name, created_at"
            " FROM projects WHERE org_id = $1 ORDER BY created_at",
            org_id,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

async def create_repo(
    pool: asyncpg.Pool,
    project_id: int,
    slug: str,
    display_name: str,
    repository_id: str | None = None,
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO repositories (project_id, slug, display_name, repository_id)"
            " VALUES ($1, $2, $3, $4)"
            " RETURNING id, project_id, slug, display_name, repository_id, created_at",
            project_id, slug, display_name, repository_id,
        )
    return dict(row)


async def get_repo(pool: asyncpg.Pool, repo_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, project_id, slug, display_name, repository_id, created_at"
            " FROM repositories WHERE id = $1",
            repo_id,
        )
    return dict(row) if row else None


async def list_repos(pool: asyncpg.Pool, project_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, project_id, slug, display_name, repository_id, created_at"
            " FROM repositories WHERE project_id = $1 ORDER BY created_at",
            project_id,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------

async def issue_api_key(
    pool: asyncpg.Pool,
    org_id: int,
    label: str | None = None,
    expires_at: Any = None,
) -> tuple[str, dict]:
    """Return ``(raw_key, key_record)``.

    *raw_key* must be shown to the caller immediately and is never
    recoverable from the database.  Pass *expires_at* (a ``datetime`` or
    ISO-8601 string) to create a time-bounded key.
    """
    raw, key_hash = generate_api_key()
    key_prefix = raw[:12]
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO api_keys (org_id, key_hash, key_prefix, label, expires_at)"
            " VALUES ($1, $2, $3, $4, $5)"
            " RETURNING id, org_id, key_prefix, label, revoked, created_at, expires_at",
            org_id, key_hash, key_prefix, label, expires_at,
        )
    return raw, dict(row)


async def list_api_keys(pool: asyncpg.Pool, org_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, org_id, key_prefix, label, revoked, created_at, revoked_at, expires_at"
            " FROM api_keys WHERE org_id = $1 ORDER BY created_at",
            org_id,
        )
    return [dict(r) for r in rows]


async def revoke_api_key(pool: asyncpg.Pool, key_id: int, org_id: int) -> bool:
    """Revoke *key_id*. Returns ``True`` if a row was updated."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE api_keys SET revoked = TRUE, revoked_at = NOW()"
            " WHERE id = $1 AND org_id = $2 AND revoked = FALSE",
            key_id, org_id,
        )
    return result == "UPDATE 1"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

async def append_audit_entry(
    pool: asyncpg.Pool,
    *,
    org_id: int | None = None,
    repository_id: str | None = None,
    run_id: str | None = None,
    task_hash: str | None = None,
    endpoint: str,
    policy_version: str | None = None,
    tokens_used: int | None = None,
    tokens_saved: int | None = None,
    violation_count: int = 0,
    policy_passed: bool | None = None,
    status_code: int | None = None,
) -> int:
    """Append one entry and return its ``id``."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO audit_log (
                org_id, repository_id, run_id, task_hash, endpoint,
                policy_version, tokens_used, tokens_saved,
                violation_count, policy_passed, status_code
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
            """,
            org_id, repository_id, run_id, task_hash, endpoint,
            policy_version, tokens_used, tokens_saved,
            violation_count, policy_passed, status_code,
        )
    return row["id"]


async def list_audit_log(
    pool: asyncpg.Pool,
    org_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, org_id, repository_id, run_id, task_hash, endpoint,
                   policy_version, tokens_used, tokens_saved,
                   violation_count, policy_passed, status_code, created_at
            FROM   audit_log
            WHERE  org_id = $1
            ORDER  BY created_at DESC
            LIMIT  $2 OFFSET $3
            """,
            org_id, limit, offset,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Policy versions
# ---------------------------------------------------------------------------

async def create_policy_version(
    pool: asyncpg.Pool,
    *,
    org_id: int,
    project_id: int | None,
    repo_id: int | None,
    version: str,
    spec: dict[str, Any],
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO policy_versions (org_id, project_id, repo_id, version, spec)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id, org_id, project_id, repo_id, version, spec,
                      is_active, created_at, activated_at
            """,
            org_id, project_id, repo_id, version, json.dumps(spec),
        )
    return dict(row)


async def activate_policy_version(
    pool: asyncpg.Pool, policy_id: int, org_id: int
) -> bool:
    """Deactivate all active versions at the same scope, then activate *policy_id*.

    Returns ``True`` if the row was found and activated.
    """
    async with pool.acquire() as conn:
        target = await conn.fetchrow(
            "SELECT id, org_id, project_id, repo_id FROM policy_versions"
            " WHERE id = $1 AND org_id = $2",
            policy_id, org_id,
        )
        if target is None:
            return False
        async with conn.transaction():
            # Deactivate all currently-active versions at the same (org, project, repo) scope
            await conn.execute(
                """
                UPDATE policy_versions
                SET    is_active = FALSE
                WHERE  org_id = $1
                  AND  (project_id IS NOT DISTINCT FROM $2)
                  AND  (repo_id    IS NOT DISTINCT FROM $3)
                  AND  is_active = TRUE
                """,
                target["org_id"], target["project_id"], target["repo_id"],
            )
            result = await conn.execute(
                "UPDATE policy_versions SET is_active = TRUE, activated_at = NOW()"
                " WHERE id = $1",
                policy_id,
            )
    return result == "UPDATE 1"


async def get_active_policy(
    pool: asyncpg.Pool,
    *,
    org_id: int,
    repo_id: int | None = None,
    project_id: int | None = None,
) -> dict | None:
    """Return the most-specific active policy: repo > project > org-level."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, org_id, project_id, repo_id, version, spec,
                   is_active, created_at, activated_at
            FROM   policy_versions
            WHERE  org_id = $1
              AND  is_active = TRUE
            ORDER BY
                (repo_id     IS NOT NULL AND repo_id     = $2) DESC,
                (project_id  IS NOT NULL AND project_id  = $3) DESC,
                created_at DESC
            LIMIT 1
            """,
            org_id, repo_id, project_id,
        )
    return dict(row) if row else None


async def list_policy_versions(pool: asyncpg.Pool, org_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, org_id, project_id, repo_id, version, spec,
                   is_active, created_at, activated_at
            FROM   policy_versions
            WHERE  org_id = $1
            ORDER  BY created_at DESC
            """,
            org_id,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Agent runs
# ---------------------------------------------------------------------------

async def record_agent_run(
    pool: asyncpg.Pool,
    *,
    repo_id: int,
    run_id: str,
    task_hash: str | None = None,
    status: str = "unknown",
    tokens_used: int | None = None,
    tokens_saved: int | None = None,
    cache_hits: int | None = None,
    policy_version: str | None = None,
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_runs
                (repo_id, run_id, task_hash, status, tokens_used,
                 tokens_saved, cache_hits, policy_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (run_id) DO UPDATE
                SET status       = EXCLUDED.status,
                    tokens_used  = EXCLUDED.tokens_used,
                    tokens_saved = EXCLUDED.tokens_saved,
                    cache_hits   = EXCLUDED.cache_hits,
                    completed_at = NOW()
            RETURNING id, repo_id, run_id, task_hash, status,
                      tokens_used, tokens_saved, cache_hits,
                      policy_version, started_at, completed_at
            """,
            repo_id, run_id, task_hash, status, tokens_used,
            tokens_saved, cache_hits, policy_version,
        )
    return dict(row)


async def list_agent_runs(
    pool: asyncpg.Pool,
    repo_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, repo_id, run_id, task_hash, status,
                   tokens_used, tokens_saved, cache_hits,
                   policy_version, started_at, completed_at
            FROM   agent_runs
            WHERE  repo_id = $1
            ORDER  BY started_at DESC
            LIMIT  $2 OFFSET $3
            """,
            repo_id, limit, offset,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Row-level security context helper
# ---------------------------------------------------------------------------

async def set_org_context(conn: asyncpg.Connection, org_id: int | None) -> None:
    """Set ``app.current_org_id`` for RLS enforcement within a transaction.

    Call this inside a ``conn.transaction()`` block before issuing any
    org-scoped query on the ``events`` table.  The setting is automatically
    cleared when the transaction ends.
    """
    val = str(org_id) if org_id is not None else ""
    await conn.execute("SET LOCAL app.current_org_id = $1", val)


# ---------------------------------------------------------------------------
# Delete operations (hard delete — cascades to children via FK)
# ---------------------------------------------------------------------------

async def delete_org(pool: asyncpg.Pool, org_id: int) -> bool:
    """Delete an org and all its children.  Returns ``True`` if a row was deleted."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM orgs WHERE id = $1", org_id
        )
    return result == "DELETE 1"


async def delete_project(pool: asyncpg.Pool, project_id: int, org_id: int) -> bool:
    """Delete a project (and its repos/runs) within *org_id*."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM projects WHERE id = $1 AND org_id = $2",
            project_id, org_id,
        )
    return result == "DELETE 1"


async def delete_repo(pool: asyncpg.Pool, repo_id: int, project_id: int) -> bool:
    """Delete a repository (and its runs) within *project_id*."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM repositories WHERE id = $1 AND project_id = $2",
            repo_id, project_id,
        )
    return result == "DELETE 1"
