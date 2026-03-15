from __future__ import annotations

"""Backward-compatible imports for summary cache backends."""

from contextbudget.cache.backends import (
    CacheStats,
    InMemorySummaryCacheBackend,
    LocalFileSummaryCacheBackend,
    SharedSummaryCacheBackendStub,
    SummaryCacheBackend,
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
    "SharedSummaryCacheBackendStub",
    "SummaryCache",
    "SummaryCacheBackend",
    "cache_report_as_dict",
    "create_summary_cache_backend",
    "normalize_cache_backend_name",
    "normalize_cache_report",
]
