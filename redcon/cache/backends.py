from __future__ import annotations

"""Cache backend abstractions and built-in implementations."""

import json
import zlib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from redcon.schemas.models import CACHE_FILE, CacheReport


@dataclass(slots=True)
class CacheStats:
    """Runtime counters collected by a cache backend."""

    hits: int = 0
    misses: int = 0
    writes: int = 0
    tokens_saved: int = 0
    fragment_hits: int = 0
    fragment_misses: int = 0
    fragment_writes: int = 0
    slice_hits: int = 0
    slice_misses: int = 0
    slice_writes: int = 0


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

    def put_summary(self, key: str, summary: str) -> bool:
        """Store a summary if the backend accepts writes."""

        if not self.enabled:
            return False
        if self._put_summary(key, summary):
            self.stats.writes += 1
            return True
        return False

    def get_fragment(self, key: str) -> str | None:
        """Lookup a cached fragment reference and update counters."""

        if not self.enabled:
            return None
        fragment = self._get_fragment(key)
        if fragment is None:
            self.stats.misses += 1
            self.stats.fragment_misses += 1
            return None
        self.stats.hits += 1
        self.stats.fragment_hits += 1
        return fragment

    def put_fragment(self, key: str, reference: str) -> bool:
        """Store a fragment reference if the backend accepts writes."""

        if not self.enabled:
            return False
        if self._put_fragment(key, reference):
            self.stats.writes += 1
            self.stats.fragment_writes += 1
            return True
        return False

    def get_slice(self, key: str) -> str | None:
        """Lookup a cached context slice and update hit/miss counters."""

        if not self.enabled:
            return None
        value = self._get_slice(key)
        if value is None:
            self.stats.misses += 1
            self.stats.slice_misses += 1
            return None
        self.stats.hits += 1
        self.stats.slice_hits += 1
        return value

    def put_slice(self, key: str, data: str) -> bool:
        """Store a context slice if the backend accepts writes."""

        if not self.enabled:
            return False
        if self._put_slice(key, data):
            self.stats.writes += 1
            self.stats.slice_writes += 1
            return True
        return False

    def invalidate(self, key: str) -> bool:
        """Remove all stored values associated with *key*.

        Removes the key from summaries, fragments, and slices stores.
        Returns ``True`` if at least one entry was actually removed.
        """

        if not self.enabled:
            return False
        return self._invalidate(key)

    def record_tokens_saved(self, tokens_saved: int) -> None:
        """Record prompt tokens saved by fragment reuse."""

        if not self.enabled:
            return
        self.stats.tokens_saved += max(0, int(tokens_saved))

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
            tokens_saved=self.stats.tokens_saved,
            fragment_hits=self.stats.fragment_hits,
            fragment_misses=self.stats.fragment_misses,
            fragment_writes=self.stats.fragment_writes,
            slice_hits=self.stats.slice_hits,
            slice_misses=self.stats.slice_misses,
            slice_writes=self.stats.slice_writes,
        )

    @abstractmethod
    def _get_summary(self, key: str) -> str | None:
        """Return a cached summary for ``key`` if available."""

    @abstractmethod
    def _put_summary(self, key: str, summary: str) -> bool:
        """Persist ``summary`` and return ``True`` if it counted as a new write."""

    @abstractmethod
    def _get_fragment(self, key: str) -> str | None:
        """Return a cached fragment reference for ``key`` if available."""

    @abstractmethod
    def _put_fragment(self, key: str, reference: str) -> bool:
        """Persist ``reference`` and return ``True`` if it counted as a new write."""

    @abstractmethod
    def _get_slice(self, key: str) -> str | None:
        """Return a cached context slice for ``key`` if available."""

    @abstractmethod
    def _put_slice(self, key: str, data: str) -> bool:
        """Persist ``data`` and return ``True`` if it counted as a new write."""

    @abstractmethod
    def _invalidate(self, key: str) -> bool:
        """Remove all stored values for ``key`` across summaries, fragments, and slices.

        Return ``True`` if at least one entry was removed.
        """

    def _save(self) -> None:
        """Optional persistence hook."""


class LocalFileSummaryCacheBackend(SummaryCacheBackend):
    """Persistent local summary cache backed by a JSON file."""

    backend_name = "local_file"

    def __init__(self, repo_path: Path, cache_file: str = CACHE_FILE, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.repo_path = repo_path
        self.cache_path = repo_path / cache_file
        self._data: dict[str, Any] = {"summaries": {}, "fragments": {}, "slices": {}}
        self._load()

    def _load(self) -> None:
        if not self.enabled:
            return
        if not self.cache_path.exists():
            return
        try:
            raw_data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {"summaries": {}, "fragments": {}, "slices": {}}
            return
        self._data = raw_data if isinstance(raw_data, dict) else {"summaries": {}, "fragments": {}, "slices": {}}

    def _store(self, name: str) -> dict[str, str]:
        raw = self._data.get(name)
        if isinstance(raw, dict):
            return raw
        summaries: dict[str, str] = {}
        self._data[name] = summaries
        return summaries

    def _get_summary(self, key: str) -> str | None:
        summaries = self._store("summaries")
        if key in summaries:
            return str(summaries[key])
        return None

    def _put_summary(self, key: str, summary: str) -> bool:
        summaries = self._store("summaries")
        is_new_key = key not in summaries
        summaries[key] = summary
        return is_new_key

    def _get_fragment(self, key: str) -> str | None:
        fragments = self._store("fragments")
        if key in fragments:
            return str(fragments[key])
        return None

    def _put_fragment(self, key: str, reference: str) -> bool:
        fragments = self._store("fragments")
        is_new_key = key not in fragments
        fragments[key] = reference
        return is_new_key

    def _get_slice(self, key: str) -> str | None:
        slices = self._store("slices")
        if key in slices:
            return str(slices[key])
        return None

    def _put_slice(self, key: str, data: str) -> bool:
        slices = self._store("slices")
        is_new_key = key not in slices
        slices[key] = data
        return is_new_key

    def _invalidate(self, key: str) -> bool:
        removed = False
        for store_name in ("summaries", "fragments", "slices"):
            store = self._store(store_name)
            if key in store:
                del store[key]
                removed = True
        return removed

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._data, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            return


class SQLiteSummaryCacheBackend(SummaryCacheBackend):
    """Persistent local summary cache backed by SQLite.

    Faster than :class:`LocalFileSummaryCacheBackend` for large caches: reads
    are targeted ``SELECT`` queries instead of parsing a full JSON file on
    startup.  Uses WAL mode so concurrent agent processes can read/write
    without blocking each other.

    On first use it automatically imports any existing JSON cache file so
    existing cached summaries are not lost when switching backends.

    Configuration example::

        [cache]
        backend = "sqlite"
    """

    backend_name = "sqlite"
    _DB_FILENAME = ".redcon_cache.db"

    def __init__(self, repo_path: Path, cache_file: str = CACHE_FILE, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.repo_path = repo_path
        self.db_path = repo_path / self._DB_FILENAME
        self._conn: Any = None
        if self.enabled:
            try:
                self._ensure_schema()
                self._migrate_from_json(repo_path / cache_file)
            except Exception:  # noqa: BLE001 - degrade silently if SQLite unavailable
                self.enabled = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        if self._conn is None:
            import sqlite3  # stdlib - always available

            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._conn = conn
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS summaries (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS fragments (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS slices    (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        )
        conn.commit()

    def _migrate_from_json(self, json_path: Path) -> None:
        """One-time import of an existing JSON cache file into SQLite."""
        if not json_path.exists():
            return
        conn = self._connect()
        already_populated = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()
        if already_populated and already_populated[0] > 0:
            return
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        with conn:
            for key, value in raw.get("summaries", {}).items():
                conn.execute("INSERT OR IGNORE INTO summaries VALUES (?, ?)", (key, str(value)))
            for key, value in raw.get("fragments", {}).items():
                conn.execute("INSERT OR IGNORE INTO fragments VALUES (?, ?)", (key, str(value)))
            for key, value in raw.get("slices", {}).items():
                conn.execute("INSERT OR IGNORE INTO slices VALUES (?, ?)", (key, str(value)))

    def _db_get(self, table: str, key: str) -> str | None:
        try:
            row = self._connect().execute(f"SELECT value FROM {table} WHERE key = ?", (key,)).fetchone()  # noqa: S608
        except Exception:  # noqa: BLE001
            return None
        return row[0] if row else None

    def _db_put(self, table: str, key: str, value: str) -> bool:
        conn = self._connect()
        try:
            existing = conn.execute(f"SELECT 1 FROM {table} WHERE key = ?", (key,)).fetchone()  # noqa: S608
            conn.execute(f"INSERT OR REPLACE INTO {table} VALUES (?, ?)", (key, value))  # noqa: S608
            conn.commit()
        except Exception:  # noqa: BLE001
            return False
        return existing is None

    # ------------------------------------------------------------------
    # SummaryCacheBackend protocol
    # ------------------------------------------------------------------

    def _get_summary(self, key: str) -> str | None:
        return self._db_get("summaries", key)

    def _put_summary(self, key: str, summary: str) -> bool:
        return self._db_put("summaries", key, summary)

    def _get_fragment(self, key: str) -> str | None:
        return self._db_get("fragments", key)

    def _put_fragment(self, key: str, reference: str) -> bool:
        return self._db_put("fragments", key, reference)

    def _get_slice(self, key: str) -> str | None:
        return self._db_get("slices", key)

    def _put_slice(self, key: str, data: str) -> bool:
        return self._db_put("slices", key, data)

    def _invalidate(self, key: str) -> bool:
        conn = self._connect()
        deleted = 0
        try:
            for table in ("summaries", "fragments", "slices"):
                cursor = conn.execute(f"DELETE FROM {table} WHERE key = ?", (key,))  # noqa: S608
                deleted += cursor.rowcount
            conn.commit()
        except Exception:  # noqa: BLE001
            return False
        return deleted > 0

    def __del__(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


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

    def _get_fragment(self, key: str) -> str | None:
        return None

    def _put_fragment(self, key: str, reference: str) -> bool:
        return False

    def _get_slice(self, key: str) -> str | None:
        return None

    def _put_slice(self, key: str, data: str) -> bool:
        return False

    def _invalidate(self, key: str) -> bool:
        return False


def build_redis_cache_key(
    *,
    org: str,
    repo: str,
    file_path: str,
    symbol_or_slice: str,
    content_hash: str,
) -> str:
    """Build a structured cache key for cross-agent context reuse.

    The returned key encodes the full identity of a cached fragment so that
    multiple agents working on the same codebase can share cached summaries
    and context fragments without key collisions.

    Example::

        key = build_redis_cache_key(
            org="acme",
            repo="backend",
            file_path="src/auth.py",
            symbol_or_slice="def login",
            content_hash=sha256(text.encode()).hexdigest(),
        )
        cache.get_fragment(key)
    """

    def _slug(value: str) -> str:
        return value.replace(":", "_").replace("/", "|").strip() or "_"

    parts = [
        _slug(org),
        _slug(repo),
        _slug(file_path),
        _slug(symbol_or_slice),
        sha256(content_hash.encode()).hexdigest()[:16] if content_hash else "_",
    ]
    return ":".join(parts)


class RedisSummaryCacheBackend(SummaryCacheBackend):
    """Production-ready shared cache backend backed by Redis.

    Stores compressed context fragments and symbol/slice summaries in Redis
    with configurable TTL and namespace isolation.  Values are zlib-compressed
    before storage to reduce network and memory overhead.

    The backend is fully compatible with :class:`LocalFileSummaryCacheBackend`
    and can be swapped in via ``cache_backend = redis`` in ``redcon.toml``.

    Configuration example::

        [cache]
        backend = "redis"
        redis_url = "redis://localhost:6379/0"
        redis_namespace = "myorg:myrepo"
        redis_ttl_seconds = 86400

    Cross-agent reuse is enabled automatically: any agent that connects to the
    same Redis instance with the same namespace will reuse cached summaries and
    context fragments produced by prior runs.
    """

    backend_name = "redis"

    def __init__(
        self,
        *,
        redis_url: str = "redis://localhost:6379/0",
        namespace: str = "redcon",
        ttl_seconds: int = 86400,
        enabled: bool = True,
    ) -> None:
        super().__init__(enabled=enabled)
        self.redis_url = redis_url
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self._redis: Any = None  # Lazy-initialised on first use

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _client(self) -> Any:
        if self._redis is None:
            try:
                import redis as _redis  # type: ignore[import-not-found]
            except ModuleNotFoundError as exc:  # pragma: no cover
                raise RuntimeError(
                    "The 'redis' package is required for the Redis cache backend. "
                    "Install it with: pip install 'redcon[redis]'"
                ) from exc
            self._redis = _redis.Redis.from_url(self.redis_url, decode_responses=False)
        return self._redis

    def _namespace_key(self, prefix: str, key: str) -> str:
        return f"{self.namespace}:{prefix}:{key}"

    def _compress(self, value: str) -> bytes:
        return zlib.compress(value.encode("utf-8"), level=6)

    def _decompress(self, data: bytes) -> str:
        return zlib.decompress(data).decode("utf-8")

    def _redis_get(self, redis_key: str) -> str | None:
        try:
            raw = self._client().get(redis_key)
        except Exception:  # noqa: BLE001 - connection failures degrade gracefully
            return None
        if raw is None:
            return None
        try:
            return self._decompress(raw)
        except zlib.error:
            return None

    def _redis_set(self, redis_key: str, value: str) -> bool:
        """Store *value* at *redis_key* with TTL.  Returns True if key was new."""
        compressed = self._compress(value)
        try:
            client = self._client()
            is_new = not client.exists(redis_key)
            if self.ttl_seconds > 0:
                client.setex(redis_key, self.ttl_seconds, compressed)
            else:
                client.set(redis_key, compressed)
        except Exception:  # noqa: BLE001
            return False
        return is_new

    # ------------------------------------------------------------------
    # SummaryCacheBackend protocol
    # ------------------------------------------------------------------

    def _get_summary(self, key: str) -> str | None:
        return self._redis_get(self._namespace_key("s", key))

    def _put_summary(self, key: str, summary: str) -> bool:
        return self._redis_set(self._namespace_key("s", key), summary)

    def _get_fragment(self, key: str) -> str | None:
        return self._redis_get(self._namespace_key("f", key))

    def _put_fragment(self, key: str, reference: str) -> bool:
        return self._redis_set(self._namespace_key("f", key), reference)

    def _get_slice(self, key: str) -> str | None:
        return self._redis_get(self._namespace_key("c", key))

    def _put_slice(self, key: str, data: str) -> bool:
        return self._redis_set(self._namespace_key("c", key), data)

    def _invalidate(self, key: str) -> bool:
        """Delete all stores (summary, fragment, slice) for *key* from Redis."""
        keys = [
            self._namespace_key("s", key),
            self._namespace_key("f", key),
            self._namespace_key("c", key),
        ]
        try:
            deleted: int = self._client().delete(*keys)
        except Exception:  # noqa: BLE001
            return False
        return deleted > 0

    def invalidate_namespace(self) -> int:
        """Delete all keys in this backend's namespace from Redis.

        Uses ``SCAN`` to avoid blocking the server.  Returns the total number
        of keys deleted.

        Example::

            backend = RedisSummaryCacheBackend(namespace="myorg:myrepo")
            backend.invalidate_namespace()  # remove stale cache after a force-push
        """
        pattern = f"{self.namespace}:*"
        deleted = 0
        try:
            client = self._client()
            cursor = 0
            while True:
                cursor, batch = client.scan(cursor, match=pattern, count=200)
                if batch:
                    deleted += client.delete(*batch)
                if cursor == 0:
                    break
        except Exception:  # noqa: BLE001
            pass
        return deleted


class InMemorySummaryCacheBackend(SummaryCacheBackend):
    """Process-local cache backend primarily for tests."""

    backend_name = "memory"

    def __init__(self, initial_summaries: Mapping[str, str] | None = None, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self._summaries_store = dict(initial_summaries or {})
        self._fragments_store: dict[str, str] = {}
        self._slices_store: dict[str, str] = {}

    def _get_summary(self, key: str) -> str | None:
        return self._summaries_store.get(key)

    def _put_summary(self, key: str, summary: str) -> bool:
        is_new_key = key not in self._summaries_store
        self._summaries_store[key] = summary
        return is_new_key

    def _get_fragment(self, key: str) -> str | None:
        return self._fragments_store.get(key)

    def _put_fragment(self, key: str, reference: str) -> bool:
        is_new_key = key not in self._fragments_store
        self._fragments_store[key] = reference
        return is_new_key

    def _get_slice(self, key: str) -> str | None:
        return self._slices_store.get(key)

    def _put_slice(self, key: str, data: str) -> bool:
        is_new_key = key not in self._slices_store
        self._slices_store[key] = data
        return is_new_key

    def _invalidate(self, key: str) -> bool:
        removed = False
        for store in (self._summaries_store, self._fragments_store, self._slices_store):
            if key in store:
                del store[key]
                removed = True
        return removed


def normalize_cache_backend_name(backend: str | None) -> str:
    """Normalize configured cache backend names to canonical identifiers."""

    value = str(backend or LocalFileSummaryCacheBackend.backend_name).strip().lower()
    aliases = {
        "file": LocalFileSummaryCacheBackend.backend_name,
        "local": LocalFileSummaryCacheBackend.backend_name,
        "local_file": LocalFileSummaryCacheBackend.backend_name,
        "sqlite": SQLiteSummaryCacheBackend.backend_name,
        "in_memory": InMemorySummaryCacheBackend.backend_name,
        "memory": InMemorySummaryCacheBackend.backend_name,
        "remote": SharedSummaryCacheBackendStub.backend_name,
        "remote_stub": SharedSummaryCacheBackendStub.backend_name,
        "shared": SharedSummaryCacheBackendStub.backend_name,
        "shared_stub": SharedSummaryCacheBackendStub.backend_name,
        "redis": RedisSummaryCacheBackend.backend_name,
    }
    normalized = aliases.get(value)
    if normalized is None:
        raise ValueError(
            "Unsupported cache backend "
            f"{backend!r}. Expected one of: local_file, sqlite, redis, shared_stub, memory."
        )
    return normalized


def create_summary_cache_backend(
    repo_path: Path,
    *,
    backend: str = LocalFileSummaryCacheBackend.backend_name,
    cache_file: str = CACHE_FILE,
    enabled: bool = True,
    redis_url: str = "redis://localhost:6379/0",
    redis_namespace: str = "redcon",
    redis_ttl_seconds: int = 86400,
) -> SummaryCacheBackend:
    """Build a configured cache backend."""

    backend_name = normalize_cache_backend_name(backend)
    if backend_name == LocalFileSummaryCacheBackend.backend_name:
        return LocalFileSummaryCacheBackend(repo_path=repo_path, cache_file=cache_file, enabled=enabled)
    if backend_name == SQLiteSummaryCacheBackend.backend_name:
        return SQLiteSummaryCacheBackend(repo_path=repo_path, cache_file=cache_file, enabled=enabled)
    if backend_name == SharedSummaryCacheBackendStub.backend_name:
        return SharedSummaryCacheBackendStub(namespace=repo_path.name or "default", enabled=enabled)
    if backend_name == InMemorySummaryCacheBackend.backend_name:
        return InMemorySummaryCacheBackend(enabled=enabled)
    if backend_name == RedisSummaryCacheBackend.backend_name:
        try:
            backend_instance = RedisSummaryCacheBackend(
                redis_url=redis_url,
                namespace=redis_namespace,
                ttl_seconds=redis_ttl_seconds,
                enabled=enabled,
            )
            # Probe connectivity - fall back to local_file on failure
            backend_instance._client().ping()
            return backend_instance
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "Redis unavailable at %s - falling back to local_file cache backend",
                redis_url,
            )
            return LocalFileSummaryCacheBackend(repo_path=repo_path, cache_file=cache_file, enabled=enabled)
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
        tokens_saved_raw = raw_cache.get("tokens_saved", 0)
        fragment_hits_raw = raw_cache.get("fragment_hits", 0)
        fragment_misses_raw = raw_cache.get("fragment_misses", 0)
        fragment_writes_raw = raw_cache.get("fragment_writes", 0)
        slice_hits_raw = raw_cache.get("slice_hits", 0)
        slice_misses_raw = raw_cache.get("slice_misses", 0)
        slice_writes_raw = raw_cache.get("slice_writes", 0)
    else:
        backend = "unknown"
        enabled = True
        hits_raw = data.get("cache_hits", 0)
        misses_raw = 0
        writes_raw = 0
        tokens_saved_raw = 0
        fragment_hits_raw = 0
        fragment_misses_raw = 0
        fragment_writes_raw = 0
        slice_hits_raw = 0
        slice_misses_raw = 0
        slice_writes_raw = 0

    return {
        "backend": backend,
        "enabled": enabled,
        "hits": _to_int(hits_raw),
        "misses": _to_int(misses_raw),
        "writes": _to_int(writes_raw),
        "tokens_saved": _to_int(tokens_saved_raw),
        "fragment_hits": _to_int(fragment_hits_raw),
        "fragment_misses": _to_int(fragment_misses_raw),
        "fragment_writes": _to_int(fragment_writes_raw),
        "slice_hits": _to_int(slice_hits_raw),
        "slice_misses": _to_int(slice_misses_raw),
        "slice_writes": _to_int(slice_writes_raw),
    }


def cache_report_as_dict(report: CacheReport) -> dict[str, Any]:
    """Convert a typed cache report into a JSON-serializable mapping."""

    return asdict(report)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
