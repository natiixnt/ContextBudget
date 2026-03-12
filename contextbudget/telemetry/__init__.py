from __future__ import annotations

"""Telemetry abstraction layer for optional analytics sinks."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class TelemetryEvent:
    """Structured telemetry event payload."""

    name: str
    timestamp: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to JSON-serializable dictionary."""

        return {
            "name": self.name,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "payload": self.payload,
        }


class TelemetrySink(Protocol):
    """Telemetry event sink interface."""

    def emit(self, event: TelemetryEvent) -> None:
        """Consume a telemetry event."""


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
class TelemetrySession:
    """Run-scoped telemetry emitter that adds shared context."""

    sink: TelemetrySink
    base_payload: dict[str, Any] = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: uuid4().hex)

    def emit(self, name: str, **payload: Any) -> None:
        """Emit a structured event for this run session."""

        data = dict(self.base_payload)
        data.update(payload)
        self.sink.emit(
            TelemetryEvent(
                name=name,
                timestamp=_utc_now(),
                run_id=self.run_id,
                payload=data,
            )
        )


def build_telemetry_sink(
    *,
    repo: Path,
    enabled: bool,
    sink: str,
    file_path: str,
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
    return NoOpTelemetrySink()


__all__ = [
    "TelemetryEvent",
    "TelemetrySink",
    "NoOpTelemetrySink",
    "JsonlFileTelemetrySink",
    "TelemetrySession",
    "build_telemetry_sink",
]
