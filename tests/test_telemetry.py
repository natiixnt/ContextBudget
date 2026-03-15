from __future__ import annotations

import json
from pathlib import Path

from contextbudget import ContextBudgetEngine
from contextbudget.core.pipeline import run_pack
from contextbudget.telemetry import EVENT_SCHEMA_VERSIONS


EVENT_KEYS = {"name", "schema_version", "timestamp", "run_id", "payload"}
PAYLOAD_KEYS = {"command", "repository", "tokens", "files", "cache", "policy", "quality_risk_estimate", "benchmark", "delta"}
REPOSITORY_KEYS = {"repository_id", "workspace_id"}
TOKEN_KEYS = {"max_tokens", "estimated_input_tokens", "estimated_saved_tokens", "baseline_full_context_tokens"}
FILE_KEYS = {"scanned_files", "ranked_files", "included_files", "skipped_files", "top_files", "strategy_count"}
CACHE_KEYS = {"cache_hits", "duplicate_reads_prevented", "tokens_saved", "backend", "fragment_hits", "fragment_misses"}
POLICY_KEYS = {"evaluated", "passed", "violation_count", "violations", "failing_checks", "checks"}
BENCHMARK_KEYS = {"scan_runtime_ms", "strategies"}
DELTA_KEYS = {"files_added", "files_removed", "files_changed", "delta_tokens", "tokens_saved", "has_previous_run", "slices_changed", "symbols_changed"}
BENCHMARK_STRATEGY_KEYS = {
    "name",
    "estimated_input_tokens",
    "estimated_saved_tokens",
    "included_files",
    "skipped_files",
    "cache_hits",
    "duplicate_reads_prevented",
    "quality_risk_estimate",
    "runtime_ms",
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_events(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _assert_event_shape(event: dict) -> None:
    assert set(event) == EVENT_KEYS
    assert event["schema_version"] == EVENT_SCHEMA_VERSIONS[event["name"]]
    payload = event["payload"]
    assert set(payload) == PAYLOAD_KEYS
    assert set(payload["repository"]) == REPOSITORY_KEYS
    assert set(payload["tokens"]) == TOKEN_KEYS
    assert set(payload["files"]) == FILE_KEYS
    assert set(payload["cache"]) == CACHE_KEYS
    assert set(payload["policy"]) == POLICY_KEYS
    assert set(payload["benchmark"]) == BENCHMARK_KEYS
    assert set(payload["delta"]) == DELTA_KEYS
    for strategy in payload["benchmark"]["strategies"]:
        assert set(strategy) == BENCHMARK_STRATEGY_KEYS


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
    assert [event["name"] for event in events] == [
        "run_started",
        "scan_completed",
        "scoring_completed",
        "pack_completed",
    ]
    for event in events:
        _assert_event_shape(event)

    serialized = telemetry_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "add caching to search api" not in serialized
    assert "def search()" not in serialized

    pack_event = events[-1]
    assert pack_event["payload"]["command"] == "pack"
    assert pack_event["payload"]["tokens"]["estimated_input_tokens"] is not None
    assert pack_event["payload"]["files"]["included_files"] is not None
    assert pack_event["payload"]["cache"]["cache_hits"] is not None


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
    event_names = [e["name"] for e in events]
    assert "policy_failed" in event_names
    policy_event = next(e for e in events if e["name"] == "policy_failed")
    _assert_event_shape(policy_event)
    assert policy_event["payload"]["policy"]["evaluated"] is True
    assert policy_event["payload"]["policy"]["passed"] is False
    assert policy_event["payload"]["policy"]["violation_count"] >= 1
    assert "max_estimated_input_tokens" in policy_event["payload"]["policy"]["failing_checks"]


def test_benchmark_emits_schema_versioned_events_without_nested_pack_events(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[telemetry]
enabled = true
sink = "file"
file_path = ".contextbudget/events.jsonl"
""".strip(),
    )
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")
    _write(tmp_path / "src" / "cache.py", "CACHE_KEY = 'auth'\n")

    engine = ContextBudgetEngine()
    benchmark = engine.benchmark(task="benchmark auth context", repo=tmp_path, max_tokens=400)

    assert benchmark["command"] == "benchmark"
    events = _read_events(tmp_path / ".contextbudget" / "events.jsonl")
    assert [event["name"] for event in events] == [
        "run_started",
        "scan_completed",
        "scoring_completed",
        "benchmark_completed",
    ]
    for event in events:
        _assert_event_shape(event)

    benchmark_event = events[-1]
    assert benchmark_event["payload"]["command"] == "benchmark"
    assert benchmark_event["payload"]["files"]["strategy_count"] == 4
    assert len(benchmark_event["payload"]["benchmark"]["strategies"]) == 4
    assert "pack_completed" not in [event["name"] for event in events]

    serialized = json.dumps(events, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "src/auth.py" not in serialized


def test_delta_applied_event_emitted(tmp_path: Path) -> None:
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

    first_run = run_pack("add caching to search api", repo=tmp_path, max_tokens=500)
    from contextbudget.core.pipeline import as_json_dict
    first_run_dict = as_json_dict(first_run)

    _write(tmp_path / "src" / "cache.py", "def cache() -> None:\n    pass\n")
    run_pack("add caching to search api", repo=tmp_path, max_tokens=500, delta_from=first_run_dict)

    events = _read_events(tmp_path / ".contextbudget" / "events.jsonl")
    event_names = [event["name"] for event in events]
    assert "delta_applied" in event_names

    delta_event = next(e for e in events if e["name"] == "delta_applied")
    _assert_event_shape(delta_event)
    assert delta_event["payload"]["delta"]["has_previous_run"] is True

    serialized = json.dumps(events, sort_keys=True)
    assert str(tmp_path) not in serialized


def test_policy_violation_event_emitted(tmp_path: Path) -> None:
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
    violation_event = next((e for e in events if e["name"] == "policy_violation"), None)
    assert violation_event is not None, "policy_violation event not emitted"
    _assert_event_shape(violation_event)
    assert violation_event["payload"]["policy"]["evaluated"] is True
    assert violation_event["payload"]["policy"]["passed"] is False
    assert violation_event["payload"]["policy"]["violation_count"] >= 1
