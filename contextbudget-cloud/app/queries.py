from __future__ import annotations

import asyncpg

from app.models import CacheHitRateRow, TokensPerRepoRow, TokensPerTaskRow

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
