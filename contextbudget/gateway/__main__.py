"""Entry point for ``python -m contextbudget.gateway``.

Reads configuration from ``CB_GATEWAY_*`` environment variables and starts
the gateway server on the configured host/port.

Environment variables
---------------------
CB_GATEWAY_HOST          Bind address (default: 127.0.0.1)
CB_GATEWAY_PORT          TCP port (default: 8787)
CB_GATEWAY_MAX_TOKENS    Default token budget per request (default: 128000)
CB_GATEWAY_MAX_FILES     Default top-files cap per request (default: 100)
CB_GATEWAY_MAX_CONTEXT_SIZE  Default context-size limit in bytes (default: 10485760)
CB_GATEWAY_DEFAULT_REPO  Default repo path (default: .)
CB_GATEWAY_CONFIG_PATH   Path to contextbudget.toml
CB_GATEWAY_TELEMETRY     Enable telemetry events: true/false (default: false)
CB_GATEWAY_LOG_REQUESTS  Log each HTTP request: true/false (default: true)
"""

from __future__ import annotations

import logging

from contextbudget.gateway.server import run_gateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

run_gateway()
