from __future__ import annotations

"""Tests for the context architecture advisor (core logic and CLI)."""

import json
from pathlib import Path

import pytest

from contextbudget.cli import main
from contextbudget.core.advisor import (
    SUGGESTION_EXTRACT_MODULE,
    SUGGESTION_REDUCE_DEPENDENCIES,
    SUGGESTION_SPLIT_FILE,
    AdviceSuggestion,
    AdviseReport,
    _compute_pack_frequency,
    _load_pack_artifacts,
    run_advise,
)
from contextbudget.config import load_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal repo with a few Python files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "core.py", "def helper():\n    pass\n")
    _write(repo / "a.py", "from core import helper\n")
    _write(repo / "b.py", "from core import helper\n")
    return repo


def _make_large_file_repo(tmp_path: Path, *, threshold: int = 10) -> Path:
    """Create a repo where one file clearly exceeds the token threshold."""
    repo = tmp_path / "large_repo"
    repo.mkdir()
    # ~200 words → well above threshold=10 tokens
    big_content = "word " * 200
    _write(repo / "big.py", big_content)
    _write(repo / "small.py", "x = 1\n")
    return repo


def _make_fanin_repo(tmp_path: Path, *, fanin: int = 3) -> Path:
    """Create a repo where hub.py is imported by `fanin` files."""
    repo = tmp_path / "fanin_repo"
    repo.mkdir()
    _write(repo / "hub.py", "HUB = True\n")
    for i in range(fanin):
        _write(repo / f"consumer_{i}.py", "from hub import HUB\n")
    return repo


def _make_pack_artifact(files_included: list[str]) -> dict:
    """Build a minimal pack run.json dict with the given files in compressed_context."""
    return {
        "command": "pack",
        "compressed_context": [{"path": p, "text": "x"} for p in files_included],
    }


# ---------------------------------------------------------------------------
# _load_pack_artifacts
# ---------------------------------------------------------------------------


def test_load_pack_artifacts_reads_valid_json(tmp_path: Path) -> None:
    artifact = _make_pack_artifact(["src/a.py"])
    p = tmp_path / "run.json"
    p.write_text(json.dumps(artifact), encoding="utf-8")
    result = _load_pack_artifacts([tmp_path])
    assert len(result) == 1
    assert result[0]["command"] == "pack"


def test_load_pack_artifacts_skips_non_pack_commands(tmp_path: Path) -> None:
    bad = {"command": "plan", "compressed_context": []}
    (tmp_path / "plan.json").write_text(json.dumps(bad), encoding="utf-8")
    result = _load_pack_artifacts([tmp_path])
    assert result == []


def test_load_pack_artifacts_skips_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    result = _load_pack_artifacts([tmp_path])
    assert result == []


def test_load_pack_artifacts_deduplicates(tmp_path: Path) -> None:
    artifact = _make_pack_artifact(["a.py"])
    p = tmp_path / "run.json"
    p.write_text(json.dumps(artifact), encoding="utf-8")
    # Pass the same path twice - should only load once.
    result = _load_pack_artifacts([p, p])
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _compute_pack_frequency
# ---------------------------------------------------------------------------


def test_compute_pack_frequency_returns_empty_without_artifacts(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    cfg = load_config(repo)
    from contextbudget.scanners.repository import scan_repository
    files = scan_repository(repo, max_file_size_bytes=cfg.scan.max_file_size_bytes)
    result = _compute_pack_frequency(files, [])
    assert result == {}


def test_compute_pack_frequency_counts_inclusion(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    cfg = load_config(repo)
    from contextbudget.scanners.repository import scan_repository
    files = scan_repository(repo, max_file_size_bytes=cfg.scan.max_file_size_bytes)

    artifacts = [
        _make_pack_artifact(["core.py", "a.py"]),
        _make_pack_artifact(["core.py"]),
    ]
    freq = _compute_pack_frequency(files, artifacts)
    # core.py included in both → rate = 1.0
    assert freq.get("core.py", 0.0) == pytest.approx(1.0)
    # a.py included in one of two → rate = 0.5
    assert freq.get("a.py", 0.0) == pytest.approx(0.5)
    # b.py never included → rate = 0.0
    assert freq.get("b.py", 0.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# run_advise - core logic
# ---------------------------------------------------------------------------


def test_run_advise_returns_advise_report(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    cfg = load_config(repo)
    report = run_advise(repo, config=cfg)
    assert isinstance(report, AdviseReport)
    assert report.command == "advise"
    assert report.scanned_files > 0


def test_run_advise_no_suggestions_on_clean_repo(tmp_path: Path) -> None:
    """A repo of tiny files with low fan-in should produce no suggestions."""
    repo = tmp_path / "clean"
    repo.mkdir()
    _write(repo / "a.py", "x = 1\n")
    _write(repo / "b.py", "y = 2\n")
    cfg = load_config(repo)
    report = run_advise(
        repo,
        config=cfg,
        large_file_tokens=9999,   # nothing is "large"
        high_fanin=9999,
        high_fanout=9999,
    )
    assert report.suggestions == []
    assert report.summary["total_suggestions"] == 0


def test_run_advise_detects_large_file(tmp_path: Path) -> None:
    repo = _make_large_file_repo(tmp_path, threshold=10)
    cfg = load_config(repo)
    report = run_advise(repo, config=cfg, large_file_tokens=10, high_fanin=999, high_fanout=999)
    paths = [s.path for s in report.suggestions]
    assert any("big.py" in p for p in paths)
    big = next(s for s in report.suggestions if "big.py" in s.path)
    assert big.suggestion in (SUGGESTION_SPLIT_FILE, SUGGESTION_EXTRACT_MODULE)


def test_run_advise_detects_high_fanin(tmp_path: Path) -> None:
    repo = _make_fanin_repo(tmp_path, fanin=3)
    cfg = load_config(repo)
    report = run_advise(repo, config=cfg, large_file_tokens=99999, high_fanin=3, high_fanout=999)
    paths = [s.path for s in report.suggestions]
    assert any("hub.py" in p for p in paths)
    hub = next(s for s in report.suggestions if "hub.py" in s.path)
    assert hub.suggestion == SUGGESTION_EXTRACT_MODULE


def test_run_advise_suggestions_ranked_by_token_impact(tmp_path: Path) -> None:
    repo = _make_large_file_repo(tmp_path, threshold=5)
    # Add a second large file that is smaller
    _write(repo / "medium.py", "word " * 30)
    cfg = load_config(repo)
    report = run_advise(repo, config=cfg, large_file_tokens=5, high_fanin=999, high_fanout=999)
    if len(report.suggestions) >= 2:
        impacts = [s.estimated_token_impact for s in report.suggestions]
        assert impacts == sorted(impacts, reverse=True)


def test_run_advise_one_suggestion_per_file(tmp_path: Path) -> None:
    """Even if a file triggers multiple signals it should appear only once."""
    repo = _make_fanin_repo(tmp_path, fanin=3)
    # Also make hub.py large
    _write(repo / "hub.py", "x = 1\n" * 100)
    cfg = load_config(repo)
    report = run_advise(repo, config=cfg, large_file_tokens=5, high_fanin=3, high_fanout=999)
    hub_suggestions = [s for s in report.suggestions if "hub.py" in s.path]
    assert len(hub_suggestions) == 1


def test_run_advise_uses_frequency_signals(tmp_path: Path) -> None:
    repo = _make_large_file_repo(tmp_path, threshold=5)
    cfg = load_config(repo)
    # Build a pack artifact that includes big.py 100% of the time
    artifacts_dir = tmp_path / "runs"
    artifacts_dir.mkdir()
    artifact = _make_pack_artifact(["big.py"])
    (artifacts_dir / "run1.json").write_text(json.dumps(artifact), encoding="utf-8")

    report_with = run_advise(
        repo, config=cfg, large_file_tokens=5, high_fanin=999, high_fanout=999,
        history=[artifacts_dir],
    )
    report_without = run_advise(
        repo, config=cfg, large_file_tokens=5, high_fanin=999, high_fanout=999,
    )
    # Frequency signal should increase estimated impact
    big_with = next((s for s in report_with.suggestions if "big.py" in s.path), None)
    big_without = next((s for s in report_without.suggestions if "big.py" in s.path), None)
    assert big_with is not None
    assert big_without is not None
    assert big_with.estimated_token_impact >= big_without.estimated_token_impact


def test_run_advise_top_suggestions_limit(tmp_path: Path) -> None:
    repo = tmp_path / "many"
    repo.mkdir()
    # Create 20 large files
    for i in range(20):
        _write(repo / f"mod_{i}.py", "word " * 50)
    cfg = load_config(repo)
    report = run_advise(repo, config=cfg, large_file_tokens=5, high_fanin=999, high_fanout=999, top_suggestions=5)
    assert len(report.suggestions) <= 5


def test_run_advise_summary_counts_match(tmp_path: Path) -> None:
    repo = _make_large_file_repo(tmp_path, threshold=5)
    cfg = load_config(repo)
    report = run_advise(repo, config=cfg, large_file_tokens=5, high_fanin=999, high_fanout=999)
    total = report.summary["total_suggestions"]
    by_type = (
        report.summary["split_file"]
        + report.summary["extract_module"]
        + report.summary["reduce_dependencies"]
    )
    assert total == by_type == len(report.suggestions)


def test_run_advise_runs_analyzed_zero_without_history(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    cfg = load_config(repo)
    report = run_advise(repo, config=cfg)
    assert report.runs_analyzed == 0


def test_run_advise_runs_analyzed_with_history(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    cfg = load_config(repo)
    artifacts_dir = tmp_path / "runs"
    artifacts_dir.mkdir()
    for i in range(3):
        a = _make_pack_artifact(["core.py"])
        (artifacts_dir / f"run{i}.json").write_text(json.dumps(a), encoding="utf-8")
    report = run_advise(repo, config=cfg, history=[artifacts_dir])
    assert report.runs_analyzed == 3


# ---------------------------------------------------------------------------
# CLI - contextbudget advise
# ---------------------------------------------------------------------------


def test_cli_advise_writes_json_and_markdown(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "advise", "--repo", str(repo), "--out-prefix", "adv"],
    )
    assert main() == 0
    assert (tmp_path / "adv.json").exists()
    assert (tmp_path / "adv.md").exists()


def test_cli_advise_json_has_required_keys(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "advise", "--repo", str(repo), "--out-prefix", "adv2"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "adv2.json").read_text(encoding="utf-8"))
    for key in ("command", "generated_at", "repo", "scanned_files", "runs_analyzed", "suggestions", "summary"):
        assert key in data, f"Missing key: {key}"
    assert data["command"] == "advise"


def test_cli_advise_markdown_contains_header(tmp_path: Path, monkeypatch) -> None:
    repo = _make_large_file_repo(tmp_path, threshold=5)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget", "advise",
            "--repo", str(repo),
            "--large-file-tokens", "5",
            "--out-prefix", "adv3",
        ],
    )
    assert main() == 0
    md = (tmp_path / "adv3.md").read_text(encoding="utf-8")
    assert "# ContextBudget Architecture Advice" in md


def test_cli_advise_large_file_tokens_flag(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _make_large_file_repo(tmp_path, threshold=5)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget", "advise",
            "--repo", str(repo),
            "--large-file-tokens", "5",
            "--high-fanin", "9999",
            "--high-fanout", "9999",
            "--out-prefix", "adv-large",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "adv-large.json").read_text(encoding="utf-8"))
    assert data["summary"]["total_suggestions"] >= 1
    paths = [s["path"] for s in data["suggestions"]]
    assert any("big.py" in p for p in paths)


def test_cli_advise_high_fanin_flag(tmp_path: Path, monkeypatch) -> None:
    repo = _make_fanin_repo(tmp_path, fanin=3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget", "advise",
            "--repo", str(repo),
            "--large-file-tokens", "99999",
            "--high-fanin", "3",
            "--high-fanout", "9999",
            "--out-prefix", "adv-fanin",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "adv-fanin.json").read_text(encoding="utf-8"))
    paths = [s["path"] for s in data["suggestions"]]
    assert any("hub.py" in p for p in paths)


def test_cli_advise_top_limits_output(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "many2"
    repo.mkdir()
    for i in range(15):
        _write(repo / f"big_{i}.py", "word " * 60)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget", "advise",
            "--repo", str(repo),
            "--large-file-tokens", "5",
            "--top", "3",
            "--out-prefix", "adv-top",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "adv-top.json").read_text(encoding="utf-8"))
    assert len(data["suggestions"]) <= 3


def test_cli_advise_with_history(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    artifact = _make_pack_artifact(["core.py"])
    (runs_dir / "run.json").write_text(json.dumps(artifact), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget", "advise",
            "--repo", str(repo),
            "--history", str(runs_dir),
            "--out-prefix", "adv-hist",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "adv-hist.json").read_text(encoding="utf-8"))
    assert data["runs_analyzed"] == 1


def test_cli_advise_no_suggestions_prints_clean(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "clean2"
    repo.mkdir()
    _write(repo / "tiny.py", "x = 1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget", "advise",
            "--repo", str(repo),
            "--large-file-tokens", "99999",
            "--high-fanin", "9999",
            "--high-fanout", "9999",
            "--out-prefix", "adv-clean",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "adv-clean.json").read_text(encoding="utf-8"))
    assert data["summary"]["total_suggestions"] == 0
    md = (tmp_path / "adv-clean.md").read_text(encoding="utf-8")
    assert "No suggestions" in md
