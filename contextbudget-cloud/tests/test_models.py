"""Tests for IncomingEvent Pydantic validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import IncomingEvent, VALID_EVENT_NAMES, SUPPORTED_SCHEMA_VERSIONS


_VALID_EVENT = {
    "name": "run_started",
    "schema_version": "v1",
    "timestamp": "2024-01-01T00:00:00Z",
    "run_id": "abc123",
    "payload": {"command": "pack"},
}


def test_valid_event_parses():
    event = IncomingEvent.model_validate(_VALID_EVENT)
    assert event.name == "run_started"
    assert event.schema_version == "v1"
    assert event.run_id == "abc123"


def test_all_supported_event_names_are_accepted():
    for name in VALID_EVENT_NAMES:
        data = {**_VALID_EVENT, "name": name}
        event = IncomingEvent.model_validate(data)
        assert event.name == name


@pytest.mark.parametrize("name", [
    "run_started", "scan_completed", "pack_completed", "cache_hit", "policy_violation",
])
def test_required_event_names_accepted(name: str):
    event = IncomingEvent.model_validate({**_VALID_EVENT, "name": name})
    assert event.name == name


def test_unknown_event_name_rejected():
    with pytest.raises(ValidationError, match="Unknown event name"):
        IncomingEvent.model_validate({**_VALID_EVENT, "name": "does_not_exist"})


def test_unsupported_schema_version_rejected():
    with pytest.raises(ValidationError, match="Unsupported schema_version"):
        IncomingEvent.model_validate({**_VALID_EVENT, "schema_version": "v999"})


def test_empty_run_id_rejected():
    with pytest.raises(ValidationError, match="run_id must not be empty"):
        IncomingEvent.model_validate({**_VALID_EVENT, "run_id": "   "})


def test_blank_run_id_rejected():
    with pytest.raises(ValidationError, match="run_id must not be empty"):
        IncomingEvent.model_validate({**_VALID_EVENT, "run_id": ""})


def test_payload_defaults_to_empty_dict():
    data = {k: v for k, v in _VALID_EVENT.items() if k != "payload"}
    event = IncomingEvent.model_validate(data)
    assert event.payload == {}


def test_timestamp_parsed_as_datetime():
    from datetime import datetime
    event = IncomingEvent.model_validate(_VALID_EVENT)
    assert isinstance(event.timestamp, datetime)
