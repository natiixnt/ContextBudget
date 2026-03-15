from __future__ import annotations

"""Configuration for the ContextBudget Runtime Gateway."""

import os
from dataclasses import dataclass, fields


@dataclass
class GatewayConfig:
    """Runtime settings for the ContextBudget Gateway.

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
        Path to a ``contextbudget.toml`` shared by all requests.
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

    @classmethod
    def from_env(cls) -> GatewayConfig:
        """Build config from ``CB_GATEWAY_*`` environment variables."""
        return cls(
            host=os.environ.get("CB_GATEWAY_HOST", "127.0.0.1"),
            port=int(os.environ.get("CB_GATEWAY_PORT", "8787")),
            max_tokens=int(os.environ.get("CB_GATEWAY_MAX_TOKENS", "128000")),
            max_files=int(os.environ.get("CB_GATEWAY_MAX_FILES", "100")),
            max_context_size=int(
                os.environ.get(
                    "CB_GATEWAY_MAX_CONTEXT_SIZE", str(10 * 1024 * 1024)
                )
            ),
            default_repo=os.environ.get("CB_GATEWAY_DEFAULT_REPO", "."),
            config_path=os.environ.get("CB_GATEWAY_CONFIG_PATH") or None,
            telemetry_enabled=(
                os.environ.get("CB_GATEWAY_TELEMETRY", "false").lower() == "true"
            ),
            log_requests=(
                os.environ.get("CB_GATEWAY_LOG_REQUESTS", "true").lower() == "true"
            ),
        )

    @classmethod
    def from_dict(cls, d: dict) -> GatewayConfig:
        """Build config from a plain dict, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
