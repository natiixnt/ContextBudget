from __future__ import annotations

"""Redcon Runtime Gateway — FastAPI ASGI service (falls back to stdlib HTTP).

When the optional ``fastapi`` and ``uvicorn`` packages are available, this module
provides a production-grade ASGI gateway.  Without them, it falls back to the
existing stdlib implementation so no new hard dependency is introduced.

Install gateway extras:
    pip install 'redcon[gateway]'
"""

import asyncio
import logging
import os
import time
import threading
from typing import Any

from redcon.gateway.config import GatewayConfig
from redcon.gateway.handlers import GatewayHandlers
from redcon.gateway.models import (
    PrepareContextRequest,
    ReportRunRequest,
    RunAgentStepRequest,
)

logger = logging.getLogger(__name__)

_START_TIME = time.monotonic()


def _try_import_fastapi():
    try:
        import fastapi
        import uvicorn
        return fastapi, uvicorn
    except ImportError:
        return None, None


def _build_fastapi_app(config: GatewayConfig, handlers: GatewayHandlers):
    """Build and return a FastAPI application for the gateway."""
    fastapi, uvicorn = _try_import_fastapi()
    if fastapi is None:
        return None, None

    from fastapi import FastAPI, Request, HTTPException, Depends
    from fastapi.responses import JSONResponse
    import fastapi as _fa

    app = FastAPI(title="Redcon Gateway", version="1.0.0-alpha", docs_url=None, redoc_url=None)

    # Request counter
    _stats = {"requests_total": 0, "requests_active": 0}

    # ── Auth middleware ────────────────────────────────────────────────────────

    async def _verify_api_key(request: Request):
        if config.api_key is None:
            return  # auth disabled
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
        token = auth_header[len("Bearer "):]
        if token != config.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    # ── Exception handlers ─────────────────────────────────────────────────────

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error for %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": str(exc), "type": type(exc).__name__})

    # ── Health & metrics ───────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "1.0.0-alpha"}

    @app.get("/metrics")
    async def metrics():
        return {
            "gateway": {
                "requests_total": _stats["requests_total"],
                "requests_active": _stats["requests_active"],
                "uptime_seconds": round(time.monotonic() - _START_TIME, 1),
            }
        }

    # ── Helper ────────────────────────────────────────────────────────────────

    async def _run_with_timeout(coro, timeout: int):
        try:
            return await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, coro),
                timeout=float(timeout),
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"Request timed out after {timeout}s")

    # ── POST /prepare-context ─────────────────────────────────────────────────

    @app.post("/prepare-context", dependencies=[Depends(_verify_api_key)])
    async def prepare_context(body: dict, request: Request):
        _stats["requests_total"] += 1
        _stats["requests_active"] += 1
        try:
            req = PrepareContextRequest.from_dict(body)
            resp = await _run_with_timeout(
                lambda: handlers.handle_prepare_context(req),
                config.request_timeout_seconds,
            )
            return resp.as_dict()
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"Missing required field: {exc}")
        finally:
            _stats["requests_active"] -= 1

    @app.post("/run-agent-step", dependencies=[Depends(_verify_api_key)])
    @app.post("/run-step", dependencies=[Depends(_verify_api_key)])
    async def run_agent_step(body: dict, request: Request):
        _stats["requests_total"] += 1
        _stats["requests_active"] += 1
        try:
            req = RunAgentStepRequest.from_dict(body)
            resp = await _run_with_timeout(
                lambda: handlers.handle_run_agent_step(req),
                config.request_timeout_seconds,
            )
            return resp.as_dict()
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"Missing required field: {exc}")
        finally:
            _stats["requests_active"] -= 1

    @app.post("/report-run", dependencies=[Depends(_verify_api_key)])
    async def report_run(body: dict, request: Request):
        _stats["requests_total"] += 1
        _stats["requests_active"] += 1
        try:
            req = ReportRunRequest.from_dict(body)
            resp = await _run_with_timeout(
                lambda: handlers.handle_report_run(req),
                config.request_timeout_seconds,
            )
            return resp.as_dict()
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"Missing required field: {exc}")
        finally:
            _stats["requests_active"] -= 1

    return app, uvicorn


class GatewayServer:
    """Redcon Runtime Gateway.

    Uses FastAPI + Uvicorn when available (install with ``pip install 'redcon[gateway]'``),
    falls back to stdlib HTTP otherwise.
    """

    def __init__(
        self,
        config: GatewayConfig | None = None,
        *,
        handlers: GatewayHandlers | None = None,
    ) -> None:
        self._config = config or GatewayConfig()
        self._handlers = handlers or GatewayHandlers(self._config)
        self._server = None

    def start(self, *, block: bool = True) -> None:
        fastapi_mod, uvicorn_mod = _try_import_fastapi()
        if fastapi_mod is not None:
            self._start_fastapi(block=block)
        else:
            logger.warning(
                "fastapi/uvicorn not installed — using stdlib HTTP gateway. "
                "For production use: pip install 'redcon[gateway]'"
            )
            self._start_stdlib(block=block)

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass

    def _start_fastapi(self, *, block: bool) -> None:
        app, uvicorn_mod = _build_fastapi_app(self._config, self._handlers)
        if app is None:
            self._start_stdlib(block=block)
            return

        logger.info(
            "Redcon Gateway (FastAPI) listening on http://%s:%d",
            self._config.host,
            self._config.port,
        )

        uv_config = uvicorn_mod.Config(
            app,
            host=self._config.host,
            port=self._config.port,
            log_level="info" if self._config.log_requests else "warning",
            access_log=self._config.log_requests,
        )
        server = uvicorn_mod.Server(uv_config)

        if block:
            server.run()
        else:
            t = threading.Thread(target=server.run, daemon=True)
            t.start()

    def _start_stdlib(self, *, block: bool) -> None:
        """Fall-back stdlib HTTP server (single-threaded)."""
        import http.server
        import json

        handlers = self._handlers
        config = self._config

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                path = self.path.split("?")[0].rstrip("/")
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw)
                except Exception as exc:
                    self._send_json({"error": f"invalid JSON body: {exc}"}, 400)
                    return

                # Auth check
                if config.api_key:
                    auth = self.headers.get("Authorization", "")
                    if not auth.startswith("Bearer ") or auth[len("Bearer "):] != config.api_key:
                        self._send_json({"error": "Invalid API key"}, 401)
                        return

                try:
                    if path == "/prepare-context":
                        req = PrepareContextRequest.from_dict(body)
                        resp = handlers.handle_prepare_context(req)
                        self._send_json(resp.as_dict(), 200)
                    elif path in ("/run-agent-step", "/run-step"):
                        req = RunAgentStepRequest.from_dict(body)
                        resp = handlers.handle_run_agent_step(req)
                        self._send_json(resp.as_dict(), 200)
                    elif path == "/report-run":
                        req = ReportRunRequest.from_dict(body)
                        resp = handlers.handle_report_run(req)
                        self._send_json(resp.as_dict(), 200)
                    else:
                        self._send_json({"error": f"unknown endpoint: {path}"}, 404)
                except KeyError as exc:
                    self._send_json({"error": f"missing required field: {exc}"}, 400)
                except Exception as exc:
                    logger.exception("unhandled error for %s", path)
                    self._send_json({"error": str(exc)}, 500)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/health":
                    self._send_json({"status": "ok", "version": "1.0.0-alpha"}, 200)
                elif path == "/metrics":
                    self._send_json({
                        "gateway": {
                            "requests_total": 0,
                            "uptime_seconds": round(time.monotonic() - _START_TIME, 1),
                        }
                    }, 200)
                else:
                    self._send_json({"error": "not found"}, 404)

            def _send_json(self, data, status):
                body = json.dumps(data, default=str).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                if config.log_requests:
                    logger.info(fmt, *args)

        self._server = http.server.HTTPServer((config.host, config.port), _Handler)
        logger.info(
            "Redcon Gateway (stdlib) listening on http://%s:%d",
            config.host,
            config.port,
        )
        if block:
            try:
                self._server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                self._server.server_close()
        else:
            t = threading.Thread(target=self._server.serve_forever, daemon=True)
            t.start()


def run_gateway(config: GatewayConfig | None = None) -> None:
    """Start the Redcon Runtime Gateway and block until interrupted."""
    GatewayServer(config or GatewayConfig.from_env()).start(block=True)
