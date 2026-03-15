from __future__ import annotations

"""HTTP server for the ContextBudget Runtime Gateway.

Uses Python's stdlib ``http.server`` so no extra dependencies are required.
"""

import http.server
import json
import logging
import threading
from typing import Any

from contextbudget.gateway.config import GatewayConfig
from contextbudget.gateway.handlers import GatewayHandlers
from contextbudget.gateway.models import (
    PrepareContextRequest,
    ReportRunRequest,
    RunAgentStepRequest,
)

logger = logging.getLogger(__name__)

_CONTENT_TYPE = "application/json"


class _GatewayRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler wired to :class:`GatewayHandlers`."""

    # Injected by GatewayServer._build_handler_class before the server starts
    _handlers: GatewayHandlers
    _config: GatewayConfig

    def do_POST(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        try:
            body = self._read_json()
        except Exception as exc:
            self._send_json({"error": f"invalid JSON body: {exc}"}, 400)
            return

        try:
            data, status = self._route(path, body)
        except KeyError as exc:
            data, status = {"error": f"missing required field: {exc}"}, 400
        except Exception as exc:
            logger.exception("unhandled error for %s", path)
            data, status = {"error": str(exc)}, 500

        self._send_json(data, status)

    def _route(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if path == "/prepare-context":
            req = PrepareContextRequest.from_dict(body)
            resp = self._handlers.handle_prepare_context(req)
            return resp.as_dict(), 200

        if path in ("/run-agent-step", "/run-step"):
            req = RunAgentStepRequest.from_dict(body)
            resp = self._handlers.handle_run_agent_step(req)
            return resp.as_dict(), 200

        if path == "/report-run":
            req = ReportRunRequest.from_dict(body)
            resp = self._handlers.handle_report_run(req)
            return resp.as_dict(), 200

        return {"error": f"unknown endpoint: {path}"}, 404

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)  # type: ignore[return-value]

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", _CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        if self._config.log_requests:
            logger.info(fmt, *args)


class GatewayServer:
    """ContextBudget Runtime Gateway.

    Sits between coding agents and LLM APIs, intercepting every task turn to
    apply the full context optimization pipeline before the prompt is forwarded
    downstream.

    Architecture::

        agent → GatewayServer (HTTP) → ContextBudget pipeline → LLM

    Quick-start::

        from contextbudget.gateway import GatewayServer, GatewayConfig

        config = GatewayConfig(host="127.0.0.1", port=8787, max_tokens=32_000)
        GatewayServer(config).start()   # blocks; Ctrl-C to stop

    Background mode::

        server = GatewayServer(config)
        server.start(block=False)   # returns immediately; runs in daemon thread
        # … do other work …
        server.stop()

    Parameters
    ----------
    config:
        Gateway configuration.  Defaults to :class:`GatewayConfig` with
        all built-in defaults when omitted.
    handlers:
        Pre-built :class:`GatewayHandlers` instance.  A fresh one is created
        from ``config`` when omitted.
    """

    def __init__(
        self,
        config: GatewayConfig | None = None,
        *,
        handlers: GatewayHandlers | None = None,
    ) -> None:
        self._config = config or GatewayConfig()
        self._handlers = handlers or GatewayHandlers(self._config)
        self._server: http.server.HTTPServer | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self, *, block: bool = True) -> None:
        """Start the HTTP server.

        Parameters
        ----------
        block:
            If ``True`` (default) the calling thread blocks until the server
            is interrupted (Ctrl-C / :meth:`stop`).  Pass ``False`` to run
            the server in a background daemon thread and return immediately.
        """
        handler_cls = self._build_handler_class()
        self._server = http.server.HTTPServer(
            (self._config.host, self._config.port), handler_cls
        )
        logger.info(
            "ContextBudget Gateway listening on http://%s:%d",
            self._config.host,
            self._config.port,
        )
        if block:
            try:
                self._server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                self._server.server_close()
        else:
            thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            thread.start()

    def stop(self) -> None:
        """Shut down the server gracefully."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_handler_class(self) -> type[_GatewayRequestHandler]:
        """Return a request handler class pre-wired with shared state."""
        handlers = self._handlers
        config = self._config

        class _Handler(_GatewayRequestHandler):
            _handlers = handlers  # type: ignore[assignment]
            _config = config  # type: ignore[assignment]

        return _Handler


def run_gateway(config: GatewayConfig | None = None) -> None:
    """Start the ContextBudget Runtime Gateway and block until interrupted.

    Uses :meth:`GatewayConfig.from_env` when ``config`` is omitted so the
    server can be configured entirely through environment variables.
    """
    GatewayServer(config or GatewayConfig.from_env()).start(block=True)
