from __future__ import annotations

"""Tests for context drift detection (core logic, render, engine, CLI)."""

import json
from pathlib import Path

import pytest

from contextbudget.cache.run_history import RunHistoryEntry, append_run_history_entry
from contextbudget.core.drift import (
    DriftReport,
    _compute_contributors,
    _file_frequency,
    _pct_change,
    _verdict,
    run_drift,
)
from contextbudget.core.render import render_drift_markdown
from contextbudget.engine import ContextBudgetEngine


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _entry(
    generated_at: str,
    task: str,
    token_count: int,
    files: list[str],
) -> RunHistoryEntry:
    return RunHistoryEntry(
        generated_at=generated_at,
        task=task,
        selected_files=files,
        ignored_files=[],
        candidate_files=files,
        token_usage={
            "max_tokens": 8000,
            "estimated_input_tokens": token_count,
            "estimated_saved_tokens": max(0, 8000 - token_count),
            "quality_risk_estimate": "low",
        },
    )


def _write_history(repo: Path, entries: list[RunHistoryEntry]) -> None:
    for entry in entries:
        append_run_history_entry(repo, entry, enabled=True)


def _make_repo(tmp_path: Path, entries: list[RunHistoryEntry]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_history(repo, entries)
    return repo


# ---------------------------------------------------------------------------
# Unit tests: pure helpers
# ---------------------------------------------------------------------------


def test_pct_change_positive():
    assert _pct_change(1000, 1200) == pytest.approx(20.0)


def test_pct_change_negative():
    assert _pct_change(1000, 800) == pytest.approx(-20.0)


def test_pct_change_zero_baseline():
    assert _pct_change(0, 500) == 0.0


def test_verdict_none(threshold=10.0):
    assert _verdict(5.0, threshold) == "none"
    assert _verdict(-9.9, threshold) == "none"


def test_verdict_low():
    assert _verdict(15.0, 10.0) == "low"
    assert _verdict(-20.0, 10.0) == "low"


def test_verdict_moderate():
    assert _verdict(30.0, 10.0) == "moderate"


def test_verdict_significant():
    assert _verdict(75.0, 10.0) == "significant"


def test_verdict_critical():
    assert _verdict(150.0, 10.0) == "critical"


def test_file_frequency_present():
    entries = [
        _entry("2026-01-01T00:00:00+00:00", "t", 100, ["a.py", "b.py"]),
        _entry("2026-01-02T00:00:00+00:00", "t", 100, ["a.py"]),
    ]
    assert _file_frequency(entries, "a.py") == 1.0
    assert _file_frequency(entries, "b.py") == 0.5


def test_file_frequency_absent():
    entries = [_entry("2026-01-01T00:00:00+00:00", "t", 100, ["a.py"])]
    assert _file_frequency(entries, "missing.py") == 0.0


def test_file_frequency_empty_entries():
    assert _file_frequency([], "a.py") == 0.0


def test_compute_contributors_added():
    baseline = [_entry("2026-01-01T00:00:00+00:00", "t", 100, ["old.py"])]
    recent = [_entry("2026-01-02T00:00:00+00:00", "t", 200, ["old.py", "new.py"])]
    contributors = _compute_contributors(baseline, recent)
    statuses = {c.file: c.status for c in contributors}
    assert statuses.get("new.py") == "added"


def test_compute_contributors_removed():
    baseline = [_entry("2026-01-01T00:00:00+00:00", "t", 200, ["old.py", "gone.py"])]
    recent = [_entry("2026-01-02T00:00:00+00:00", "t", 100, ["old.py"])]
    contributors = _compute_contributors(baseline, recent)
    statuses = {c.file: c.status for c in contributors}
    assert statuses.get("gone.py") == "removed"


def test_compute_contributors_respects_top_n():
    files = [f"file_{i}.py" for i in range(20)]
    baseline: list[RunHistoryEntry] = []
    recent = [_entry("2026-01-02T00:00:00+00:00", "t", 1000, files)]
    contributors = _compute_contributors(baseline, recent, top_n=5)
    assert len(contributors) <= 5


# ---------------------------------------------------------------------------
# Integration tests: run_drift
# ---------------------------------------------------------------------------


def test_run_drift_basic_growth(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "add caching", 1000, ["a.py", "b.py"]),
        _entry("2026-01-02T00:00:00+00:00", "add caching", 1500, ["a.py", "b.py", "c.py"]),
        _entry("2026-01-03T00:00:00+00:00", "add caching", 2000, ["a.py", "b.py", "c.py", "d.py"]),
    ])
    report = run_drift(repo)

    assert report["command"] == "drift"
    assert report["entries_analyzed"] == 3
    drift = report["drift"]
    assert drift["token_drift_pct"] == pytest.approx(100.0)  # 1000 -> 2000
    assert drift["alert"] is True
    assert drift["verdict"] in ("significant", "critical")


def test_run_drift_no_change(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "stable task", 1000, ["a.py"]),
        _entry("2026-01-02T00:00:00+00:00", "stable task", 1000, ["a.py"]),
        _entry("2026-01-03T00:00:00+00:00", "stable task", 1000, ["a.py"]),
    ])
    report = run_drift(repo)

    drift = report["drift"]
    assert drift["token_drift_pct"] == pytest.approx(0.0)
    assert drift["alert"] is False
    assert drift["verdict"] == "none"


def test_run_drift_task_filter(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "add caching", 1000, ["a.py"]),
        _entry("2026-01-02T00:00:00+00:00", "add auth", 5000, ["x.py", "y.py", "z.py"]),
        _entry("2026-01-03T00:00:00+00:00", "add caching", 1010, ["a.py"]),
    ])
    report = run_drift(repo, task="caching")

    assert report["entries_analyzed"] == 2
    assert report["baseline"]["task"] == "add caching"
    assert report["current"]["task"] == "add caching"


def test_run_drift_trend_length(tmp_path: Path):
    entries = [
        _entry(f"2026-01-{i:02d}T00:00:00+00:00", "t", 1000 + i * 10, ["a.py"])
        for i in range(1, 8)
    ]
    repo = _make_repo(tmp_path, entries)
    report = run_drift(repo, window=5)

    assert len(report["trend"]) == 5
    assert report["entries_analyzed"] == 5


def test_run_drift_top_contributors_present(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "t", 1000, ["old.py"]),
        _entry("2026-01-02T00:00:00+00:00", "t", 1500, ["old.py", "new1.py"]),
        _entry("2026-01-03T00:00:00+00:00", "t", 2000, ["old.py", "new1.py", "new2.py"]),
    ])
    report = run_drift(repo)

    files = {c["file"] for c in report["top_contributors"]}
    assert "new2.py" in files or "new1.py" in files


def test_run_drift_requires_min_two_entries(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "t", 1000, ["a.py"]),
    ])
    with pytest.raises(ValueError, match="2 history entries"):
        run_drift(repo)


def test_run_drift_no_history(tmp_path: Path):
    repo = tmp_path / "empty_repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="No history entries found"):
        run_drift(repo)


def test_run_drift_window_clamps(tmp_path: Path):
    entries = [
        _entry(f"2026-01-{i:02d}T00:00:00+00:00", "t", 1000, ["a.py"])
        for i in range(1, 11)
    ]
    repo = _make_repo(tmp_path, entries)
    report = run_drift(repo, window=3)
    assert report["entries_analyzed"] == 3


def test_run_drift_custom_threshold_suppresses_alert(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "t", 1000, ["a.py"]),
        _entry("2026-01-02T00:00:00+00:00", "t", 1050, ["a.py"]),  # +5%
    ])
    # With threshold=10 (default), 5% is no alert
    report = run_drift(repo, threshold_pct=10.0)
    assert report["drift"]["alert"] is False

    # With threshold=3, 5% is an alert
    report2 = run_drift(repo, threshold_pct=3.0)
    assert report2["drift"]["alert"] is True


def test_run_drift_invalid_window(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    with pytest.raises(ValueError, match="window"):
        run_drift(repo, window=1)


# ---------------------------------------------------------------------------
# Render tests
# ---------------------------------------------------------------------------


def test_render_drift_markdown_structure(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "add caching", 1000, ["a.py"]),
        _entry("2026-01-02T00:00:00+00:00", "add caching", 1300, ["a.py", "b.py"]),
    ])
    report = run_drift(repo)
    md = render_drift_markdown(report)

    assert "# ContextBudget Drift Report" in md
    assert "## Verdict" in md
    assert "## Trend" in md
    assert "+30.0%" in md  # token drift


def test_render_drift_markdown_alert_badge(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "t", 1000, ["a.py"]),
        _entry("2026-01-02T00:00:00+00:00", "t", 2500, ["a.py", "b.py"]),
    ])
    report = run_drift(repo)
    md = render_drift_markdown(report)
    assert "ALERT" in md


def test_render_drift_markdown_no_alert(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "t", 1000, ["a.py"]),
        _entry("2026-01-02T00:00:00+00:00", "t", 1000, ["a.py"]),
    ])
    report = run_drift(repo)
    md = render_drift_markdown(report)
    assert "ALERT" not in md
    assert "NONE" in md


# ---------------------------------------------------------------------------
# Engine API test
# ---------------------------------------------------------------------------


def test_engine_drift(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "t", 1000, ["a.py"]),
        _entry("2026-01-02T00:00:00+00:00", "t", 1200, ["a.py", "b.py"]),
    ])
    engine = ContextBudgetEngine()
    report = engine.drift(repo=repo)

    assert report["command"] == "drift"
    assert "drift" in report
    assert "trend" in report
    assert "top_contributors" in report


def test_engine_drift_with_task_filter(tmp_path: Path):
    repo = _make_repo(tmp_path, [
        _entry("2026-01-01T00:00:00+00:00", "add auth", 5000, ["x.py"]),
        _entry("2026-01-02T00:00:00+00:00", "add caching", 1000, ["a.py"]),
        _entry("2026-01-03T00:00:00+00:00", "add caching", 1200, ["a.py", "b.py"]),
    ])
    engine = ContextBudgetEngine()
    report = engine.drift(repo=repo, task="caching")

    assert report["entries_analyzed"] == 2
    assert report["task_filter"] == "caching"
