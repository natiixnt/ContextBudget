"""API key issuance and verification.

Keys have the form ``cbk_<64 hex chars>`` (prefix + 32 random bytes).
Only a SHA-256 hash of the raw key is stored in the database; the raw
key is returned once at issuance and never again.
"""
from __future__ import annotations

import hashlib
import secrets

import asyncpg

_KEY_PREFIX = "cbk_"
_KEY_BYTES = 32  # 32 random bytes → 64 hex chars


def generate_api_key() -> tuple[str, str]:
    """Return ``(raw_key, key_hash)``.

    Store *key_hash* in the database and return *raw_key* to the caller
    exactly once.  The raw key cannot be recovered from the hash.
    """
    raw = _KEY_PREFIX + secrets.token_hex(_KEY_BYTES)
    return raw, _hash(raw)


def hash_api_key(raw: str) -> str:
    """Hash a raw key for storage or comparison."""
    return _hash(raw)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def verify_api_key(pool: asyncpg.Pool, raw_key: str) -> dict | None:
    """Verify *raw_key* against the database.

    Returns a dict with ``key_id``, ``org_id``, and ``org_slug`` if the key
    is valid, not revoked, and not expired; ``None`` otherwise.
    """
    if not isinstance(raw_key, str) or not raw_key.startswith(_KEY_PREFIX):
        return None
    key_hash = _hash(raw_key)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ak.id AS key_id, ak.org_id, o.slug AS org_slug
            FROM   api_keys ak
            JOIN   orgs o ON o.id = ak.org_id
            WHERE  ak.key_hash = $1
              AND  ak.revoked = FALSE
              AND  (ak.expires_at IS NULL OR ak.expires_at > NOW())
            """,
            key_hash,
        )
    if row is None:
        return None
    return {"key_id": row["key_id"], "org_id": row["org_id"], "org_slug": row["org_slug"]}
