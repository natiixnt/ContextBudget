from __future__ import annotations

"""Cache backend abstractions and built-in implementations."""

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from contextbudget.schemas.models import CACHE_FILE, CacheReport


@dataclass(slots=True)
class CacheStats:
    """Runtime counters collected by a cache backend."""

    hits: int = 0
    misses: int = 0
    writes: int = 0


class SummaryCacheBackend(ABC):
    """Abstract summary-cache backend used by the compression pipeline."""

    backend_name = "unknown"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.stats = CacheStats()

    def get_summary(self, key: str) -> str | None:
        """Lookup a cached summary and update hit/miss counters."""

        if not self.enabled:
            return None
        summary = self._get_summary(key)
        if summary is None:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        return summary

    def put_summary(self, key: str, summary: str) -> None:
        """Store a summary if the backend accepts writes."""

        if not self.enabled:
            return
        if self._put_summary(key, summary):
            self.stats.writes += 1

    def save(self) -> None:
        """Flush backend state if needed."""

        if not self.enabled:
            return
        self._save()

    def snapshot(self) -> CacheReport:
        """Return artifact-friendly cache metadata."""

        return CacheReport(
            backend=self.backend_name,
            enabled=self.enabled,
            hits=self.stats.hits,
            misses=self.stats.misses,
            writes=self.stats.writes,
        )

    @abstractmethod
    def _get_summary(self, key: str) -> str | None:
        """Return a cached summary for ``key`` if available."""

    @abstractmethod
    def _put_summary(self, key: str, summary: str) -> bool:
        """Persist ``summary`` and return ``True`` if it counted as a new write."""

    def _save(self) -> None:
        """Optional persistence hook."""


class LocalFileSummaryCacheBackend(SummaryCacheBackend):
    """Persistent local summary cache backed by a JSON file."""

    backend_name = "local_file"

    def __init__(self, repo_path: Path, cache_file: str = CACHE_FILE, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.repo_path = repo_path
        self.cache_path = repo_path / cache_file
        self._data: dict[str, Any] = {"summaries": {}}
        self._load()

    def _load(self) -> None:
        if not self.enabled:
            return
        if not self.cache_path.exists():
            return
        try:
            raw_data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {"summaries": {}}
            return
        self._data = raw_data if isinstance(raw_data, dict) else {"summaries": {}}

    def _summaries(self) -> dict[str, str]:
        raw = self._data.get("summaries")
        if isinstance(raw, dict):
            return raw
        summaries: dict[str, str] = {}
        self._data["summaries"] = summaries
        return summaries

    def _get_summary(self, key: str) -> str | None:
        summaries = self._summaries()
        if key in summaries:
            return str(summaries[key])
        return None

    def _put_summary(self, key: str, summary: str) -> bool:
        summaries = self._summaries()
        is_new_key = key not in summaries
        summaries[key] = summary
        return is_new_key

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._data, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            return


class SharedSummaryCacheBackendStub(SummaryCacheBackend):
    """No-op shared-cache stub for future remote/team-level integrations."""

    backend_name = "shared_stub"

    def __init__(self, *, namespace: str = "default", enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.namespace = namespace

    def _get_summary(self, key: str) -> str | None:
        return None

    def _put_summary(self, key: str, summary: str) -> bool:
        return False


class InMemorySummaryCacheBackend(SummaryCacheBackend):
    """Process-local cache backend primarily for tests."""

    backend_name = "memory"

    def __init__(self, initial_summaries: Mapping[str, str] | None = None, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self._summaries_store = dict(initial_summaries or {})

    def _get_summary(self, key: str) -> str | None:
        return self._summaries_store.get(key)

    def _put_summary(self, key: str, summary: str) -> bool:
        is_new_key = key not in self._summaries_store
        self._summaries_store[key] = summary
        return is_new_key


def normalize_cache_backend_name(backend: str | None) -> str:
    """Normalize configured cache backend names to canonical identifiers."""

    value = str(backend or LocalFileSummaryCacheBackend.backend_name).strip().lower()
    aliases = {
        "file": LocalFileSummaryCacheBackend.backend_name,
        "local": LocalFileSummaryCacheBackend.backend_name,
        "local_file": LocalFileSummaryCacheBackend.backend_name,
        "in_memory": InMemorySummaryCacheBackend.backend_name,
        "memory": InMemorySummaryCacheBackend.backend_name,
        "remote": SharedSummaryCacheBackendStub.backend_name,
        "remote_stub": SharedSummaryCacheBackendStub.backend_name,
        "shared": SharedSummaryCacheBackendStub.backend_name,
        "shared_stub": SharedSummaryCacheBackendStub.backend_name,
    }
    normalized = aliases.get(value)
    if normalized is None:
        raise ValueError(
            "Unsupported cache backend "
            f"{backend!r}. Expected one of: local_file, shared_stub, memory."
        )
    return normalized


def create_summary_cache_backend(
    repo_path: Path,
    *,
    backend: str = LocalFileSummaryCacheBackend.backend_name,
    cache_file: str = CACHE_FILE,
    enabled: bool = True,
) -> SummaryCacheBackend:
    """Build a configured cache backend."""

    backend_name = normalize_cache_backend_name(backend)
    if backend_name == LocalFileSummaryCacheBackend.backend_name:
        return LocalFileSummaryCacheBackend(repo_path=repo_path, cache_file=cache_file, enabled=enabled)
    if backend_name == SharedSummaryCacheBackendStub.backend_name:
        return SharedSummaryCacheBackendStub(namespace=repo_path.name or "default", enabled=enabled)
    if backend_name == InMemorySummaryCacheBackend.backend_name:
        return InMemorySummaryCacheBackend(enabled=enabled)
    raise AssertionError(f"Unhandled cache backend: {backend_name}")


def normalize_cache_report(data: Mapping[str, Any]) -> dict[str, Any]:
    """Read cache metadata from a run artifact or report payload."""

    raw_cache = data.get("cache")
    if isinstance(raw_cache, Mapping):
        backend = str(raw_cache.get("backend", "unknown") or "unknown")
        enabled = bool(raw_cache.get("enabled", True))
        hits_raw = raw_cache.get("hits", data.get("cache_hits", 0))
        misses_raw = raw_cache.get("misses", 0)
        writes_raw = raw_cache.get("writes", 0)
    else:
        backend = "unknown"
        enabled = True
        hits_raw = data.get("cache_hits", 0)
        misses_raw = 0
        writes_raw = 0

    return {
        "backend": backend,
        "enabled": enabled,
        "hits": _to_int(hits_raw),
        "misses": _to_int(misses_raw),
        "writes": _to_int(writes_raw),
    }


def cache_report_as_dict(report: CacheReport) -> dict[str, Any]:
    """Convert a typed cache report into a JSON-serializable mapping."""

    return asdict(report)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
