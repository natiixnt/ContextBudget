from __future__ import annotations

"""Redcon Runtime Gateway.

Exposes the context optimization pipeline as an HTTP middleware service that
sits between coding agents and LLM APIs::

    agent → Redcon Gateway → LLM

Quick-start::

    from redcon.gateway import GatewayServer, GatewayConfig

    config = GatewayConfig(host="127.0.0.1", port=8787, max_tokens=32_000)
    GatewayServer(config).start()

Or from the command line::

    python -m redcon.gateway
    RC_GATEWAY_PORT=9000 python -m redcon.gateway
"""

from redcon.gateway.config import GatewayConfig
from redcon.gateway.handlers import GatewayHandlers
from redcon.gateway.server import GatewayServer, run_gateway

__all__ = [
    "GatewayConfig",
    "GatewayHandlers",
    "GatewayServer",
    "run_gateway",
]
