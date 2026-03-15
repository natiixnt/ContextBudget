from __future__ import annotations

"""Tests for control plane store and HTTP server."""

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from redcon.control_plane.models import AgentRun, Organization, Project, Repository
from redcon.control_plane.server import _Handler
from redcon.control_plane.store import ControlPlaneStore


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


def _store() -> ControlPlaneStore:
    return ControlPlaneStore(db_path=":memory:")


def test_store_create_and_get_org() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    assert isinstance(org, Organization)
    assert org.name == "Acme"
    assert org.slug == "acme"
    assert org.id > 0
    fetched = store.get_org(org.id)
    assert fetched is not None
    assert fetched.id == org.id


def test_store_list_orgs() -> None:
    store = _store()
    store.create_org("Acme", "acme")
    store.create_org("Beta", "beta")
    orgs = store.list_orgs()
    assert len(orgs) == 2
    assert {o.slug for o in orgs} == {"acme", "beta"}


def test_store_get_org_returns_none_for_missing() -> None:
    store = _store()
    assert store.get_org(999) is None


def test_store_create_and_get_project() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    project = store.create_project(org.id, "Backend", "backend")
    assert isinstance(project, Project)
    assert project.org_id == org.id
    assert project.name == "Backend"
    fetched = store.get_project(project.id)
    assert fetched is not None
    assert fetched.slug == "backend"


def test_store_list_projects_filtered_by_org() -> None:
    store = _store()
    org1 = store.create_org("Acme", "acme")
    org2 = store.create_org("Beta", "beta")
    store.create_project(org1.id, "P1", "p1")
    store.create_project(org1.id, "P2", "p2")
    store.create_project(org2.id, "P3", "p3")
    assert len(store.list_projects(org_id=org1.id)) == 2
    assert len(store.list_projects(org_id=org2.id)) == 1
    assert len(store.list_projects()) == 3


def test_store_create_and_get_repo() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    project = store.create_project(org.id, "Backend", "backend")
    repo = store.create_repo(project.id, "api", path="/srv/api")
    assert isinstance(repo, Repository)
    assert repo.project_id == project.id
    assert repo.path == "/srv/api"
    fetched = store.get_repo(repo.id)
    assert fetched is not None
    assert fetched.name == "api"


def test_store_list_repos_filtered_by_project() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    proj1 = store.create_project(org.id, "P1", "p1")
    proj2 = store.create_project(org.id, "P2", "p2")
    store.create_repo(proj1.id, "r1")
    store.create_repo(proj1.id, "r2")
    store.create_repo(proj2.id, "r3")
    assert len(store.list_repos(project_id=proj1.id)) == 2
    assert len(store.list_repos(project_id=proj2.id)) == 1
    assert len(store.list_repos()) == 3


def test_store_create_and_get_run() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    proj = store.create_project(org.id, "P1", "p1")
    repo = store.create_repo(proj.id, "api")
    run = store.create_run(
        repo.id,
        task="add caching",
        token_usage=1000,
        tokens_saved=400,
        context_size=8000,
        cache_hits=3,
    )
    assert isinstance(run, AgentRun)
    assert run.repo_id == repo.id
    assert run.token_usage == 1000
    assert run.tokens_saved == 400
    assert run.context_size == 8000
    assert run.cache_hits == 3
    fetched = store.get_run(run.id)
    assert fetched is not None
    assert fetched.task == "add caching"


def test_store_list_runs_filtered_by_repo() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    proj = store.create_project(org.id, "P1", "p1")
    repo1 = store.create_repo(proj.id, "r1")
    repo2 = store.create_repo(proj.id, "r2")
    store.create_run(repo1.id, task="t1")
    store.create_run(repo1.id, task="t2")
    store.create_run(repo2.id, task="t3")
    assert len(store.list_runs(repo_id=repo1.id)) == 2
    assert len(store.list_runs(repo_id=repo2.id)) == 1
    assert len(store.list_runs()) == 3


def test_store_get_repo_stats_aggregates_correctly() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    proj = store.create_project(org.id, "P1", "p1")
    repo = store.create_repo(proj.id, "api")
    store.create_run(repo.id, token_usage=1000, tokens_saved=200, context_size=4000, cache_hits=2)
    store.create_run(repo.id, token_usage=2000, tokens_saved=600, context_size=8000, cache_hits=5)
    stats = store.get_repo_stats(repo.id)
    assert stats is not None
    assert stats["repo_id"] == repo.id
    assert stats["run_count"] == 2
    assert stats["total_token_usage"] == 3000
    assert stats["total_tokens_saved"] == 800
    assert stats["total_cache_hits"] == 7
    assert stats["avg_context_size"] == 6000.0


def test_store_get_repo_stats_returns_none_for_missing_repo() -> None:
    store = _store()
    assert store.get_repo_stats(999) is None


def test_store_get_repo_stats_empty_runs() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    proj = store.create_project(org.id, "P1", "p1")
    repo = store.create_repo(proj.id, "api")
    stats = store.get_repo_stats(repo.id)
    assert stats is not None
    assert stats["run_count"] == 0
    assert stats["total_token_usage"] == 0
    assert stats["avg_context_size"] == 0.0


def test_store_as_dict_round_trips() -> None:
    store = _store()
    org = store.create_org("Acme", "acme")
    d = org.as_dict()
    assert d["name"] == "Acme"
    assert d["slug"] == "acme"
    assert "created_at" in d


# ---------------------------------------------------------------------------
# HTTP server helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _TestServer:
    """Spins up a ControlPlane HTTP server in a background thread for testing."""

    def __init__(self, store: ControlPlaneStore) -> None:
        handler_cls = type("Handler", (_Handler,), {"store": store})
        self._port = _free_port()
        self._server = HTTPServer(("127.0.0.1", self._port), handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def get(self, path: str) -> tuple[int, dict]:
        req = urllib.request.Request(f"{self.base_url}{path}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def close(self) -> None:
        self._server.shutdown()


@pytest.fixture()
def srv() -> _TestServer:  # type: ignore[misc]
    store = _store()
    server = _TestServer(store)
    yield server
    server.close()


@pytest.fixture()
def populated_srv() -> _TestServer:  # type: ignore[misc]
    store = _store()
    org = store.create_org("Acme", "acme")
    proj = store.create_project(org.id, "Backend", "backend")
    repo = store.create_repo(proj.id, "api", path="/srv/api")
    store.create_run(repo.id, task="add caching", token_usage=1000, tokens_saved=300, context_size=4000, cache_hits=2)
    store.create_run(repo.id, task="fix auth", token_usage=500, tokens_saved=100, context_size=2000, cache_hits=1)
    server = _TestServer(store)
    yield server
    server.close()


# ---------------------------------------------------------------------------
# GET endpoint tests
# ---------------------------------------------------------------------------


def test_server_get_orgs_empty(srv: _TestServer) -> None:
    status, body = srv.get("/orgs")
    assert status == 200
    assert body["orgs"] == []


def test_server_get_orgs_after_create(srv: _TestServer) -> None:
    srv.post("/orgs", {"name": "Acme", "slug": "acme"})
    status, body = srv.get("/orgs")
    assert status == 200
    assert len(body["orgs"]) == 1
    assert body["orgs"][0]["slug"] == "acme"


def test_server_get_org_by_id(populated_srv: _TestServer) -> None:
    _, list_body = populated_srv.get("/orgs")
    org_id = list_body["orgs"][0]["id"]
    status, body = populated_srv.get(f"/orgs/{org_id}")
    assert status == 200
    assert body["slug"] == "acme"


def test_server_get_org_by_id_not_found(srv: _TestServer) -> None:
    status, body = srv.get("/orgs/999")
    assert status == 404
    assert "error" in body


def test_server_get_projects_filtered(populated_srv: _TestServer) -> None:
    _, orgs_body = populated_srv.get("/orgs")
    org_id = orgs_body["orgs"][0]["id"]
    status, body = populated_srv.get(f"/projects?org_id={org_id}")
    assert status == 200
    assert len(body["projects"]) == 1
    assert body["projects"][0]["slug"] == "backend"


def test_server_get_project_by_id(populated_srv: _TestServer) -> None:
    _, projs = populated_srv.get("/projects")
    proj_id = projs["projects"][0]["id"]
    status, body = populated_srv.get(f"/projects/{proj_id}")
    assert status == 200
    assert body["name"] == "Backend"


def test_server_get_project_by_id_not_found(srv: _TestServer) -> None:
    status, body = srv.get("/projects/999")
    assert status == 404


def test_server_get_repos_filtered(populated_srv: _TestServer) -> None:
    _, projs = populated_srv.get("/projects")
    proj_id = projs["projects"][0]["id"]
    status, body = populated_srv.get(f"/repos?project_id={proj_id}")
    assert status == 200
    assert len(body["repos"]) == 1
    assert body["repos"][0]["name"] == "api"


def test_server_get_repo_by_id(populated_srv: _TestServer) -> None:
    _, repos = populated_srv.get("/repos")
    repo_id = repos["repos"][0]["id"]
    status, body = populated_srv.get(f"/repos/{repo_id}")
    assert status == 200
    assert body["path"] == "/srv/api"


def test_server_get_repo_by_id_not_found(srv: _TestServer) -> None:
    status, body = srv.get("/repos/999")
    assert status == 404


def test_server_get_repo_stats(populated_srv: _TestServer) -> None:
    _, repos = populated_srv.get("/repos")
    repo_id = repos["repos"][0]["id"]
    status, body = populated_srv.get(f"/repos/{repo_id}/stats")
    assert status == 200
    assert body["repo_id"] == repo_id
    assert body["run_count"] == 2
    assert body["total_token_usage"] == 1500
    assert body["total_tokens_saved"] == 400
    assert body["total_cache_hits"] == 3
    assert body["avg_context_size"] == 3000.0


def test_server_get_repo_stats_not_found(srv: _TestServer) -> None:
    status, body = srv.get("/repos/999/stats")
    assert status == 404


def test_server_get_runs_filtered(populated_srv: _TestServer) -> None:
    _, repos = populated_srv.get("/repos")
    repo_id = repos["repos"][0]["id"]
    status, body = populated_srv.get(f"/runs?repo_id={repo_id}")
    assert status == 200
    assert len(body["runs"]) == 2
    tasks = {r["task"] for r in body["runs"]}
    assert tasks == {"add caching", "fix auth"}


def test_server_get_run_by_id(populated_srv: _TestServer) -> None:
    _, runs = populated_srv.get("/runs")
    run_id = runs["runs"][0]["id"]
    status, body = populated_srv.get(f"/runs/{run_id}")
    assert status == 200
    assert "token_usage" in body
    assert "tokens_saved" in body
    assert "context_size" in body
    assert "cache_hits" in body


def test_server_get_run_by_id_not_found(srv: _TestServer) -> None:
    status, body = srv.get("/runs/999")
    assert status == 404


def test_server_unknown_route_returns_404(srv: _TestServer) -> None:
    status, body = srv.get("/unknown")
    assert status == 404
    assert "error" in body


# ---------------------------------------------------------------------------
# POST endpoint tests
# ---------------------------------------------------------------------------


def test_server_post_org_creates_and_returns_201(srv: _TestServer) -> None:
    status, body = srv.post("/orgs", {"name": "Acme", "slug": "acme"})
    assert status == 201
    assert body["name"] == "Acme"
    assert body["slug"] == "acme"
    assert body["id"] > 0
    assert "created_at" in body


def test_server_post_org_missing_fields_returns_400(srv: _TestServer) -> None:
    status, body = srv.post("/orgs", {"name": "Acme"})
    assert status == 400
    assert "error" in body


def test_server_post_org_duplicate_slug_returns_400(srv: _TestServer) -> None:
    srv.post("/orgs", {"name": "Acme", "slug": "acme"})
    status, body = srv.post("/orgs", {"name": "Acme2", "slug": "acme"})
    assert status == 400


def test_server_post_project_creates_and_returns_201(srv: _TestServer) -> None:
    _, org_body = srv.post("/orgs", {"name": "Acme", "slug": "acme"})
    org_id = org_body["id"]
    status, body = srv.post("/projects", {"org_id": org_id, "name": "Backend", "slug": "backend"})
    assert status == 201
    assert body["org_id"] == org_id
    assert body["slug"] == "backend"


def test_server_post_project_missing_org_returns_404(srv: _TestServer) -> None:
    status, body = srv.post("/projects", {"org_id": 999, "name": "P", "slug": "p"})
    assert status == 404


def test_server_post_project_missing_fields_returns_400(srv: _TestServer) -> None:
    status, body = srv.post("/projects", {"name": "P", "slug": "p"})
    assert status == 400


def test_server_post_repo_creates_and_returns_201(srv: _TestServer) -> None:
    _, org = srv.post("/orgs", {"name": "Acme", "slug": "acme"})
    _, proj = srv.post("/projects", {"org_id": org["id"], "name": "Backend", "slug": "backend"})
    status, body = srv.post("/repos", {"project_id": proj["id"], "name": "api", "path": "/srv/api"})
    assert status == 201
    assert body["project_id"] == proj["id"]
    assert body["name"] == "api"
    assert body["path"] == "/srv/api"


def test_server_post_repo_missing_project_returns_404(srv: _TestServer) -> None:
    status, body = srv.post("/repos", {"project_id": 999, "name": "api"})
    assert status == 404


def test_server_post_repo_missing_fields_returns_400(srv: _TestServer) -> None:
    status, body = srv.post("/repos", {"name": "api"})
    assert status == 400


def test_server_post_run_creates_and_returns_201(srv: _TestServer) -> None:
    _, org = srv.post("/orgs", {"name": "Acme", "slug": "acme"})
    _, proj = srv.post("/projects", {"org_id": org["id"], "name": "Backend", "slug": "backend"})
    _, repo = srv.post("/repos", {"project_id": proj["id"], "name": "api"})
    status, body = srv.post(
        "/runs",
        {
            "repo_id": repo["id"],
            "task": "add caching",
            "token_usage": 1200,
            "tokens_saved": 400,
            "context_size": 6000,
            "cache_hits": 3,
        },
    )
    assert status == 201
    assert body["repo_id"] == repo["id"]
    assert body["task"] == "add caching"
    assert body["token_usage"] == 1200
    assert body["tokens_saved"] == 400
    assert body["context_size"] == 6000
    assert body["cache_hits"] == 3
    assert "created_at" in body


def test_server_post_run_missing_repo_returns_404(srv: _TestServer) -> None:
    status, body = srv.post("/runs", {"repo_id": 999})
    assert status == 404


def test_server_post_run_missing_repo_id_returns_400(srv: _TestServer) -> None:
    status, body = srv.post("/runs", {"task": "do something"})
    assert status == 400


def test_server_post_run_defaults_zero_metrics(srv: _TestServer) -> None:
    _, org = srv.post("/orgs", {"name": "Acme", "slug": "acme"})
    _, proj = srv.post("/projects", {"org_id": org["id"], "name": "B", "slug": "b"})
    _, repo = srv.post("/repos", {"project_id": proj["id"], "name": "r"})
    status, body = srv.post("/runs", {"repo_id": repo["id"]})
    assert status == 201
    assert body["token_usage"] == 0
    assert body["tokens_saved"] == 0
    assert body["cache_hits"] == 0


def test_server_post_unknown_route_returns_404(srv: _TestServer) -> None:
    status, body = srv.post("/unknown", {})
    assert status == 404
