"""Webhook dispatcher for Redcon gateway push notifications.

Supported event types
---------------------
- ``policy_violation``  — a pack or agent-step request violated a policy rule
- ``budget_overrun``    — estimated tokens exceeded the configured max_tokens budget
- ``drift_alert``       — repository context drift exceeded the threshold
- ``cache_miss_spike``  — cache hit rate dropped below expected levels

Usage
-----
Call :func:`dispatch_webhook` from the gateway after each handled request.
The function is fire-and-forget: all network failures are logged at WARNING
level and swallowed so the gateway is never blocked by webhook delivery.

If ``secret`` is provided the payload is signed with HMAC-SHA256 and the
signature is sent in the ``X-CB-Signature-256`` header as ``sha256=<hex>``.
This lets receiving servers verify authenticity.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Webhook delivery timeout — keep short to avoid blocking gateway requests
_TIMEOUT_SECONDS = 3


def _sign_payload(payload: bytes, secret: str) -> str:
    """Return ``sha256=<hex>`` HMAC signature."""
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def dispatch_webhook(
    url: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    secret: str | None = None,
) -> None:
    """Deliver one webhook event to *url*.

    This function is synchronous and blocks for up to :data:`_TIMEOUT_SECONDS`.
    Call from a thread or as fire-and-forget — never ``await`` it from async code
    (use ``asyncio.get_event_loop().run_in_executor`` or a background thread).
    All errors are caught and logged; the caller is never raised to.
    """
    body: dict[str, Any] = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Redcon-Webhook/1.0")
    if secret:
        req.add_header("X-CB-Signature-256", _sign_payload(data, secret))

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
            status = resp.status
            if status >= 400:
                logger.warning("Webhook %s returned HTTP %d", url, status)
    except urllib.error.HTTPError as exc:
        logger.warning("Webhook delivery failed: HTTP %d from %s", exc.code, url)
    except Exception as exc:
        logger.warning("Webhook delivery error (%s): %s", url, exc)


def dispatch_policy_violation(
    url: str,
    *,
    secret: str | None = None,
    run_id: str | None = None,
    endpoint: str | None = None,
    violations: list[str] | None = None,
    tokens_used: int | None = None,
    repository_id: str | None = None,
) -> None:
    dispatch_webhook(
        url,
        "policy_violation",
        {
            "run_id":        run_id,
            "endpoint":      endpoint,
            "violations":    violations or [],
            "tokens_used":   tokens_used,
            "repository_id": repository_id,
        },
        secret=secret,
    )


def dispatch_budget_overrun(
    url: str,
    *,
    secret: str | None = None,
    run_id: str | None = None,
    endpoint: str | None = None,
    tokens_used: int | None = None,
    max_tokens: int | None = None,
    repository_id: str | None = None,
) -> None:
    dispatch_webhook(
        url,
        "budget_overrun",
        {
            "run_id":        run_id,
            "endpoint":      endpoint,
            "tokens_used":   tokens_used,
            "max_tokens":    max_tokens,
            "repository_id": repository_id,
        },
        secret=secret,
    )


def dispatch_drift_alert(
    url: str,
    *,
    secret: str | None = None,
    repository_id: str | None = None,
    token_drift_pct: float | None = None,
    verdict: str | None = None,
) -> None:
    dispatch_webhook(
        url,
        "drift_alert",
        {
            "repository_id":  repository_id,
            "token_drift_pct": token_drift_pct,
            "verdict":         verdict,
        },
        secret=secret,
    )
