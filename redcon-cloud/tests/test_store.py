"""Tests for store._extract_params - no database required."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import IncomingEvent
from app.store import _extract_params


def _make_event(name: str = "pack_completed", payload: dict | None = None) -> IncomingEvent:
    return IncomingEvent(
        name=name,
        schema_version="v1",
        timestamp=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        run_id="run-001",
        payload=payload or {},
    )


def test_extract_params_envelope_fields():
    event = _make_event("run_started")
    params = _extract_params(event)
    assert params[0] == "run_started"       # name
    assert params[1] == "v1"               # schema_version
    assert params[3] == "run-001"          # run_id


def test_extract_params_timestamp_has_timezone():
    event = _make_event()
    params = _extract_params(event)
    ts = params[2]
    assert ts.tzinfo is not None


def test_extract_params_naive_timestamp_gets_utc():
    event = IncomingEvent(
        name="run_started",
        schema_version="v1",
        timestamp=datetime(2024, 6, 1, 12, 0, 0),  # naive
        run_id="run-002",
        payload={},
    )
    params = _extract_params(event)
    assert params[2].tzinfo is not None


def test_extract_params_full_payload():
    payload = {
        "command": "pack",
        "repository": {"repository_id": "repo-sha", "workspace_id": "ws-sha"},
        "tokens": {
            "max_tokens": 8000,
            "estimated_input_tokens": 3000,
            "estimated_saved_tokens": 1000,
            "baseline_full_context_tokens": 4000,
        },
        "files": {
            "scanned_files": 50,
            "ranked_files": 30,
            "included_files": 20,
            "skipped_files": 10,
            "top_files": 5,
        },
        "cache": {
            "cache_hits": 3,
            "tokens_saved": 500,
            "backend": "local_file",
        },
        "policy": {
            "evaluated": True,
            "passed": True,
            "violation_count": 0,
        },
    }
    event = _make_event("pack_completed", payload)
    params = _extract_params(event)

    assert params[4] == "pack"          # command
    assert params[5] == "repo-sha"      # repository_id
    assert params[6] == "ws-sha"        # workspace_id
    assert params[7] == 8000            # max_tokens
    assert params[8] == 3000            # estimated_input_tokens
    assert params[9] == 1000            # estimated_saved_tokens
    assert params[10] == 4000           # baseline_full_context_tokens
    assert params[11] == 50             # scanned_files
    assert params[12] == 30             # ranked_files
    assert params[13] == 20             # included_files
    assert params[14] == 10             # skipped_files
    assert params[15] == 5              # top_files
    assert params[16] == 3              # cache_hits
    assert params[17] == 500            # tokens_saved_by_cache
    assert params[18] == "local_file"   # cache_backend
    assert params[19] is True           # policy_evaluated
    assert params[20] is True           # policy_passed
    assert params[21] == 0              # violation_count


def test_extract_params_missing_sections_yield_none():
    event = _make_event("run_started", {})
    params = _extract_params(event)
    assert params[4] is None   # command
    assert params[5] is None   # repository_id
    assert params[7] is None   # max_tokens
    assert params[16] is None  # cache_hits


def test_extract_params_payload_json_is_serialized():
    import json
    payload = {"command": "pack", "extra": [1, 2, 3]}
    event = _make_event("pack_completed", payload)
    params = _extract_params(event)
    # last param is the JSONB payload string
    parsed = json.loads(params[-1])
    assert parsed["command"] == "pack"
    assert parsed["extra"] == [1, 2, 3]
