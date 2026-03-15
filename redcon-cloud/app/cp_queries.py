"""Cost analytics queries against the events table."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Cost summary
# ---------------------------------------------------------------------------

_COST_SUMMARY_SQL = """
SELECT
    COALESCE(SUM(baseline_full_context_tokens), 0)::bigint  AS baseline_tokens,
    COALESCE(SUM(estimated_input_tokens),       0)::bigint  AS optimized_tokens,
    COALESCE(SUM(estimated_saved_tokens),       0)::bigint  AS tokens_saved,
    COUNT(*)::int                                            AS run_count
FROM events
WHERE name = 'pack_completed'
  AND ($1::text        IS NULL OR repository_id    = $1)
  AND ($2::timestamptz IS NULL OR event_timestamp >= $2)
  AND ($3::timestamptz IS NULL OR event_timestamp <  $3)
"""


async def cost_summary(
    pool: asyncpg.Pool,
    *,
    repository_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_COST_SUMMARY_SQL, repository_id, from_date, to_date)
    baseline   = row["baseline_tokens"]  or 0
    optimized  = row["optimized_tokens"] or 0
    saved      = row["tokens_saved"]     or 0
    denom      = optimized + saved
    return {
        "baseline_tokens":  baseline,
        "optimized_tokens": optimized,
        "tokens_saved":     saved,
        "savings_rate":     round(saved / denom, 4) if denom > 0 else None,
        "run_count":        row["run_count"] or 0,
    }


# ---------------------------------------------------------------------------
# Cost by repository
# ---------------------------------------------------------------------------

_COST_BY_REPO_SQL = """
SELECT
    repository_id,
    COALESCE(SUM(baseline_full_context_tokens), 0)::bigint  AS baseline_tokens,
    COALESCE(SUM(estimated_input_tokens),       0)::bigint  AS optimized_tokens,
    COALESCE(SUM(estimated_saved_tokens),       0)::bigint  AS tokens_saved,
    COUNT(*)::int                                            AS run_count
FROM events
WHERE name = 'pack_completed'
  AND repository_id IS NOT NULL
  AND ($1::timestamptz IS NULL OR event_timestamp >= $1)
  AND ($2::timestamptz IS NULL OR event_timestamp <  $2)
GROUP BY repository_id
ORDER BY optimized_tokens DESC
"""


async def cost_by_repo(
    pool: asyncpg.Pool,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_COST_BY_REPO_SQL, from_date, to_date)
    result = []
    for r in rows:
        optimized = r["optimized_tokens"] or 0
        saved     = r["tokens_saved"]     or 0
        denom     = optimized + saved
        result.append({
            "repository_id":  r["repository_id"],
            "baseline_tokens":  r["baseline_tokens"]  or 0,
            "optimized_tokens": optimized,
            "tokens_saved":     saved,
            "savings_rate":     round(saved / denom, 4) if denom > 0 else None,
            "run_count":        r["run_count"] or 0,
        })
    return result


# ---------------------------------------------------------------------------
# Cost by date
# ---------------------------------------------------------------------------

_COST_BY_DATE_SQL = """
SELECT
    DATE_TRUNC('day', event_timestamp)::date               AS date,
    COALESCE(SUM(baseline_full_context_tokens), 0)::bigint  AS baseline_tokens,
    COALESCE(SUM(estimated_input_tokens),       0)::bigint  AS optimized_tokens,
    COALESCE(SUM(estimated_saved_tokens),       0)::bigint  AS tokens_saved,
    COUNT(*)::int                                            AS run_count
FROM events
WHERE name = 'pack_completed'
  AND ($1::text        IS NULL OR repository_id    = $1)
  AND ($2::timestamptz IS NULL OR event_timestamp >= $2)
  AND ($3::timestamptz IS NULL OR event_timestamp <  $3)
GROUP BY 1
ORDER BY 1 DESC
"""


async def cost_by_date(
    pool: asyncpg.Pool,
    *,
    repository_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_COST_BY_DATE_SQL, repository_id, from_date, to_date)
    result = []
    for r in rows:
        optimized = r["optimized_tokens"] or 0
        saved     = r["tokens_saved"]     or 0
        denom     = optimized + saved
        result.append({
            "date":             str(r["date"]),
            "baseline_tokens":  r["baseline_tokens"]  or 0,
            "optimized_tokens": optimized,
            "tokens_saved":     saved,
            "savings_rate":     round(saved / denom, 4) if denom > 0 else None,
            "run_count":        r["run_count"] or 0,
        })
    return result


# ---------------------------------------------------------------------------
# Cost by agent run (run-level attribution)
# ---------------------------------------------------------------------------

_COST_BY_RUN_SQL = """
SELECT
    run_id,
    repository_id,
    command,
    event_timestamp                                          AS run_at,
    COALESCE(baseline_full_context_tokens, 0)::bigint        AS baseline_tokens,
    COALESCE(estimated_input_tokens, 0)::bigint              AS optimized_tokens,
    COALESCE(estimated_saved_tokens, 0)::bigint              AS tokens_saved,
    COALESCE(cache_hits, 0)::int                             AS cache_hits,
    COALESCE(tokens_saved_by_cache, 0)::bigint               AS tokens_saved_by_cache
FROM events
WHERE name = 'pack_completed'
  AND ($1::text        IS NULL OR repository_id    = $1)
  AND ($2::timestamptz IS NULL OR event_timestamp >= $2)
  AND ($3::timestamptz IS NULL OR event_timestamp <  $3)
  AND ($4::bigint      IS NULL OR org_id           = $4)
ORDER BY event_timestamp DESC
LIMIT $5
"""


async def cost_by_run(
    pool: asyncpg.Pool,
    *,
    repository_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    org_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _COST_BY_RUN_SQL, repository_id, from_date, to_date, org_id, limit
        )
    result = []
    for r in rows:
        optimized = r["optimized_tokens"] or 0
        saved     = r["tokens_saved"]     or 0
        denom     = optimized + saved
        result.append({
            "run_id":                r["run_id"],
            "repository_id":         r["repository_id"],
            "command":               r["command"],
            "run_at":                r["run_at"].isoformat() if r["run_at"] else None,
            "baseline_tokens":       r["baseline_tokens"]  or 0,
            "optimized_tokens":      optimized,
            "tokens_saved":          saved,
            "savings_rate":          round(saved / denom, 4) if denom > 0 else None,
            "cache_hits":            r["cache_hits"]            or 0,
            "tokens_saved_by_cache": r["tokens_saved_by_cache"] or 0,
        })
    return result


# ---------------------------------------------------------------------------
# Cost saved by optimization stage
# Stage attribution: ranking/compression saves = estimated_saved_tokens - tokens_saved_by_cache
# Cache saves = tokens_saved_by_cache
# ---------------------------------------------------------------------------

_COST_BY_STAGE_SQL = """
SELECT
    COALESCE(SUM(estimated_saved_tokens), 0)::bigint         AS total_tokens_saved,
    COALESCE(SUM(tokens_saved_by_cache), 0)::bigint          AS cache_tokens_saved,
    COALESCE(SUM(
        GREATEST(0, COALESCE(estimated_saved_tokens, 0) - COALESCE(tokens_saved_by_cache, 0))
    ), 0)::bigint                                            AS compression_tokens_saved,
    COALESCE(SUM(estimated_input_tokens), 0)::bigint         AS total_optimized_tokens,
    COALESCE(SUM(baseline_full_context_tokens), 0)::bigint   AS total_baseline_tokens,
    COUNT(*)::int                                            AS run_count
FROM events
WHERE name = 'pack_completed'
  AND ($1::text        IS NULL OR repository_id    = $1)
  AND ($2::timestamptz IS NULL OR event_timestamp >= $2)
  AND ($3::timestamptz IS NULL OR event_timestamp <  $3)
  AND ($4::bigint      IS NULL OR org_id           = $4)
"""


async def cost_by_stage(
    pool: asyncpg.Pool,
    *,
    repository_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    org_id: int | None = None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _COST_BY_STAGE_SQL, repository_id, from_date, to_date, org_id
        )
    total_saved       = row["total_tokens_saved"]       or 0
    cache_saved       = row["cache_tokens_saved"]       or 0
    compression_saved = row["compression_tokens_saved"] or 0
    optimized         = row["total_optimized_tokens"]   or 0
    baseline          = row["total_baseline_tokens"]    or 0
    run_count         = row["run_count"]                or 0
    denom             = optimized + total_saved
    return {
        "run_count":              run_count,
        "total_baseline_tokens":  baseline,
        "total_optimized_tokens": optimized,
        "total_tokens_saved":     total_saved,
        "overall_savings_rate":   round(total_saved / denom, 4) if denom > 0 else None,
        "stages": {
            "compression_and_ranking": {
                "tokens_saved":  compression_saved,
                "savings_rate":  round(compression_saved / denom, 4) if denom > 0 else None,
                "description":   "Savings from file ranking, compression, and summarization",
            },
            "cache": {
                "tokens_saved":  cache_saved,
                "savings_rate":  round(cache_saved / denom, 4) if denom > 0 else None,
                "description":   "Savings from prompt cache hits (reused unchanged files)",
            },
        },
    }


# ---------------------------------------------------------------------------
# ROI summary (for dashboard / sales demos)
# ---------------------------------------------------------------------------

_ROI_TOP_REPOS_SQL = """
SELECT
    repository_id,
    COALESCE(SUM(estimated_input_tokens), 0)::bigint         AS tokens_used,
    COALESCE(SUM(estimated_saved_tokens), 0)::bigint         AS tokens_saved,
    COALESCE(SUM(baseline_full_context_tokens), 0)::bigint   AS baseline_tokens,
    COUNT(*)::int                                            AS run_count
FROM events
WHERE name = 'pack_completed'
  AND repository_id IS NOT NULL
  AND ($1::bigint IS NULL OR org_id = $1)
GROUP BY repository_id
ORDER BY tokens_used DESC
LIMIT 10
"""

_ROI_OVERVIEW_SQL = """
SELECT
    COALESCE(SUM(estimated_input_tokens), 0)::bigint        AS total_tokens_used,
    COALESCE(SUM(estimated_saved_tokens), 0)::bigint        AS total_tokens_saved,
    COALESCE(SUM(baseline_full_context_tokens), 0)::bigint  AS total_baseline_tokens,
    COUNT(*)::int                                            AS total_runs,
    COUNT(*) FILTER (WHERE cache_hits > 0)::int             AS runs_with_cache_hits
FROM events
WHERE name = 'pack_completed'
  AND ($1::bigint IS NULL OR org_id = $1)
"""


async def roi_summary(
    pool: asyncpg.Pool,
    *,
    org_id: int | None = None,
    price_per_1m: float = 15.0,
) -> dict[str, Any]:
    """Compute ROI summary suitable for a customer-facing dashboard or sales demo."""
    async with pool.acquire() as conn:
        overview = await conn.fetchrow(_ROI_OVERVIEW_SQL, org_id)
        repo_rows = await conn.fetch(_ROI_TOP_REPOS_SQL, org_id)

    used     = overview["total_tokens_used"]    or 0
    saved    = overview["total_tokens_saved"]   or 0
    baseline = overview["total_baseline_tokens"] or 0
    runs     = overview["total_runs"]           or 0
    hits     = overview["runs_with_cache_hits"] or 0
    denom    = used + saved

    dollars_saved = round(saved / 1_000_000 * price_per_1m, 4)
    savings_rate  = round(saved / denom, 4) if denom > 0 else None
    cache_hit_pct = round(100.0 * hits / runs, 2) if runs > 0 else None

    top_repos = []
    for r in repo_rows:
        r_used   = r["tokens_used"]  or 0
        r_saved  = r["tokens_saved"] or 0
        r_denom  = r_used + r_saved
        top_repos.append({
            "repository_id":   r["repository_id"],
            "tokens_used":     r_used,
            "tokens_saved":    r_saved,
            "baseline_tokens": r["baseline_tokens"] or 0,
            "run_count":       r["run_count"] or 0,
            "savings_rate":    round(r_saved / r_denom, 4) if r_denom > 0 else None,
            "dollars_saved":   round(r_saved / 1_000_000 * price_per_1m, 4),
        })

    return {
        "total_tokens_used":    used,
        "total_tokens_saved":   saved,
        "total_baseline_tokens": baseline,
        "savings_rate":          savings_rate,
        "estimated_dollars_saved": dollars_saved,
        "cache_hit_rate_pct":    cache_hit_pct,
        "total_runs":            runs,
        "runs_with_cache_hits":  hits,
        "price_per_1m_tokens":   price_per_1m,
        "top_repos":             top_repos,
        "note": (
            f"Dollar estimates use ${price_per_1m:.2f}/MTok input rate. "
            "Pass ?price_per_1m=X to override."
        ),
    }
