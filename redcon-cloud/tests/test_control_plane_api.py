"""Tests for control plane API endpoints (DB and auth are mocked)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app.main import app

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

_ORG = {"id": 1, "slug": "acme", "display_name": "Acme Corp", "created_at": _NOW}
_PROJECT = {
    "id": 10,
    "org_id": 1,
    "slug": "backend",
    "display_name": "Backend",
    "created_at": _NOW,
}
_REPO = {
    "id": 100,
    "project_id": 10,
    "slug": "api",
    "display_name": "API",
    "repository_id": "abc123",
    "created_at": _NOW,
}
_KEY_RECORD = {
    "id": 5,
    "org_id": 1,
    "key_prefix": "rck_testtest",
    "label": "ci",
    "revoked": False,
    "created_at": _NOW,
}
_POLICY = {
    "id": 20,
    "org_id": 1,
    "project_id": None,
    "repo_id": None,
    "version": "v1",
    "spec": {"max_estimated_input_tokens": 64000},
    "is_active": False,
    "created_at": _NOW,
    "activated_at": None,
}
_AUDIT_ENTRY = {
    "id": 1,
    "org_id": 1,
    "repository_id": None,
    "run_id": None,
    "task_hash": None,
    "endpoint": "POST /orgs/{org_id}/policies",
    "policy_version": "v1",
    "tokens_used": None,
    "tokens_saved": None,
    "violation_count": 0,
    "policy_passed": None,
    "status_code": 201,
    "created_at": _NOW,
}
_VALID_AUTH = {"org_id": 1, "org_slug": "acme", "key_id": 5}


@pytest.fixture()
def client():
    """TestClient with DB pool and auth patched out."""
    mock_pool = object()
    with (
        patch.object(db_module, "_pool", mock_pool),
        patch.object(db_module, "init_pool", new_callable=AsyncMock),
        patch.object(db_module, "close_pool", new_callable=AsyncMock),
        patch("app.main.db.get_pool", return_value=mock_pool),
        patch("app.main.auth.verify_api_key", new_callable=AsyncMock, return_value=_VALID_AUTH),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


_AUTH = {"Authorization": "Bearer rck_testkey"}
_ADMIN_TOKEN = "test-admin-token-abc123"
_ADMIN_AUTH = {"Authorization": f"Bearer {_ADMIN_TOKEN}"}


# ---------------------------------------------------------------------------
# /orgs
# ---------------------------------------------------------------------------

class TestOrgs:
    def test_create_org(self, client):
        with (
            patch("app.main.cp_store.create_org", new_callable=AsyncMock, return_value=_ORG),
            patch("app.main.cfg.ADMIN_TOKEN", _ADMIN_TOKEN),
        ):
            r = client.post("/orgs", json={"slug": "acme", "display_name": "Acme Corp"}, headers=_ADMIN_AUTH)
        assert r.status_code == 201
        body = r.json()
        assert body["slug"] == "acme"
        assert body["id"] == 1

    def test_create_org_requires_admin_token(self, client):
        with patch("app.main.cfg.ADMIN_TOKEN", _ADMIN_TOKEN):
            r = client.post("/orgs", json={"slug": "acme"})
        assert r.status_code == 401

    def test_create_org_wrong_admin_token_returns_403(self, client):
        with patch("app.main.cfg.ADMIN_TOKEN", _ADMIN_TOKEN):
            r = client.post("/orgs", json={"slug": "acme"}, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403

    def test_create_org_duplicate_returns_409(self, client):
        with (
            patch(
                "app.main.cp_store.create_org",
                new_callable=AsyncMock,
                side_effect=Exception("unique constraint"),
            ),
            patch("app.main.cfg.ADMIN_TOKEN", _ADMIN_TOKEN),
        ):
            r = client.post("/orgs", json={"slug": "acme", "display_name": "Acme"}, headers=_ADMIN_AUTH)
        assert r.status_code == 409

    def test_list_orgs_requires_auth(self, client):
        with patch("app.main.auth.verify_api_key", new_callable=AsyncMock, return_value=None):
            r = client.get("/orgs", headers={"Authorization": "Bearer bad"})
        assert r.status_code == 401

    def test_list_orgs(self, client):
        with patch(
            "app.main.cp_store.list_orgs", new_callable=AsyncMock, return_value=[_ORG]
        ):
            r = client.get("/orgs", headers=_AUTH)
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_get_org_not_found(self, client):
        with patch("app.main.cp_store.get_org", new_callable=AsyncMock, return_value=None):
            r = client.get("/orgs/999", headers=_AUTH)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /orgs/{org_id}/projects
# ---------------------------------------------------------------------------

class TestProjects:
    def test_create_project(self, client):
        with patch(
            "app.main.cp_store.create_project",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ):
            r = client.post(
                "/orgs/1/projects",
                json={"slug": "backend", "display_name": "Backend"},
                headers=_AUTH,
            )
        assert r.status_code == 201
        assert r.json()["org_id"] == 1

    def test_list_projects(self, client):
        with patch(
            "app.main.cp_store.list_projects",
            new_callable=AsyncMock,
            return_value=[_PROJECT],
        ):
            r = client.get("/orgs/1/projects", headers=_AUTH)
        assert r.status_code == 200
        assert r.json()[0]["slug"] == "backend"


# ---------------------------------------------------------------------------
# /orgs/{org_id}/projects/{project_id}/repos
# ---------------------------------------------------------------------------

class TestRepos:
    def test_create_repo(self, client):
        with (
            patch("app.main.cp_store.get_project", new_callable=AsyncMock, return_value=_PROJECT),
            patch("app.main.cp_store.create_repo", new_callable=AsyncMock, return_value=_REPO),
        ):
            r = client.post(
                "/orgs/1/projects/10/repos",
                json={"slug": "api", "display_name": "API", "repository_id": "abc123"},
                headers=_AUTH,
            )
        assert r.status_code == 201
        assert r.json()["repository_id"] == "abc123"

    def test_create_repo_wrong_org_returns_404(self, client):
        wrong_project = {**_PROJECT, "org_id": 999}
        with patch(
            "app.main.cp_store.get_project",
            new_callable=AsyncMock,
            return_value=wrong_project,
        ):
            r = client.post(
                "/orgs/1/projects/10/repos",
                json={"slug": "api", "display_name": "API"},
                headers=_AUTH,
            )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /orgs/{org_id}/api-keys
# ---------------------------------------------------------------------------

class TestApiKeys:
    def test_issue_api_key_returns_raw_key(self, client):
        with (
            patch("app.main.cp_store.get_org", new_callable=AsyncMock, return_value=_ORG),
            patch(
                "app.main.cp_store.issue_api_key",
                new_callable=AsyncMock,
                return_value=("rck_" + "x" * 64, _KEY_RECORD),
            ),
        ):
            r = client.post("/orgs/1/api-keys", json={"label": "ci"})
        assert r.status_code == 201
        body = r.json()
        assert "raw_key" in body
        assert body["raw_key"].startswith("rck_")

    def test_issue_api_key_org_not_found(self, client):
        with patch("app.main.cp_store.get_org", new_callable=AsyncMock, return_value=None):
            r = client.post("/orgs/999/api-keys", json={})
        assert r.status_code == 404

    def test_list_api_keys(self, client):
        key_list = [{**_KEY_RECORD, "revoked_at": None}]
        with patch(
            "app.main.cp_store.list_api_keys",
            new_callable=AsyncMock,
            return_value=key_list,
        ):
            r = client.get("/orgs/1/api-keys", headers=_AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body[0]["key_prefix"] == "rck_testtest"
        assert "raw_key" not in body[0]

    def test_revoke_api_key(self, client):
        with patch(
            "app.main.cp_store.revoke_api_key",
            new_callable=AsyncMock,
            return_value=True,
        ):
            r = client.delete("/orgs/1/api-keys/5", headers=_AUTH)
        assert r.status_code == 204

    def test_revoke_api_key_not_found(self, client):
        with patch(
            "app.main.cp_store.revoke_api_key",
            new_callable=AsyncMock,
            return_value=False,
        ):
            r = client.delete("/orgs/1/api-keys/999", headers=_AUTH)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /orgs/{org_id}/audit-log
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_list_audit_log(self, client):
        with patch(
            "app.main.cp_store.list_audit_log",
            new_callable=AsyncMock,
            return_value=[_AUDIT_ENTRY],
        ):
            r = client.get("/orgs/1/audit-log", headers=_AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["entries"][0]["endpoint"] == "POST /orgs/{org_id}/policies"

    def test_audit_log_respects_limit(self, client):
        with patch(
            "app.main.cp_store.list_audit_log",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fn:
            r = client.get("/orgs/1/audit-log?limit=10&offset=20", headers=_AUTH)
        assert r.status_code == 200
        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs["limit"] == 10
        assert kwargs["offset"] == 20


# ---------------------------------------------------------------------------
# /orgs/{org_id}/policies
# ---------------------------------------------------------------------------

class TestPolicies:
    def test_create_policy_version(self, client):
        with (
            patch(
                "app.main.cp_store.create_policy_version",
                new_callable=AsyncMock,
                return_value=_POLICY,
            ),
            patch("app.main.cp_store.append_audit_entry", new_callable=AsyncMock),
        ):
            r = client.post(
                "/orgs/1/policies",
                json={
                    "version": "v1",
                    "spec": {"max_estimated_input_tokens": 64000},
                },
                headers=_AUTH,
            )
        assert r.status_code == 201
        assert r.json()["version"] == "v1"

    def test_activate_policy_version(self, client):
        active_policy = {**_POLICY, "is_active": True}
        with (
            patch(
                "app.main.cp_store.activate_policy_version",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.main.cp_store.list_policy_versions",
                new_callable=AsyncMock,
                return_value=[active_policy],
            ),
            patch("app.main.cp_store.append_audit_entry", new_callable=AsyncMock),
        ):
            r = client.put("/orgs/1/policies/20/activate", headers=_AUTH)
        assert r.status_code == 200
        assert r.json()["is_active"] is True

    def test_activate_policy_not_found(self, client):
        with patch(
            "app.main.cp_store.activate_policy_version",
            new_callable=AsyncMock,
            return_value=False,
        ):
            r = client.put("/orgs/1/policies/999/activate", headers=_AUTH)
        assert r.status_code == 404

    def test_get_active_policy_none(self, client):
        with patch(
            "app.main.cp_store.get_active_policy",
            new_callable=AsyncMock,
            return_value=None,
        ):
            r = client.get("/policies/active?org_id=1", headers=_AUTH)
        assert r.status_code == 200
        assert r.json() is None


# ---------------------------------------------------------------------------
# /analytics/cost
# ---------------------------------------------------------------------------

class TestCostAnalytics:
    _SUMMARY = {
        "baseline_tokens": 10000,
        "optimized_tokens": 4000,
        "tokens_saved": 6000,
        "savings_rate": 0.6,
        "run_count": 5,
    }

    def test_cost_summary(self, client):
        with patch(
            "app.main.cp_queries.cost_summary",
            new_callable=AsyncMock,
            return_value=self._SUMMARY,
        ):
            r = client.get("/analytics/cost", headers=_AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["tokens_saved"] == 6000
        assert body["savings_rate"] == 0.6

    def test_cost_summary_with_repo_filter(self, client):
        with patch(
            "app.main.cp_queries.cost_summary",
            new_callable=AsyncMock,
            return_value=self._SUMMARY,
        ) as mock_fn:
            r = client.get("/analytics/cost?repository_id=repo-sha256", headers=_AUTH)
        assert r.status_code == 200
        _, kwargs = mock_fn.call_args
        assert kwargs["repository_id"] == "repo-sha256"

    def test_cost_by_repo(self, client):
        rows = [{
            "repository_id": "sha1",
            "baseline_tokens": 5000,
            "optimized_tokens": 2000,
            "tokens_saved": 3000,
            "savings_rate": 0.6,
            "run_count": 3,
        }]
        with patch(
            "app.main.cp_queries.cost_by_repo",
            new_callable=AsyncMock,
            return_value=rows,
        ):
            r = client.get("/analytics/cost/by-repo", headers=_AUTH)
        assert r.status_code == 200
        assert r.json()["repositories"][0]["repository_id"] == "sha1"

    def test_cost_by_date(self, client):
        rows = [{
            "date": "2026-03-15",
            "baseline_tokens": 2000,
            "optimized_tokens": 800,
            "tokens_saved": 1200,
            "savings_rate": 0.6,
            "run_count": 2,
        }]
        with patch(
            "app.main.cp_queries.cost_by_date",
            new_callable=AsyncMock,
            return_value=rows,
        ):
            r = client.get("/analytics/cost/by-date", headers=_AUTH)
        assert r.status_code == 200
        assert r.json()["days"][0]["date"] == "2026-03-15"

    def test_cost_analytics_requires_auth(self, client):
        with patch("app.main.auth.verify_api_key", new_callable=AsyncMock, return_value=None):
            r = client.get("/analytics/cost", headers={"Authorization": "Bearer bad"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# DELETE endpoints
# ---------------------------------------------------------------------------

class TestDeleteEndpoints:
    def test_delete_org(self, client):
        with patch("app.main.cp_store.delete_org", new_callable=AsyncMock, return_value=True):
            r = client.delete("/orgs/1", headers=_AUTH)
        assert r.status_code == 204

    def test_delete_org_not_found(self, client):
        with patch("app.main.cp_store.delete_org", new_callable=AsyncMock, return_value=False):
            r = client.delete("/orgs/1", headers=_AUTH)
        assert r.status_code == 404

    def test_delete_org_wrong_org_returns_403(self, client):
        # Authenticated as org 1, trying to delete org 2
        r = client.delete("/orgs/2", headers=_AUTH)
        assert r.status_code == 403

    def test_delete_project(self, client):
        with (
            patch("app.main.cp_store.get_project", new_callable=AsyncMock, return_value=_PROJECT),
            patch("app.main.cp_store.delete_project", new_callable=AsyncMock, return_value=True),
        ):
            r = client.delete("/orgs/1/projects/10", headers=_AUTH)
        assert r.status_code == 204

    def test_delete_project_not_found_or_wrong_org(self, client):
        # cp_store.delete_project uses WHERE id=$1 AND org_id=$2 — returns False for either case
        with patch("app.main.cp_store.delete_project", new_callable=AsyncMock, return_value=False):
            r = client.delete("/orgs/1/projects/10", headers=_AUTH)
        assert r.status_code == 404

    def test_delete_repo(self, client):
        with (
            patch("app.main.cp_store.get_project", new_callable=AsyncMock, return_value=_PROJECT),
            patch("app.main.cp_store.delete_repo", new_callable=AsyncMock, return_value=True),
        ):
            r = client.delete("/orgs/1/projects/10/repos/100", headers=_AUTH)
        assert r.status_code == 204

    def test_delete_repo_not_found(self, client):
        with (
            patch("app.main.cp_store.get_project", new_callable=AsyncMock, return_value=_PROJECT),
            patch("app.main.cp_store.delete_repo", new_callable=AsyncMock, return_value=False),
        ):
            r = client.delete("/orgs/1/projects/10/repos/100", headers=_AUTH)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /orgs/{org_id}/audit-log (gateway push)
# ---------------------------------------------------------------------------

class TestAuditLogPush:
    def test_append_audit_log_entry(self, client):
        with patch("app.main.cp_store.append_audit_entry", new_callable=AsyncMock, return_value=42):
            r = client.post(
                "/orgs/1/audit-log",
                json={"endpoint": "/prepare-context", "tokens_used": 3000, "status_code": 200},
                headers=_AUTH,
            )
        assert r.status_code == 201
        assert r.json()["id"] == 42

    def test_append_audit_log_requires_auth(self, client):
        with patch("app.main.auth.verify_api_key", new_callable=AsyncMock, return_value=None):
            r = client.post(
                "/orgs/1/audit-log",
                json={"endpoint": "/prepare-context"},
                headers={"Authorization": "Bearer bad"},
            )
        assert r.status_code == 401
