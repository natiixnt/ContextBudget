from __future__ import annotations

import asyncpg

from app.config import DATABASE_URL, READ_DATABASE_URL

_pool: asyncpg.Pool | None = None
_read_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool, _read_pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    # If READ_DATABASE_URL is configured (replica), create a separate read pool.
    # Falls back to the primary pool when not set.
    if READ_DATABASE_URL and READ_DATABASE_URL != DATABASE_URL:
        _read_pool = await asyncpg.create_pool(READ_DATABASE_URL, min_size=2, max_size=10)


async def close_pool() -> None:
    global _pool, _read_pool
    if _pool is not None:
        await _pool.close()
        _pool = None
    if _read_pool is not None:
        await _read_pool.close()
        _read_pool = None


def get_pool() -> asyncpg.Pool:
    """Return the write pool (primary)."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool


def get_read_pool() -> asyncpg.Pool:
    """Return the read pool (replica if configured, otherwise the primary).

    Use this for analytics and dashboard queries that do not need to read
    freshly committed writes.
    """
    if _read_pool is not None:
        return _read_pool
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool
