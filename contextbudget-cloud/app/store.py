from __future__ import annotations

import json
from datetime import timezone
from typing import Any

import asyncpg

from app.models import IncomingEvent

# org_id added at position $23 (before payload $24) so payload stays last
_INSERT_SQL = """
INSERT INTO events (
    name, schema_version, event_timestamp, run_id,
    command, repository_id, workspace_id,
    max_tokens, estimated_input_tokens, estimated_saved_tokens, baseline_full_context_tokens,
    scanned_files, ranked_files, included_files, skipped_files, top_files,
    cache_hits, tokens_saved_by_cache, cache_backend,
    policy_evaluated, policy_passed, violation_count,
    org_id,
    payload
) VALUES (
    $1, $2, $3, $4,
    $5, $6, $7,
    $8, $9, $10, $11,
    $12, $13, $14, $15, $16,
    $17, $18, $19,
    $20, $21, $22,
    $23,
    $24::jsonb
) RETURNING id
"""


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _extract_params(event: IncomingEvent, org_id: int | None = None) -> tuple:
    p = event.payload
    repo = p.get("repository") or {}
    tokens = p.get("tokens") or {}
    files = p.get("files") or {}
    cache = p.get("cache") or {}
    policy = p.get("policy") or {}

    ts = event.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    return (
        event.name,
        event.schema_version,
        ts,
        event.run_id,
        _str(p.get("command")),
        _str(repo.get("repository_id")),
        _str(repo.get("workspace_id")),
        _int(tokens.get("max_tokens")),
        _int(tokens.get("estimated_input_tokens")),
        _int(tokens.get("estimated_saved_tokens")),
        _int(tokens.get("baseline_full_context_tokens")),
        _int(files.get("scanned_files")),
        _int(files.get("ranked_files")),
        _int(files.get("included_files")),
        _int(files.get("skipped_files")),
        _int(files.get("top_files")),
        _int(cache.get("cache_hits")),
        _int(cache.get("tokens_saved")),
        _str(cache.get("backend")),
        _bool(policy.get("evaluated")),
        _bool(policy.get("passed")),
        _int(policy.get("violation_count")),
        org_id,       # $23 — None for unauthenticated ingest
        json.dumps(p),  # $24 — JSONB payload (always last)
    )


async def insert_events_batch(
    pool: asyncpg.Pool,
    events: list[IncomingEvent],
    *,
    org_id: int | None = None,
) -> list[int]:
    async with pool.acquire() as conn:
        ids: list[int] = []
        async with conn.transaction():
            for event in events:
                row = await conn.fetchrow(_INSERT_SQL, *_extract_params(event, org_id))
                ids.append(row["id"])
        return ids
