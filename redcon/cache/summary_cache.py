from __future__ import annotations

"""Backward-compatible imports for summary cache backends."""

from redcon.cache.backends import (
    CacheStats,
    InMemorySummaryCacheBackend,
    LocalFileSummaryCacheBackend,
    RedisSummaryCacheBackend,
    SQLiteSummaryCacheBackend,
    SharedSummaryCacheBackendStub,
    SummaryCacheBackend,
    build_redis_cache_key,
    cache_report_as_dict,
    create_summary_cache_backend,
    normalize_cache_backend_name,
    normalize_cache_report,
)


class SummaryCache(LocalFileSummaryCacheBackend):
    """Backward-compatible name for the default local file cache backend."""


__all__ = [
    "CacheStats",
    "InMemorySummaryCacheBackend",
    "LocalFileSummaryCacheBackend",
    "RedisSummaryCacheBackend",
    "SQLiteSummaryCacheBackend",
    "SharedSummaryCacheBackendStub",
    "SummaryCache",
    "SummaryCacheBackend",
    "build_redis_cache_key",
    "cache_report_as_dict",
    "create_summary_cache_backend",
    "normalize_cache_backend_name",
    "normalize_cache_report",
]
