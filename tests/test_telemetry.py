from __future__ import annotations

import json
from pathlib import Path

from contextbudget import ContextBudgetEngine
from contextbudget.core.pipeline import run_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_events(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_telemetry_disabled_by_default(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "search.py", "def search() -> list[str]:\n    return []\n")

    run_pack("add caching to search api", repo=tmp_path, max_tokens=500)

    assert not (tmp_path / ".contextbudget" / "telemetry.jsonl").exists()


def test_pack_emits_stage_events_to_local_file_sink(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[telemetry]
enabled = true
sink = "file"
file_path = ".contextbudget/events.jsonl"
""".strip(),
    )
    _write(tmp_path / "src" / "search.py", "def search() -> list[str]:\n    return []\n")
    _write(tmp_path / "src" / "cache.py", "def cache() -> None:\n    pass\n")

    run_pack("add caching to search api", repo=tmp_path, max_tokens=500)

    telemetry_path = tmp_path / ".contextbudget" / "events.jsonl"
    assert telemetry_path.exists()
    events = _read_events(telemetry_path)
    names = [event["name"] for event in events]
    assert "run_started" in names
    assert "scan_completed" in names
    assert "scoring_completed" in names
    assert "pack_completed" in names
    assert all("timestamp" in event for event in events)
    assert all("payload" in event for event in events)


def test_policy_failed_event_emitted(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[telemetry]
enabled = true
sink = "file"
file_path = ".contextbudget/events.jsonl"
""".strip(),
    )
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    engine = ContextBudgetEngine()
    run = engine.pack(task="tighten auth checks", repo=tmp_path, max_tokens=300)
    policy = engine.make_policy(max_estimated_input_tokens=1)
    result = engine.evaluate_policy(run, policy=policy)

    assert result["passed"] is False
    events = _read_events(tmp_path / ".contextbudget" / "events.jsonl")
    names = [event["name"] for event in events]
    assert "policy_failed" in names
