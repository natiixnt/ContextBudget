from __future__ import annotations

from pathlib import Path

from redcon.cache.backends import (
    LocalFileSummaryCacheBackend,
    create_summary_cache_backend,
)


def test_redis_fallback_to_local_file_when_unavailable(tmp_path: Path) -> None:
    """When Redis is unreachable, create_summary_cache_backend should fall back to local_file."""
    backend = create_summary_cache_backend(
        tmp_path,
        backend="redis",
        redis_url="redis://localhost:19999/0",  # intentionally wrong port
        redis_namespace="test-fallback",
        enabled=True,
    )
    assert isinstance(backend, LocalFileSummaryCacheBackend)


def test_redis_fallback_still_functional(tmp_path: Path) -> None:
    """The fallback local_file backend should work normally."""
    backend = create_summary_cache_backend(
        tmp_path,
        backend="redis",
        redis_url="redis://localhost:19999/0",
        redis_namespace="test-fallback",
        enabled=True,
    )
    # Should work as local_file
    assert backend.put_summary("key1", "value1") is True
    assert backend.get_summary("key1") == "value1"
