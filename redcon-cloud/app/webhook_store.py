"""CRUD for webhook registrations in the cloud control plane."""
from __future__ import annotations

import hashlib
import json

import asyncpg


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def create_webhook(
    pool: asyncpg.Pool,
    *,
    org_id: int,
    url: str,
    secret: str | None = None,
    events: list[str],
) -> dict:
    secret_hash = _hash_secret(secret) if secret else None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO webhooks (org_id, url, secret_hash, events)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id, org_id, url, events, active, created_at
            """,
            org_id, url, secret_hash, json.dumps(events),
        )
    r = dict(row)
    r["events"] = json.loads(r["events"]) if isinstance(r["events"], str) else r["events"]
    return r


async def list_webhooks(pool: asyncpg.Pool, org_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, org_id, url, events, active, created_at"
            " FROM webhooks WHERE org_id = $1 ORDER BY created_at",
            org_id,
        )
    result = []
    for row in rows:
        r = dict(row)
        r["events"] = json.loads(r["events"]) if isinstance(r["events"], str) else (r["events"] or [])
        result.append(r)
    return result


async def delete_webhook(pool: asyncpg.Pool, webhook_id: int, org_id: int) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM webhooks WHERE id = $1 AND org_id = $2",
            webhook_id, org_id,
        )
    return result == "DELETE 1"


async def get_webhooks_for_event(
    pool: asyncpg.Pool,
    org_id: int,
    event_type: str,
) -> list[dict]:
    """Return active webhooks subscribed to *event_type* for *org_id*."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, org_id, url, secret_hash, events
            FROM webhooks
            WHERE org_id = $1
              AND active = TRUE
              AND (events = '[]'::jsonb OR events @> $2::jsonb)
            """,
            org_id, json.dumps([event_type]),
        )
    return [dict(r) for r in rows]
