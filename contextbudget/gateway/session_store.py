from __future__ import annotations

"""Redis-backed session store for the ContextBudget Gateway.

When ``CB_GATEWAY_REDIS_URL`` is set, the gateway stores :class:`RuntimeSession`
state in Redis so multiple gateway replicas can serve the same multi-turn
agent session without sticky routing.

When ``CB_GATEWAY_REDIS_URL`` is **not** set the store falls back to an
in-process dict — identical to the original behaviour.

Session key format: ``cb_session:<session_id>``
TTL: 24 h by default; configurable via ``CB_GATEWAY_SESSION_TTL_SECONDS``.

Sync API
--------
Gateway handlers run in a thread-pool executor (not on the event loop), so
this module exposes a *synchronous* API (``save``, ``load``, ``delete``).
If you need async access from an ASGI context, wrap calls with
``asyncio.get_event_loop().run_in_executor``.
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_REDIS_URL: str = os.getenv("CB_GATEWAY_REDIS_URL", "")
_TTL: int = int(os.getenv("CB_GATEWAY_SESSION_TTL_SECONDS", str(24 * 3600)))
_KEY_PREFIX = "cb_session:"


class SessionStore:
    """Unified session persistence layer (Redis or in-memory).

    All public methods are **synchronous** so they can be called safely
    from the gateway handlers running inside a thread-pool executor.
    """

    def __init__(self, redis_url: str = "") -> None:
        self._memory: dict[str, str] = {}
        self._redis = None
        self._using_redis = False

        if redis_url:
            try:
                import redis as _redis  # type: ignore

                self._redis = _redis.from_url(redis_url, decode_responses=True)
                self._using_redis = True
                logger.info("Gateway session store: Redis (%s)", redis_url)
            except ImportError:
                logger.warning(
                    "redis package not installed; falling back to in-memory session store"
                )
            except Exception as exc:
                logger.warning(
                    "Redis connection failed (%s); falling back to in-memory session store", exc
                )
        else:
            logger.debug("Gateway session store: in-memory (single-node)")

    @classmethod
    def from_env(cls) -> SessionStore:
        return cls(redis_url=_REDIS_URL)

    @property
    def is_distributed(self) -> bool:
        """True when backed by Redis (safe for multi-replica deployments)."""
        return self._using_redis

    def save(self, session_id: str, session_dict: dict[str, Any]) -> None:
        """Persist *session_dict* for *session_id*."""
        key = _KEY_PREFIX + session_id
        value = json.dumps(session_dict)
        try:
            if self._redis is not None:
                self._redis.set(key, value, ex=_TTL)
            else:
                self._memory[key] = value
        except Exception as exc:
            logger.warning("Session save failed for %s: %s", session_id, exc)

    def load(self, session_id: str) -> dict[str, Any] | None:
        """Return session state for *session_id*, or ``None`` if not found."""
        key = _KEY_PREFIX + session_id
        try:
            if self._redis is not None:
                raw = self._redis.get(key)
            else:
                raw = self._memory.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Session load failed for %s: %s", session_id, exc)
            return None

    def delete(self, session_id: str) -> None:
        """Remove session state for *session_id*."""
        key = _KEY_PREFIX + session_id
        try:
            if self._redis is not None:
                self._redis.delete(key)
            else:
                self._memory.pop(key, None)
        except Exception as exc:
            logger.warning("Session delete failed for %s: %s", session_id, exc)

    def ping(self) -> bool:
        """Return True if the backing store is reachable."""
        if self._redis is not None:
            try:
                self._redis.ping()
                return True
            except Exception:
                return False
        return True
