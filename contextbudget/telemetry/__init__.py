from __future__ import annotations

"""Telemetry abstraction layer for optional analytics sinks."""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from contextbudget.telemetry.schemas import (
    ANALYTICS_EVENT_NAMES,
    EVENT_SCHEMA_VERSIONS,
    build_analytics_payload,
    build_repository_identifiers,
    schema_version_for_event,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class TelemetryEvent:
    """Structured telemetry event payload."""

    name: str
    schema_version: str
    timestamp: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to JSON-serializable dictionary."""

        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "payload": self.payload,
        }


class TelemetrySink(Protocol):
    """Telemetry event sink interface."""

    def emit(self, event: TelemetryEvent) -> None:
        """Consume a telemetry event."""


class EventEmitter(Protocol):
    """
    Interface for the pipeline-facing emitter.

    Pipeline stages call ``emit(name, **payload)`` without depending on a
    concrete ``TelemetrySession``.  Any object that satisfies this protocol
    can be injected in its place (e.g. a test double or a future cloud-backed
    session).
    """

    def emit(self, name: str, **payload: Any) -> None:
        """Emit a named event with keyword payload fields."""


class NoOpTelemetrySink:
    """Default sink implementation that drops all events."""

    def emit(self, event: TelemetryEvent) -> None:
        del event


@dataclass(slots=True)
class JsonlFileTelemetrySink:
    """
    Local development sink writing JSONL events to disk.

    This sink does not perform any network operations.
    """

    path: Path

    def emit(self, event: TelemetryEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True))
            handle.write("\n")


@dataclass(slots=True)
class HttpTelemetrySink:
    """
    Sink that forwards events to the ContextBudget Cloud ingestion endpoint.

    Delivery is best-effort: network errors are silently swallowed so they
    never interrupt the main pipeline.
    """

    url: str
    timeout: float = 5.0

    def emit(self, event: TelemetryEvent) -> None:
        body = json.dumps(event.to_dict(), sort_keys=True).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                pass
        except Exception:
            pass


@dataclass(slots=True)
class TelemetrySession:
    """Run-scoped telemetry emitter that adds shared context."""

    sink: TelemetrySink
    base_payload: dict[str, Any] = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: uuid4().hex)

    def emit(self, name: str, **payload: Any) -> None:
        """Emit a structured event for this run session."""

        command = str(self.base_payload.get("command", ""))
        repo = self.base_payload.get("repo")
        self.sink.emit(
            TelemetryEvent(
                name=name,
                schema_version=schema_version_for_event(name),
                timestamp=_utc_now(),
                run_id=self.run_id,
                payload=build_analytics_payload(
                    name,
                    command=command,
                    repo=repo if isinstance(repo, (str, Path)) else None,
                    data=payload,
                ),
            )
        )


def build_telemetry_sink(
    *,
    repo: Path,
    enabled: bool,
    sink: str,
    file_path: str,
    endpoint_url: str = "",
) -> TelemetrySink:
    """
    Build sink from config-like values.

    Unknown sink names fall back to the no-op sink.
    """

    if not enabled:
        return NoOpTelemetrySink()

    sink_name = sink.strip().lower()
    if sink_name in {"noop", "none", ""}:
        return NoOpTelemetrySink()
    if sink_name in {"file", "jsonl"}:
        path = Path(file_path)
        if not path.is_absolute():
            path = repo / path
        return JsonlFileTelemetrySink(path=path)
    if sink_name in {"cloud", "http"}:
        url = (
            endpoint_url.strip()
            or os.environ.get("CONTEXTBUDGET_CLOUD_URL", "")
            or "http://localhost:8080/events"
        )
        return HttpTelemetrySink(url=url)
    return NoOpTelemetrySink()


__all__ = [
    "ANALYTICS_EVENT_NAMES",
    "EVENT_SCHEMA_VERSIONS",
    "EventEmitter",
    "TelemetryEvent",
    "TelemetrySink",
    "NoOpTelemetrySink",
    "JsonlFileTelemetrySink",
    "HttpTelemetrySink",
    "TelemetrySession",
    "build_repository_identifiers",
    "build_telemetry_sink",
]
