from __future__ import annotations

"""Minimal HTTP API server for control plane multi-team analytics."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from contextbudget.control_plane.store import ControlPlaneStore

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7700


def _json_response(handler: BaseHTTPRequestHandler, data: object, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error(handler: BaseHTTPRequestHandler, message: str, status: int = 400) -> None:
    _json_response(handler, {"error": message}, status)


def _int_param(params: dict[str, list[str]], key: str) -> int | None:
    vals = params.get(key)
    if not vals:
        return None
    try:
        return int(vals[0])
    except ValueError:
        return None


class _Handler(BaseHTTPRequestHandler):
    store: ControlPlaneStore  # injected by ControlPlaneServer

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102
        pass  # suppress default stderr logging; server prints its own startup line

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path = parsed.path.rstrip("/")

        if path == "/orgs":
            orgs = self.store.list_orgs()
            _json_response(self, {"orgs": [o.as_dict() for o in orgs]})

        elif path == "/projects":
            org_id = _int_param(params, "org_id")
            projects = self.store.list_projects(org_id=org_id)
            _json_response(self, {"projects": [p.as_dict() for p in projects]})

        elif path == "/repos":
            project_id = _int_param(params, "project_id")
            repos = self.store.list_repos(project_id=project_id)
            _json_response(self, {"repos": [r.as_dict() for r in repos]})

        elif path == "/runs":
            repo_id = _int_param(params, "repo_id")
            runs = self.store.list_runs(repo_id=repo_id)
            _json_response(self, {"runs": [r.as_dict() for r in runs]})

        else:
            _error(self, f"unknown route: {path}", 404)


class ControlPlaneServer:
    """HTTP server exposing the control plane analytics API."""

    def __init__(
        self,
        store: ControlPlaneStore,
        *,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
    ) -> None:
        self._store = store
        self._host = host
        self._port = port

    def serve(self) -> None:
        handler_cls = type("Handler", (_Handler,), {"store": self._store})
        server = HTTPServer((self._host, self._port), handler_cls)
        print(f"Control plane listening on http://{self._host}:{self._port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


def make_server(
    db_path: str | Path = ".contextbudget/control_plane.db",
    *,
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> ControlPlaneServer:
    """Convenience factory used by the CLI."""
    store = ControlPlaneStore(db_path=db_path)
    return ControlPlaneServer(store, host=host, port=port)
