from __future__ import annotations

import json
from pathlib import Path

from contextbudget.telemetry.schemas import (
    ANALYTICS_SCHEMA_V1,
    EVENT_SCHEMA_VERSIONS,
    build_analytics_payload,
    build_repository_identifiers,
)


TOP_LEVEL_PAYLOAD_KEYS = {
    "command",
    "repository",
    "tokens",
    "files",
    "cache",
    "policy",
    "quality_risk_estimate",
    "benchmark",
    "delta",
}
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


def _assert_payload_shape(payload: dict) -> None:
    assert set(payload) == TOP_LEVEL_PAYLOAD_KEYS
    assert set(payload["repository"]) == REPOSITORY_KEYS
    assert set(payload["tokens"]) == TOKEN_KEYS
    assert set(payload["files"]) == FILE_KEYS
    assert set(payload["cache"]) == CACHE_KEYS
    assert set(payload["policy"]) == POLICY_KEYS
    assert set(payload["benchmark"]) == BENCHMARK_KEYS
    assert set(payload["delta"]) == DELTA_KEYS
    for strategy in payload["benchmark"]["strategies"]:
        assert set(strategy) == BENCHMARK_STRATEGY_KEYS


def test_event_schema_versions_are_stable() -> None:
    assert EVENT_SCHEMA_VERSIONS == {
        "run_started": ANALYTICS_SCHEMA_V1,
        "scan_completed": ANALYTICS_SCHEMA_V1,
        "scoring_completed": ANALYTICS_SCHEMA_V1,
        "pack_completed": ANALYTICS_SCHEMA_V1,
        "plan_completed": ANALYTICS_SCHEMA_V1,
        "cache_hit": ANALYTICS_SCHEMA_V1,
        "delta_applied": ANALYTICS_SCHEMA_V1,
        "benchmark_completed": ANALYTICS_SCHEMA_V1,
        "policy_failed": ANALYTICS_SCHEMA_V1,
        "policy_violation": ANALYTICS_SCHEMA_V1,
    }


def test_repository_identifiers_are_deterministic_and_privacy_safe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    identifiers_a = build_repository_identifiers(repo)
    identifiers_b = build_repository_identifiers(repo)

    assert identifiers_a == identifiers_b
    assert identifiers_a.repository_id.startswith("sha256:")
    assert identifiers_a.workspace_id.startswith("sha256:")
    assert str(repo) not in identifiers_a.repository_id
    assert str(repo.parent) not in identifiers_a.workspace_id


def test_build_analytics_payload_shapes_are_stable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    samples = {
        "run_started": {
            "command": "pack",
            "data": {"max_tokens": 512, "top_files": 25},
        },
        "scan_completed": {
            "command": "pack",
            "data": {"scanned_files": 12},
        },
        "scoring_completed": {
            "command": "plan",
            "data": {"scanned_files": 12, "ranked_files": 12, "top_files": 25},
        },
        "pack_completed": {
            "command": "pack",
            "data": {
                "max_tokens": 512,
                "estimated_input_tokens": 180,
                "estimated_saved_tokens": 70,
                "scanned_files": 12,
                "ranked_files": 12,
                "files_included": 3,
                "files_skipped": 9,
                "top_files": 25,
                "cache_hits": 1,
                "duplicate_reads_prevented": 0,
                "quality_risk_estimate": "low",
            },
        },
        "benchmark_completed": {
            "command": "benchmark",
            "data": {
                "max_tokens": 512,
                "baseline_full_context_tokens": 640,
                "scanned_files": 12,
                "ranked_files": 12,
                "top_files": 25,
                "scan_runtime_ms": 8,
                "strategies": [
                    {
                        "strategy": "compressed_pack",
                        "estimated_input_tokens": 180,
                        "estimated_saved_tokens": 460,
                        "files_included": ["src/auth.py", "src/cache.py"],
                        "files_skipped": ["README.md"],
                        "cache_hits": 1,
                        "duplicate_reads_prevented": 0,
                        "quality_risk_estimate": "low",
                        "runtime_ms": 11,
                    }
                ],
            },
        },
        "plan_completed": {
            "command": "plan_agent",
            "data": {"scanned_files": 12, "ranked_files": 12, "top_files": 25, "total_estimated_tokens": 2400},
        },
        "cache_hit": {
            "command": "pack",
            "data": {"total_hits": 4, "tokens_saved": 1280, "backend": "local_file", "fragment_hits": 2, "fragment_misses": 1},
        },
        "delta_applied": {
            "command": "pack",
            "data": {
                "files_added": 1,
                "files_removed": 0,
                "files_changed": 3,
                "delta_tokens": 840,
                "tokens_saved": 6200,
                "has_previous_run": True,
                "slices_changed": 5,
                "symbols_changed": 2,
            },
        },
        "policy_failed": {
            "command": "pack",
            "data": {
                "max_tokens": 512,
                "estimated_input_tokens": 180,
                "estimated_saved_tokens": 70,
                "files_included": 3,
                "files_skipped": 9,
                "cache_hits": 1,
                "duplicate_reads_prevented": 0,
                "quality_risk_estimate": "medium",
                "violations": ["estimated input tokens 180 exceed max 100"],
                "checks": {
                    "max_estimated_input_tokens": {
                        "actual": 180,
                        "limit": 100,
                        "passed": False,
                    }
                },
            },
        },
        "policy_violation": {
            "command": "pack",
            "data": {
                "max_tokens": 512,
                "estimated_input_tokens": 180,
                "estimated_saved_tokens": 70,
                "files_included": 3,
                "files_skipped": 9,
                "quality_risk_estimate": "medium",
                "violations": ["estimated input tokens 180 exceed max 100"],
                "checks": {
                    "max_estimated_input_tokens": {
                        "actual": 180,
                        "limit": 100,
                        "passed": False,
                    }
                },
            },
        },
    }

    for event_name, sample in samples.items():
        payload = build_analytics_payload(
            event_name,
            command=str(sample["command"]),
            repo=repo,
            data=sample["data"],
        )
        serialized = json.dumps(payload, sort_keys=True)
        _assert_payload_shape(payload)
        assert str(repo) not in serialized

    benchmark_payload = build_analytics_payload(
        "benchmark_completed",
        command="benchmark",
        repo=repo,
        data=samples["benchmark_completed"]["data"],
    )
    assert benchmark_payload["files"]["strategy_count"] == 1
    assert benchmark_payload["benchmark"]["strategies"][0]["name"] == "compressed_pack"
    assert benchmark_payload["benchmark"]["strategies"][0]["included_files"] == 2
    assert benchmark_payload["benchmark"]["strategies"][0]["skipped_files"] == 1
    assert "src/auth.py" not in json.dumps(benchmark_payload, sort_keys=True)

    policy_payload = build_analytics_payload(
        "policy_failed",
        command="pack",
        repo=repo,
        data=samples["policy_failed"]["data"],
    )
    assert policy_payload["policy"]["evaluated"] is True
    assert policy_payload["policy"]["passed"] is False
    assert policy_payload["policy"]["violation_count"] == 1
    assert policy_payload["policy"]["failing_checks"] == ["max_estimated_input_tokens"]
