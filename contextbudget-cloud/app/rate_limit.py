from __future__ import annotations

"""Per-API-key sliding-window rate limiter for event ingestion.

The limiter is in-process (no Redis required).  For a multi-replica
deployment replace ``_windows`` with a Redis sorted-set implementation.

Default limit: 500 events per 60-second window per API key.  Override
via the ``CB_CLOUD_EVENTS_RATE_LIMIT`` and ``CB_CLOUD_EVENTS_RATE_WINDOW``
environment variables.
"""

import os
import time
from collections import deque
from threading import Lock

_LIMIT: int = int(os.getenv("CB_CLOUD_EVENTS_RATE_LIMIT", "500"))
_WINDOW: int = int(os.getenv("CB_CLOUD_EVENTS_RATE_WINDOW", "60"))  # seconds

# api_key_hash → deque of timestamps (float)
_windows: dict[str, deque[float]] = {}
_lock = Lock()


def check_rate_limit(api_key_hash: str, n_events: int = 1) -> bool:
    """Return True if the key is within its rate limit (and record the calls).

    Parameters
    ----------
    api_key_hash:
        The *hashed* API key (never the raw key).
    n_events:
        Number of events being ingested in this request.

    Returns
    -------
    bool
        ``True``  → within limit, request allowed.
        ``False`` → limit exceeded, request should be rejected.
    """
    now = time.monotonic()
    cutoff = now - _WINDOW

    with _lock:
        if api_key_hash not in _windows:
            _windows[api_key_hash] = deque()
        window = _windows[api_key_hash]

        # Drop timestamps outside the sliding window
        while window and window[0] < cutoff:
            window.popleft()

        current_count = len(window)
        if current_count + n_events > _LIMIT:
            return False

        # Record each event as a separate timestamp entry
        for _ in range(n_events):
            window.append(now)
        return True


def remaining(api_key_hash: str) -> int:
    """Return how many more events the key can ingest in the current window."""
    now = time.monotonic()
    cutoff = now - _WINDOW
    with _lock:
        window = _windows.get(api_key_hash, deque())
        count = sum(1 for t in window if t >= cutoff)
        return max(0, _LIMIT - count)
