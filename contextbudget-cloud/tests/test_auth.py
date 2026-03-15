"""Tests for API key hashing and verification (no database required)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.auth import _hash, generate_api_key, hash_api_key, verify_api_key


def test_generate_api_key_returns_two_strings():
    raw, hashed = generate_api_key()
    assert isinstance(raw, str)
    assert isinstance(hashed, str)


def test_raw_key_starts_with_prefix():
    raw, _ = generate_api_key()
    assert raw.startswith("cbk_")


def test_raw_key_length():
    raw, _ = generate_api_key()
    # "cbk_" (4) + 64 hex chars = 68
    assert len(raw) == 68


def test_hash_is_sha256_hex():
    _, hashed = generate_api_key()
    # SHA-256 hex digest is 64 chars
    assert len(hashed) == 64
    assert all(c in "0123456789abcdef" for c in hashed)


def test_hash_is_deterministic():
    raw, hashed = generate_api_key()
    assert hash_api_key(raw) == hashed
    assert _hash(raw) == hashed


def test_different_keys_produce_different_hashes():
    _, h1 = generate_api_key()
    _, h2 = generate_api_key()
    assert h1 != h2


def test_key_prefix_for_display():
    raw, _ = generate_api_key()
    prefix = raw[:12]
    assert prefix.startswith("cbk_")
    assert len(prefix) == 12


# ---------------------------------------------------------------------------
# verify_api_key — DB-mocked tests
# ---------------------------------------------------------------------------

import pytest


@pytest.mark.asyncio
async def test_verify_api_key_returns_none_for_wrong_prefix():
    mock_pool = MagicMock()
    result = await verify_api_key(mock_pool, "sk_not_a_cbk_key")
    assert result is None


@pytest.mark.asyncio
async def test_verify_api_key_returns_none_for_non_string():
    mock_pool = MagicMock()
    result = await verify_api_key(mock_pool, None)
    assert result is None


@pytest.mark.asyncio
async def test_verify_api_key_returns_context_on_valid_key():
    raw, _ = generate_api_key()
    mock_row = {"key_id": 1, "org_id": 5, "org_slug": "acme"}
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))

    result = await verify_api_key(mock_pool, raw)
    assert result is not None
    assert result["key_id"] == 1
    assert result["org_id"] == 5
    assert result["org_slug"] == "acme"
    assert "key_hash" in result  # key_hash is now included for rate-limiting


@pytest.mark.asyncio
async def test_verify_api_key_returns_none_when_db_returns_none():
    raw, _ = generate_api_key()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))

    result = await verify_api_key(mock_pool, raw)
    assert result is None
