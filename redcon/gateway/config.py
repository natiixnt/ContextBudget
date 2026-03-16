# SPDX-License-Identifier: LicenseRef-Redcon-Commercial
# Copyright (c) 2026 nai. All rights reserved.
# See LICENSE-COMMERCIAL for terms.

from __future__ import annotations

"""Configuration for the Redcon Runtime Gateway."""

import os
from dataclasses import dataclass, fields


@dataclass
class GatewayConfig:
    """Runtime settings for the Redcon Gateway.

    All fields can be overridden at startup via environment variables
    (see :meth:`from_env`) or by passing a dict to :meth:`from_dict`.

    Attributes
    ----------
    host:
        Bind address for the HTTP server.
    port:
        TCP port the server listens on.
    max_tokens:
        Default token budget applied when a request omits ``max_tokens``.
    max_files:
        Default ``top_files`` cap applied when a request omits ``max_files``.
    max_context_size:
        Default context-size policy in bytes applied when a request omits
        ``max_context_size``.
    default_repo:
        Repository path used when a request omits ``repo``.
    config_path:
        Path to a ``redcon.toml`` shared by all requests.
    telemetry_enabled:
        Emit gateway telemetry events (default off).
    log_requests:
        Log each HTTP request to the Python logger (default on).
    """

    host: str = "127.0.0.1"
    port: int = 8787
    max_tokens: int = 128_000
    max_files: int = 100
    max_context_size: int = 10 * 1024 * 1024  # 10 MB
    default_repo: str = "."
    config_path: str | None = None
    telemetry_enabled: bool = False
    log_requests: bool = True
    api_key: str | None = None          # Required Bearer token; None = auth disabled
    request_timeout_seconds: int = 30   # Per-request processing timeout

    # Remote policy — optional cloud control plane integration
    # If set, the gateway fetches the active PolicySpec from the cloud service
    # before each request and enforces it server-side.
    cloud_policy_url: str | None = None   # e.g. https://cloud.example.com
    cloud_api_key: str | None = None      # Bearer key for the cloud service
    cloud_policy_org_id: int | None = None  # org_id to scope the policy lookup

    # Webhook push notifications (optional)
    # When set, the gateway fires a POST to webhook_url on policy violations and
    # budget overruns.  webhook_secret is used for HMAC-SHA256 signing.
    webhook_url: str | None = None
    webhook_secret: str | None = None

    # Redis session store (optional — enables horizontal scaling of /run-agent-step)
    # When set, session state is persisted to Redis so any replica can continue
    # a multi-turn session started on another node.
    redis_url: str | None = None  # e.g. redis://redis:6379/0

    @classmethod
    def from_env(cls) -> GatewayConfig:
        """Build config from ``RC_GATEWAY_*`` environment variables."""
        cloud_org_raw = os.environ.get("RC_GATEWAY_CLOUD_ORG_ID")
        return cls(
            host=os.environ.get("RC_GATEWAY_HOST", "127.0.0.1"),
            port=int(os.environ.get("RC_GATEWAY_PORT", "8787")),
            max_tokens=int(os.environ.get("RC_GATEWAY_MAX_TOKENS", "128000")),
            max_files=int(os.environ.get("RC_GATEWAY_MAX_FILES", "100")),
            max_context_size=int(
                os.environ.get(
                    "RC_GATEWAY_MAX_CONTEXT_SIZE", str(10 * 1024 * 1024)
                )
            ),
            default_repo=os.environ.get("RC_GATEWAY_DEFAULT_REPO", "."),
            config_path=os.environ.get("RC_GATEWAY_CONFIG_PATH") or None,
            telemetry_enabled=(
                os.environ.get("RC_GATEWAY_TELEMETRY", "false").lower() == "true"
            ),
            log_requests=(
                os.environ.get("RC_GATEWAY_LOG_REQUESTS", "true").lower() == "true"
            ),
            api_key=os.environ.get("RC_GATEWAY_API_KEY") or None,
            request_timeout_seconds=int(os.environ.get("RC_GATEWAY_TIMEOUT_SECONDS", "30")),
            cloud_policy_url=os.environ.get("RC_GATEWAY_CLOUD_POLICY_URL") or None,
            cloud_api_key=os.environ.get("RC_GATEWAY_CLOUD_API_KEY") or None,
            cloud_policy_org_id=int(cloud_org_raw) if cloud_org_raw else None,
            webhook_url=os.environ.get("RC_GATEWAY_WEBHOOK_URL") or None,
            webhook_secret=os.environ.get("RC_GATEWAY_WEBHOOK_SECRET") or None,
            redis_url=os.environ.get("RC_GATEWAY_REDIS_URL") or None,
        )

    @classmethod
    def from_dict(cls, d: dict) -> GatewayConfig:
        """Build config from a plain dict, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
