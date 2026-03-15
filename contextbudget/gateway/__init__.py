from __future__ import annotations

"""ContextBudget Runtime Gateway.

Exposes the context optimization pipeline as an HTTP middleware service that
sits between coding agents and LLM APIs::

    agent → ContextBudget Gateway → LLM

Quick-start::

    from contextbudget.gateway import GatewayServer, GatewayConfig

    config = GatewayConfig(host="127.0.0.1", port=8787, max_tokens=32_000)
    GatewayServer(config).start()

Or from the command line::

    python -m contextbudget.gateway
    CB_GATEWAY_PORT=9000 python -m contextbudget.gateway
"""

from contextbudget.gateway.config import GatewayConfig
from contextbudget.gateway.handlers import GatewayHandlers
from contextbudget.gateway.server import GatewayServer, run_gateway

__all__ = [
    "GatewayConfig",
    "GatewayHandlers",
    "GatewayServer",
    "run_gateway",
]
