from __future__ import annotations

"""Tests for RedisSummaryCacheBackend using fakeredis."""

import pytest

pytest.importorskip("fakeredis", reason="fakeredis is required for Redis backend tests")
pytest.importorskip("redis", reason="redis package is required for Redis backend tests")

import fakeredis  # noqa: E402
import redis as _redis_module  # noqa: E402

from pathlib import Path

from contextbudget.cache.backends import (
    RedisSummaryCacheBackend,
    build_redis_cache_key,
    create_summary_cache_backend,
    normalize_cache_backend_name,
)
from contextbudget.config import default_config
from contextbudget.stages.workflow import run_pack_stage, run_scan_stage, run_score_stage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend(namespace: str = "test", ttl: int = 3600) -> RedisSummaryCacheBackend:
    """Return a RedisSummaryCacheBackend wired to an in-process fakeredis server."""
    backend = RedisSummaryCacheBackend(
        redis_url="redis://localhost:6379/0",
        namespace=namespace,
        ttl_seconds=ttl,
    )
    # Inject a fakeredis client so no real Redis server is needed
    backend._redis = fakeredis.FakeRedis()
    return backend


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit tests – backend behaviour
# ---------------------------------------------------------------------------

def test_backend_name() -> None:
    assert RedisSummaryCacheBackend.backend_name == "redis"


def test_normalize_cache_backend_name_redis() -> None:
    assert normalize_cache_backend_name("redis") == "redis"


def test_summary_miss_on_empty_cache() -> None:
    backend = _make_backend()
    assert backend.get_summary("missing-key") is None
    assert backend.stats.misses == 1
    assert backend.stats.hits == 0


def test_summary_roundtrip() -> None:
    backend = _make_backend()
    backend.put_summary("key1", "summary text")
    result = backend.get_summary("key1")
    assert result == "summary text"
    assert backend.stats.hits == 1
    assert backend.stats.misses == 0


def test_summary_write_returns_true_for_new_key() -> None:
    backend = _make_backend()
    assert backend.put_summary("new-key", "value") is True


def test_summary_write_returns_false_for_existing_key() -> None:
    backend = _make_backend()
    backend.put_summary("existing", "v1")
    assert backend.put_summary("existing", "v2") is False


def test_fragment_miss_on_empty_cache() -> None:
    backend = _make_backend()
    assert backend.get_fragment("frag-key") is None
    assert backend.stats.fragment_misses == 1


def test_fragment_roundtrip() -> None:
    backend = _make_backend()
    backend.put_fragment("frag-key", "ref-abc123")
    result = backend.get_fragment("frag-key")
    assert result == "ref-abc123"
    assert backend.stats.fragment_hits == 1
    assert backend.stats.fragment_writes == 1


def test_namespace_isolation() -> None:
    """Keys in different namespaces must not collide."""
    a = _make_backend(namespace="org-a")
    b = _make_backend(namespace="org-b")
    # Wire both to the same server
    server = fakeredis.FakeRedis()
    a._redis = server
    b._redis = server

    a.put_summary("shared-key", "from-a")
    assert b.get_summary("shared-key") is None
    assert a.get_summary("shared-key") == "from-a"


def test_disabled_backend_never_reads_or_writes() -> None:
    backend = _make_backend()
    backend.enabled = False
    backend.put_summary("k", "v")
    assert backend.get_summary("k") is None
    assert backend.stats.hits == 0
    assert backend.stats.misses == 0
    assert backend.stats.writes == 0


def test_zlib_compression_is_transparent() -> None:
    """Large values are compressed; the caller receives the original string."""
    backend = _make_backend()
    big = "x" * 50_000
    backend.put_summary("big", big)
    assert backend.get_summary("big") == big


def test_snapshot_reflects_stats() -> None:
    backend = _make_backend()
    backend.put_summary("k1", "v1")
    backend.get_summary("k1")
    backend.get_summary("missing")
    backend.record_tokens_saved(42)

    report = backend.snapshot()
    assert report.backend == "redis"
    assert report.hits == 1
    assert report.misses == 1
    assert report.writes == 1
    assert report.tokens_saved == 42


# ---------------------------------------------------------------------------
# build_redis_cache_key
# ---------------------------------------------------------------------------

def test_build_redis_cache_key_basic() -> None:
    key = build_redis_cache_key(
        org="acme",
        repo="backend",
        file_path="src/auth.py",
        symbol_or_slice="def login",
        content_hash="abc123",
    )
    assert "acme" in key
    assert "backend" in key
    assert "src" in key
    assert "auth.py" in key


def test_build_redis_cache_key_is_deterministic() -> None:
    kwargs = dict(
        org="acme",
        repo="backend",
        file_path="src/auth.py",
        symbol_or_slice="def login",
        content_hash="deadbeef",
    )
    assert build_redis_cache_key(**kwargs) == build_redis_cache_key(**kwargs)


def test_build_redis_cache_key_differs_for_different_content_hash() -> None:
    base = dict(org="a", repo="b", file_path="c.py", symbol_or_slice="fn", content_hash="hash1")
    other = {**base, "content_hash": "hash2"}
    assert build_redis_cache_key(**base) != build_redis_cache_key(**other)


# ---------------------------------------------------------------------------
# Integration – cache reuse across pipeline runs
# ---------------------------------------------------------------------------

def test_cache_reuse_across_runs(tmp_path: Path) -> None:
    """Second pipeline run must hit cache for entries written by the first run."""
    _write(tmp_path / "src" / "large.py", "\n".join(f"line {i}" for i in range(2000)) + "\n")

    cfg = default_config()
    files = run_scan_stage(tmp_path, cfg)
    ranked = run_score_stage("update feature", files, cfg)

    # Both runs share a single in-process Redis server
    shared_redis = fakeredis.FakeRedis()

    def _make_shared_backend() -> RedisSummaryCacheBackend:
        b = RedisSummaryCacheBackend(namespace="test", ttl_seconds=3600)
        b._redis = shared_redis
        return b

    first_cache = _make_shared_backend()
    first = run_pack_stage("update feature", tmp_path, ranked, 500, first_cache, cfg)

    second_cache = _make_shared_backend()
    second = run_pack_stage("update feature", tmp_path, ranked, 500, second_cache, cfg)

    assert first.cache.backend == "redis"
    assert first.cache.misses >= 1

    assert second.cache.backend == "redis"
    assert second.cache.hits >= 1, "Second run must reuse entries cached by the first run"


def test_token_savings_recorded_on_fragment_reuse(tmp_path: Path) -> None:
    """Fragment reuse must produce measurable token savings."""
    _write(
        tmp_path / "src" / "auth.py",
        "def login(token: str) -> bool:\n    return token.startswith('prod_')\n",
    )

    cfg = default_config()
    cfg.compression.full_file_threshold_tokens = 1000
    cfg.compression.snippet_score_threshold = 999.0
    files = run_scan_stage(tmp_path, cfg)
    ranked = run_score_stage("update auth flow", files, cfg)

    shared_redis = fakeredis.FakeRedis()

    def _make_shared() -> RedisSummaryCacheBackend:
        b = RedisSummaryCacheBackend(namespace="test", ttl_seconds=3600)
        b._redis = shared_redis
        return b

    first_cache = _make_shared()
    first = run_pack_stage("update auth flow", tmp_path, ranked, 1000, first_cache, cfg)

    second_cache = _make_shared()
    second = run_pack_stage("update auth flow", tmp_path, ranked, 1000, second_cache, cfg)

    assert first.cache.fragment_misses >= 1
    assert first.cache.tokens_saved == 0
    assert first.compressed_files[0].cache_status == "stored"

    assert second.cache.fragment_hits >= 1
    assert second.compressed_files[0].cache_status == "reused"
    assert second.cache.tokens_saved == (
        first.compressed_files[0].compressed_tokens - second.compressed_files[0].compressed_tokens
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_create_summary_cache_backend_redis(tmp_path: Path) -> None:
    backend = create_summary_cache_backend(
        tmp_path,
        backend="redis",
        redis_url="redis://localhost:6379/0",
        redis_namespace="myorg:myrepo",
        redis_ttl_seconds=7200,
    )
    assert isinstance(backend, RedisSummaryCacheBackend)
    assert backend.namespace == "myorg:myrepo"
    assert backend.ttl_seconds == 7200


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_connection_failure_degrades_gracefully() -> None:
    """get_summary and put_summary must not raise on Redis connection errors."""

    class _FailingRedis:
        def get(self, key: str) -> None:
            raise ConnectionError("simulated connection failure")

        def exists(self, key: str) -> int:
            raise ConnectionError("simulated connection failure")

        def setex(self, key: str, ttl: int, value: bytes) -> None:
            raise ConnectionError("simulated connection failure")

        def set(self, key: str, value: bytes) -> None:
            raise ConnectionError("simulated connection failure")

    backend = RedisSummaryCacheBackend(namespace="test", ttl_seconds=3600)
    backend._redis = _FailingRedis()

    assert backend.get_summary("k") is None
    assert backend.put_summary("k", "v") is False
    assert backend.get_fragment("fk") is None
    assert backend.put_fragment("fk", "ref") is False


def test_ttl_zero_stores_without_expiry() -> None:
    """When ttl_seconds=0, values are stored persistently (no EXPIRY set)."""
    server = fakeredis.FakeServer()
    backend = RedisSummaryCacheBackend(namespace="test", ttl_seconds=0)
    backend._redis = fakeredis.FakeRedis(server=server)

    backend.put_summary("persistent-key", "persistent-value")

    # Value must still be readable on a fresh client connected to the same server
    reader = fakeredis.FakeRedis(server=server)
    raw = reader.get("test:s:persistent-key")
    assert raw is not None, "Key must exist without TTL"
    assert reader.ttl("test:s:persistent-key") == -1, "Key must have no expiry"


def test_cross_instance_reuse_via_fake_server(tmp_path: Path) -> None:
    """Two independent backend instances sharing a FakeServer reuse each other's entries."""
    _write(tmp_path / "src" / "large.py", "\n".join(f"line {i}" for i in range(2000)) + "\n")

    cfg = default_config()
    files = run_scan_stage(tmp_path, cfg)
    ranked = run_score_stage("cross-instance task", files, cfg)

    server = fakeredis.FakeServer()

    def _make_instance() -> RedisSummaryCacheBackend:
        b = RedisSummaryCacheBackend(namespace="org:repo", ttl_seconds=3600)
        b._redis = fakeredis.FakeRedis(server=server)
        return b

    first_cache = _make_instance()
    first = run_pack_stage("cross-instance task", tmp_path, ranked, 500, first_cache, cfg)

    second_cache = _make_instance()
    second = run_pack_stage("cross-instance task", tmp_path, ranked, 500, second_cache, cfg)

    assert first.cache.misses >= 1
    assert second.cache.hits >= 1, "Second independent instance must hit entries from the first"
