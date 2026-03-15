"""Tests for POST /events ingestion endpoint (store layer is mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app.main import app


_VALID_EVENT = {
    "name": "run_started",
    "schema_version": "v1",
    "timestamp": "2024-01-01T00:00:00Z",
    "run_id": "run-001",
    "payload": {"command": "pack"},
}


@pytest.fixture()
def client():
    """TestClient with DB pool and store patched out."""
    mock_pool = object()

    with (
        patch.object(db_module, "_pool", mock_pool),
        patch.object(db_module, "init_pool", new_callable=AsyncMock),
        patch.object(db_module, "close_pool", new_callable=AsyncMock),
        patch("app.main.db.get_pool", return_value=mock_pool),
        patch("app.main.store.insert_events_batch", new_callable=AsyncMock) as mock_insert,
    ):
        mock_insert.return_value = [1]
        with TestClient(app, raise_server_exceptions=True) as c:
            c._mock_insert = mock_insert
            yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_post_single_event_object(client):
    r = client.post("/events", json=_VALID_EVENT)
    assert r.status_code == 201
    body = r.json()
    assert body["accepted"] == 1
    assert body["event_ids"] == [1]


def test_post_batch_of_events(client):
    client._mock_insert.return_value = [10, 11, 12]
    events = [
        {**_VALID_EVENT, "name": "run_started"},
        {**_VALID_EVENT, "name": "scan_completed"},
        {**_VALID_EVENT, "name": "pack_completed"},
    ]
    r = client.post("/events", json=events)
    assert r.status_code == 201
    assert r.json()["accepted"] == 3


def test_post_all_required_event_types(client):
    for name in ("run_started", "scan_completed", "pack_completed", "cache_hit", "policy_violation"):
        client._mock_insert.return_value = [99]
        r = client.post("/events", json={**_VALID_EVENT, "name": name})
        assert r.status_code == 201, f"Expected 201 for {name}, got {r.status_code}"


def test_post_unknown_event_name_rejected(client):
    r = client.post("/events", json={**_VALID_EVENT, "name": "not_a_real_event"})
    assert r.status_code == 422


def test_post_bad_schema_version_rejected(client):
    r = client.post("/events", json={**_VALID_EVENT, "schema_version": "v0"})
    assert r.status_code == 422


def test_post_empty_run_id_rejected(client):
    r = client.post("/events", json={**_VALID_EVENT, "run_id": ""})
    assert r.status_code == 422


def test_post_empty_array_rejected(client):
    r = client.post("/events", json=[])
    assert r.status_code == 422


def test_post_non_json_object_rejected(client):
    r = client.post("/events", json="just a string")
    assert r.status_code == 422


def test_post_missing_required_field_rejected(client):
    data = {k: v for k, v in _VALID_EVENT.items() if k != "run_id"}
    r = client.post("/events", json=data)
    assert r.status_code == 422


def test_post_invalid_item_in_batch_reports_index(client):
    events = [
        _VALID_EVENT,
        {**_VALID_EVENT, "name": "invalid_event_name"},
    ]
    r = client.post("/events", json=events)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["index"] == 1
