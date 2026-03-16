# SPDX-License-Identifier: LicenseRef-Redcon-Commercial
# Copyright (c) 2026 nai. All rights reserved.
# See LICENSE-COMMERCIAL for terms.

from __future__ import annotations

"""Minimal HTTP API server for control plane multi-team analytics."""

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from redcon.control_plane.store import ControlPlaneStore

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

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path = parsed.path.rstrip("/")

        # Collections
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

        # Individual resources
        elif m := re.fullmatch(r"/orgs/(\d+)", path):
            org = self.store.get_org(int(m.group(1)))
            if org is None:
                _error(self, "org not found", 404)
            else:
                _json_response(self, org.as_dict())

        elif m := re.fullmatch(r"/projects/(\d+)", path):
            project = self.store.get_project(int(m.group(1)))
            if project is None:
                _error(self, "project not found", 404)
            else:
                _json_response(self, project.as_dict())

        elif m := re.fullmatch(r"/repos/(\d+)/stats", path):
            stats = self.store.get_repo_stats(int(m.group(1)))
            if stats is None:
                _error(self, "repo not found", 404)
            else:
                _json_response(self, stats)

        elif m := re.fullmatch(r"/repos/(\d+)", path):
            repo = self.store.get_repo(int(m.group(1)))
            if repo is None:
                _error(self, "repo not found", 404)
            else:
                _json_response(self, repo.as_dict())

        elif m := re.fullmatch(r"/runs/(\d+)", path):
            run = self.store.get_run(int(m.group(1)))
            if run is None:
                _error(self, "run not found", 404)
            else:
                _json_response(self, run.as_dict())

        else:
            _error(self, f"unknown route: {path}", 404)

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        body = self._read_json_body()
        if body is None:
            return

        if path == "/orgs":
            name = body.get("name", "")
            slug = body.get("slug", "")
            if not name or not slug:
                _error(self, "name and slug are required")
                return
            try:
                org = self.store.create_org(name, slug)
            except Exception as exc:
                _error(self, str(exc))
                return
            _json_response(self, org.as_dict(), 201)

        elif path == "/projects":
            org_id = body.get("org_id")
            name = body.get("name", "")
            slug = body.get("slug", "")
            if not isinstance(org_id, int) or not name or not slug:
                _error(self, "org_id (int), name, and slug are required")
                return
            if self.store.get_org(org_id) is None:
                _error(self, "org not found", 404)
                return
            try:
                project = self.store.create_project(org_id, name, slug)
            except Exception as exc:
                _error(self, str(exc))
                return
            _json_response(self, project.as_dict(), 201)

        elif path == "/repos":
            project_id = body.get("project_id")
            name = body.get("name", "")
            repo_path = body.get("path", "")
            if not isinstance(project_id, int) or not name:
                _error(self, "project_id (int) and name are required")
                return
            if self.store.get_project(project_id) is None:
                _error(self, "project not found", 404)
                return
            try:
                repo = self.store.create_repo(project_id, name, path=repo_path)
            except Exception as exc:
                _error(self, str(exc))
                return
            _json_response(self, repo.as_dict(), 201)

        elif path == "/runs":
            repo_id = body.get("repo_id")
            if not isinstance(repo_id, int):
                _error(self, "repo_id (int) is required")
                return
            if self.store.get_repo(repo_id) is None:
                _error(self, "repo not found", 404)
                return
            try:
                run = self.store.create_run(
                    repo_id,
                    task=body.get("task", ""),
                    token_usage=int(body.get("token_usage", 0)),
                    tokens_saved=int(body.get("tokens_saved", 0)),
                    context_size=int(body.get("context_size", 0)),
                    cache_hits=int(body.get("cache_hits", 0)),
                )
            except Exception as exc:
                _error(self, str(exc))
                return
            _json_response(self, run.as_dict(), 201)

        else:
            _error(self, f"unknown route: {path}", 404)

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            _error(self, f"invalid JSON body: {exc}")
            return None


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
    db_path: str | Path = ".redcon/control_plane.db",
    *,
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> ControlPlaneServer:
    """Convenience factory used by the CLI."""
    store = ControlPlaneStore(db_path=db_path)
    return ControlPlaneServer(store, host=host, port=port)
