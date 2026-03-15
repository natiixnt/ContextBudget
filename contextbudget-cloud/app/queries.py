from __future__ import annotations

import asyncpg

from app.models import (
    CacheHitRateRow,
    CommandSavings,
    DashboardHeatmap,
    DashboardOverview,
    DashboardRepositories,
    DashboardSavings,
    RepositoryStats,
    TokensPerRepoRow,
    TokensPerTaskRow,
)

_TOKENS_PER_REPO_SQL = """
SELECT
    repository_id,
    SUM(estimated_input_tokens)::bigint AS total_tokens,
    COUNT(*)::int                       AS run_count
FROM events
WHERE name = 'pack_completed'
  AND repository_id IS NOT NULL
GROUP BY repository_id
ORDER BY total_tokens DESC NULLS LAST
"""

_TOKENS_PER_TASK_SQL = """
SELECT
    command,
    SUM(estimated_input_tokens)::bigint AS total_tokens,
    COUNT(*)::int                       AS run_count
FROM events
WHERE name = 'pack_completed'
  AND command IS NOT NULL
GROUP BY command
ORDER BY total_tokens DESC NULLS LAST
"""

_CACHE_HIT_RATE_SQL = """
SELECT
    COUNT(*) FILTER (WHERE cache_hits > 0)::int  AS runs_with_cache_hits,
    COUNT(*)::int                                 AS total_runs,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE cache_hits > 0) / NULLIF(COUNT(*), 0),
        2
    )                                             AS cache_hit_rate_pct,
    SUM(cache_hits)::bigint                       AS total_cache_hits,
    SUM(tokens_saved_by_cache)::bigint            AS total_tokens_saved
FROM events
WHERE name = 'pack_completed'
"""


async def tokens_per_repo(pool: asyncpg.Pool) -> list[TokensPerRepoRow]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_TOKENS_PER_REPO_SQL)
    return [
        TokensPerRepoRow(
            repository_id=r["repository_id"],
            total_tokens=r["total_tokens"],
            run_count=r["run_count"],
        )
        for r in rows
    ]


async def tokens_per_task(pool: asyncpg.Pool) -> list[TokensPerTaskRow]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_TOKENS_PER_TASK_SQL)
    return [
        TokensPerTaskRow(
            command=r["command"],
            total_tokens=r["total_tokens"],
            run_count=r["run_count"],
        )
        for r in rows
    ]


_DASHBOARD_OVERVIEW_SQL = """
SELECT
    COALESCE(SUM(estimated_input_tokens), 0)::bigint  AS total_tokens_used,
    COALESCE(SUM(estimated_saved_tokens), 0)::bigint  AS total_tokens_saved,
    COUNT(*)::int                                      AS total_runs,
    COUNT(*) FILTER (WHERE cache_hits > 0)::int        AS runs_with_cache_hits
FROM events
WHERE name = 'pack_completed'
"""

_DASHBOARD_REPOSITORIES_SQL = """
SELECT
    repository_id,
    COALESCE(SUM(estimated_input_tokens), 0)::bigint AS total_tokens_used,
    COALESCE(SUM(estimated_saved_tokens), 0)::bigint AS total_tokens_saved,
    COUNT(*)::int                                     AS run_count
FROM events
WHERE name = 'pack_completed'
  AND repository_id IS NOT NULL
GROUP BY repository_id
ORDER BY total_tokens_used DESC NULLS LAST
"""

_DASHBOARD_SAVINGS_BY_COMMAND_SQL = """
SELECT
    command,
    COALESCE(SUM(estimated_input_tokens), 0)::bigint AS tokens_used,
    COALESCE(SUM(estimated_saved_tokens), 0)::bigint AS tokens_saved,
    COUNT(*)::int                                     AS run_count
FROM events
WHERE name = 'pack_completed'
GROUP BY command
ORDER BY tokens_used DESC NULLS LAST
"""

_DASHBOARD_HEATMAP_SQL = """
SELECT
    COUNT(*)::int              AS total_runs,
    ROUND(AVG(scanned_files), 1) AS avg_scanned_files,
    ROUND(AVG(included_files), 1) AS avg_included_files,
    ROUND(AVG(top_files), 1)   AS avg_top_files
FROM events
WHERE name = 'pack_completed'
"""


async def dashboard_overview(pool: asyncpg.Pool) -> DashboardOverview:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_DASHBOARD_OVERVIEW_SQL)
    used = row["total_tokens_used"] or 0
    saved = row["total_tokens_saved"] or 0
    total = used + saved
    total_runs = row["total_runs"] or 0
    runs_with_hits = row["runs_with_cache_hits"] or 0
    return DashboardOverview(
        total_tokens_used=used,
        total_tokens_saved=saved,
        savings_rate=round(saved / total, 4) if total > 0 else None,
        cache_hit_rate_pct=round(100.0 * runs_with_hits / total_runs, 2) if total_runs > 0 else None,
        total_runs=total_runs,
        runs_with_cache_hits=runs_with_hits,
    )


async def dashboard_repositories(pool: asyncpg.Pool) -> DashboardRepositories:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_DASHBOARD_REPOSITORIES_SQL)
    repos: list[RepositoryStats] = []
    for r in rows:
        used = r["total_tokens_used"] or 0
        saved = r["total_tokens_saved"] or 0
        total = used + saved
        repos.append(RepositoryStats(
            repository_id=r["repository_id"],
            total_tokens_used=used,
            total_tokens_saved=saved,
            run_count=r["run_count"],
            savings_rate=round(saved / total, 4) if total > 0 else None,
        ))
    return DashboardRepositories(repositories=repos)


async def dashboard_savings(pool: asyncpg.Pool) -> DashboardSavings:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_DASHBOARD_SAVINGS_BY_COMMAND_SQL)
    by_command: list[CommandSavings] = []
    total_used = 0
    total_saved = 0
    for r in rows:
        used = r["tokens_used"] or 0
        saved = r["tokens_saved"] or 0
        total = used + saved
        total_used += used
        total_saved += saved
        by_command.append(CommandSavings(
            command=r["command"],
            tokens_used=used,
            tokens_saved=saved,
            run_count=r["run_count"],
            savings_rate=round(saved / total, 4) if total > 0 else None,
        ))
    grand_total = total_used + total_saved
    return DashboardSavings(
        total_tokens_used=total_used,
        total_tokens_saved=total_saved,
        savings_rate=round(total_saved / grand_total, 4) if grand_total > 0 else None,
        by_command=by_command,
    )


async def dashboard_heatmap(pool: asyncpg.Pool) -> DashboardHeatmap:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_DASHBOARD_HEATMAP_SQL)
    return DashboardHeatmap(
        total_runs=row["total_runs"] or 0,
        avg_scanned_files=float(row["avg_scanned_files"]) if row["avg_scanned_files"] is not None else None,
        avg_included_files=float(row["avg_included_files"]) if row["avg_included_files"] is not None else None,
        avg_top_files=float(row["avg_top_files"]) if row["avg_top_files"] is not None else None,
        note="File-level paths are not stored in the cloud service. Metrics show aggregate counts per run.",
    )


async def cache_hit_rate(pool: asyncpg.Pool) -> CacheHitRateRow:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_CACHE_HIT_RATE_SQL)
    if row is None:
        return CacheHitRateRow(
            runs_with_cache_hits=0,
            total_runs=0,
            cache_hit_rate_pct=None,
            total_cache_hits=None,
            total_tokens_saved=None,
        )
    return CacheHitRateRow(
        runs_with_cache_hits=row["runs_with_cache_hits"] or 0,
        total_runs=row["total_runs"] or 0,
        cache_hit_rate_pct=float(row["cache_hit_rate_pct"]) if row["cache_hit_rate_pct"] is not None else None,
        total_cache_hits=row["total_cache_hits"],
        total_tokens_saved=row["total_tokens_saved"],
    )
