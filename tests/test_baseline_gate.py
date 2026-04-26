"""Tests for the cmd-bench baseline gating logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from redcon.cmd.benchmark import (
    Benchmark,
    LevelBenchmark,
    compare_to_baseline,
    run_benchmark,
)
from redcon.cmd.compressors.git_diff import GitDiffCompressor

DIFF_FIXTURE = (
    b"diff --git a/foo.py b/foo.py\n"
    b"@@ -1,2 +1,3 @@\n"
    b"-a\n+b\n+c\n"
)


def _baseline_payload(reduction: float) -> list[dict]:
    """Synthesise a one-entry baseline at the given reduction percentage."""
    return [
        {
            "schema": "git_diff",
            "fixture": "synthetic",
            "raw_bytes": 100,
            "raw_tokens": 25,
            "levels": [
                {"level": "verbose", "compressed_tokens": 10, "reduction_pct": reduction, "cold_seconds": 0.001, "warm_seconds": 0.001, "must_preserve_ok": True},
                {"level": "compact", "compressed_tokens": 5, "reduction_pct": reduction, "cold_seconds": 0.001, "warm_seconds": 0.001, "must_preserve_ok": True},
                {"level": "ultra", "compressed_tokens": 3, "reduction_pct": reduction, "cold_seconds": 0.001, "warm_seconds": 0.001, "must_preserve_ok": True},
            ],
        }
    ]


def _current_results(reduction: float) -> list[Benchmark]:
    return [
        Benchmark(
            schema="git_diff",
            fixture="synthetic",
            raw_bytes=100,
            raw_tokens=25,
            levels=tuple(
                LevelBenchmark(
                    level=level,
                    compressed_tokens=5,
                    reduction_pct=reduction,
                    cold_seconds=0.001,
                    warm_seconds=0.001,
                    must_preserve_ok=True,
                )
                for level in ("verbose", "compact", "ultra")
            ),
        )
    ]


def test_no_regression_when_current_matches_baseline(tmp_path: Path):
    payload = _baseline_payload(80.0)
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(payload))

    regressions, summary = compare_to_baseline(
        _current_results(80.0), str(baseline_file), tolerance_pp=5.0
    )
    assert regressions == []
    assert summary["matched"] == 3
    assert summary["regressions"] == 0


def test_regression_caught_when_drop_exceeds_tolerance(tmp_path: Path):
    payload = _baseline_payload(80.0)
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(payload))

    # Current run dropped to 60% reduction - 20pp drop, well over 5pp tol.
    regressions, summary = compare_to_baseline(
        _current_results(60.0), str(baseline_file), tolerance_pp=5.0
    )
    assert summary["regressions"] == 3  # one per level
    assert all("git_diff/synthetic" in r for r in regressions)


def test_drop_within_tolerance_does_not_regress(tmp_path: Path):
    payload = _baseline_payload(80.0)
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(payload))

    # Drop of 3pp is under the 5pp tolerance.
    regressions, _ = compare_to_baseline(
        _current_results(77.0), str(baseline_file), tolerance_pp=5.0
    )
    assert regressions == []


def test_improvement_is_never_a_regression(tmp_path: Path):
    payload = _baseline_payload(80.0)
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(payload))

    # Even a 50pp jump up doesn't fail the gate.
    regressions, _ = compare_to_baseline(
        _current_results(99.0), str(baseline_file), tolerance_pp=1.0
    )
    assert regressions == []


def test_axes_missing_in_baseline_are_reported(tmp_path: Path):
    payload = _baseline_payload(80.0)
    # Strip one level from the baseline so the current run has an axis
    # without a counterpart.
    payload[0]["levels"] = payload[0]["levels"][:1]  # only verbose
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(payload))

    _, summary = compare_to_baseline(
        _current_results(80.0), str(baseline_file), tolerance_pp=5.0
    )
    assert summary["matched"] == 1
    assert summary["missing_in_baseline"] == 2  # compact, ultra


def test_bundled_baseline_self_compares_clean():
    """The checked-in baseline must match the live benchmark on this host
    (within tolerance) so contributors get a green run on a fresh clone.
    """
    from redcon.cmd.benchmark import _default_cases, run_benchmarks

    bundled = Path("benchmarks/cmd_baseline.json")
    if not bundled.is_file():
        pytest.skip("bundled baseline not present")

    current = run_benchmarks(_default_cases())
    regressions, summary = compare_to_baseline(
        current, str(bundled), tolerance_pp=10.0
    )
    # 10pp tolerance is generous to absorb measurement noise on busy CI;
    # what we really enforce is no catastrophic drop.
    assert summary["matched"] > 0
    assert regressions == [], "\n".join(regressions)


def test_smoke_real_benchmark_bundled_baseline_shape():
    """The bundled file must be a parseable JSON list with the expected fields."""
    bundled = Path("benchmarks/cmd_baseline.json")
    if not bundled.is_file():
        pytest.skip("bundled baseline not present")

    payload = json.loads(bundled.read_text())
    assert isinstance(payload, list)
    assert payload, "baseline must not be empty"
    for entry in payload:
        assert "schema" in entry
        assert "fixture" in entry
        assert "levels" in entry
        for level in entry["levels"]:
            assert level["level"] in {"verbose", "compact", "ultra"}
            assert "reduction_pct" in level
