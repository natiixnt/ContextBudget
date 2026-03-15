from __future__ import annotations

"""Usage-quota enforcement for per-org token allowances.

Schema extension
----------------
The ``005_quotas.sql`` migration adds a ``org_quotas`` table.  This module
queries/updates those tables.

Quota behaviour
---------------
* If an org has no quota row, ingestion is unrestricted.
* If ``token_allowance_monthly`` is set, each batch of ingested events
  is checked against the current month's accumulated ``estimated_input_tokens``.
* If the allowance is exhausted the request is rejected with HTTP 429.
"""

from typing import Any


async def get_quota(pool: Any, org_id: int) -> dict | None:
    """Return the quota row for *org_id*, or ``None`` if no quota is set."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM org_quotas WHERE org_id = $1",
            org_id,
        )
        return dict(row) if row else None


async def set_quota(
    pool: Any,
    org_id: int,
    *,
    token_allowance_monthly: int | None = None,
    event_allowance_monthly: int | None = None,
) -> dict:
    """Upsert the quota for *org_id*."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO org_quotas (org_id, token_allowance_monthly, event_allowance_monthly)
            VALUES ($1, $2, $3)
            ON CONFLICT (org_id) DO UPDATE
                SET token_allowance_monthly  = EXCLUDED.token_allowance_monthly,
                    event_allowance_monthly  = EXCLUDED.event_allowance_monthly,
                    updated_at               = NOW()
            RETURNING *
            """,
            org_id,
            token_allowance_monthly,
            event_allowance_monthly,
        )
        return dict(row)


async def get_monthly_usage(pool: Any, org_id: int) -> dict:
    """Return (tokens_used, events_ingested) for the current calendar month."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(estimated_input_tokens), 0)::bigint AS tokens_used,
                COUNT(*)::bigint                                  AS events_count
            FROM events
            WHERE org_id = $1
              AND created_at >= date_trunc('month', NOW())
            """,
            org_id,
        )
        return {"tokens_used": row["tokens_used"], "events_count": row["events_count"]}


async def check_quota(
    pool: Any,
    org_id: int,
    incoming_tokens: int,
    incoming_events: int,
) -> tuple[bool, str | None]:
    """Check whether ingesting *incoming_tokens* / *incoming_events* is allowed.

    Returns
    -------
    (allowed: bool, reason: str | None)
        ``allowed=True`` → proceed.
        ``allowed=False`` → reject; *reason* describes which limit was hit.
    """
    quota = await get_quota(pool, org_id)
    if quota is None:
        return True, None  # no quota configured — unrestricted

    usage = await get_monthly_usage(pool, org_id)

    if quota.get("token_allowance_monthly") is not None:
        used = usage["tokens_used"]
        allowance = quota["token_allowance_monthly"]
        if used + incoming_tokens > allowance:
            return False, f"Monthly token quota exhausted ({used}/{allowance})"

    if quota.get("event_allowance_monthly") is not None:
        used = usage["events_count"]
        allowance = quota["event_allowance_monthly"]
        if used + incoming_events > allowance:
            return False, f"Monthly event quota exhausted ({used}/{allowance})"

    return True, None
