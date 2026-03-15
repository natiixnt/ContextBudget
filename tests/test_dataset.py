from __future__ import annotations

"""Tests for the dataset builder (core logic, engine, and CLI)."""

import json
from pathlib import Path
from typing import Any

import pytest

from contextbudget.cli import main
from contextbudget.core.dataset import (
    DatasetTask,
    dataset_as_dict,
    load_tasks_toml,
    run_dataset,
)
from contextbudget.core.render import render_dataset_markdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "core.py", "def helper():\n    pass\n")
    _write(repo / "a.py", "from core import helper\n")
    return repo


def _tasks_toml(tasks: list[dict]) -> str:
    lines = []
    for t in tasks:
        lines.append("[[tasks]]")
        if "name" in t:
            lines.append(f'name = "{t["name"]}"')
        lines.append(f'description = "{t["description"]}"')
        lines.append("")
    return "\n".join(lines)


def _stub_benchmark(results: list[dict]):
    """Return a stub benchmark function that cycles through pre-built results."""
    calls = iter(results)

    def _fn(*, task: str, repo: Path, max_tokens: Any, top_files: Any) -> dict:
        return next(calls)

    return _fn


def _make_benchmark_result(baseline: int, optimized: int) -> dict:
    return {
        "command": "benchmark",
        "task": "stub",
        "repo": "/repo",
        "baseline_full_context_tokens": baseline,
        "strategies": [
            {
                "strategy": "naive_full_context",
                "estimated_input_tokens": baseline,
                "estimated_saved_tokens": 0,
            },
            {
                "strategy": "compressed_pack",
                "estimated_input_tokens": optimized,
                "estimated_saved_tokens": max(0, baseline - optimized),
            },
        ],
    }


# ---------------------------------------------------------------------------
# load_tasks_toml
# ---------------------------------------------------------------------------


def test_load_tasks_toml_basic(tmp_path: Path) -> None:
    p = tmp_path / "tasks.toml"
    p.write_text(_tasks_toml([
        {"name": "Auth", "description": "Add JWT auth"},
        {"description": "Refactor DB layer"},
    ]), encoding="utf-8")
    tasks = load_tasks_toml(p)
    assert len(tasks) == 2
    assert tasks[0].name == "Auth"
    assert tasks[0].description == "Add JWT auth"
    assert tasks[1].name == ""
    assert tasks[1].description == "Refactor DB layer"


def test_load_tasks_toml_requires_description(tmp_path: Path) -> None:
    p = tmp_path / "tasks.toml"
    p.write_text('[[tasks]]\nname = "No description"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="description"):
        load_tasks_toml(p)


def test_load_tasks_toml_requires_at_least_one_task(tmp_path: Path) -> None:
    p = tmp_path / "tasks.toml"
    p.write_text("[settings]\nfoo = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No \\[\\[tasks\\]\\]"):
        load_tasks_toml(p)


def test_load_tasks_toml_rejects_empty_description(tmp_path: Path) -> None:
    p = tmp_path / "tasks.toml"
    p.write_text('[[tasks]]\ndescription = "   "\n', encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        load_tasks_toml(p)


def test_load_tasks_toml_multiple(tmp_path: Path) -> None:
    tasks = [{"description": f"Task {i}"} for i in range(5)]
    p = tmp_path / "tasks.toml"
    p.write_text(_tasks_toml(tasks), encoding="utf-8")
    loaded = load_tasks_toml(p)
    assert len(loaded) == 5
    assert all(isinstance(t, DatasetTask) for t in loaded)


# ---------------------------------------------------------------------------
# run_dataset — core logic
# ---------------------------------------------------------------------------


def test_run_dataset_basic(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="Task A"), DatasetTask(description="Task B")]
    stub = _stub_benchmark([
        _make_benchmark_result(1000, 400),
        _make_benchmark_result(2000, 600),
    ])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    assert report.command == "dataset"
    assert report.task_count == 2
    assert len(report.entries) == 2


def test_run_dataset_reduction_pct_computed(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="Task A")]
    stub = _stub_benchmark([_make_benchmark_result(1000, 250)])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    entry = report.entries[0]
    assert entry.baseline_tokens == 1000
    assert entry.optimized_tokens == 250
    assert entry.reduction_pct == pytest.approx(75.0)


def test_run_dataset_zero_reduction(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="Task A")]
    stub = _stub_benchmark([_make_benchmark_result(1000, 1000)])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    assert report.entries[0].reduction_pct == pytest.approx(0.0)


def test_run_dataset_aggregate_totals(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A"), DatasetTask(description="B")]
    stub = _stub_benchmark([
        _make_benchmark_result(1000, 200),
        _make_benchmark_result(2000, 800),
    ])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    assert report.aggregate["total_baseline_tokens"] == 3000
    assert report.aggregate["total_optimized_tokens"] == 1000


def test_run_dataset_aggregate_avg(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A"), DatasetTask(description="B")]
    stub = _stub_benchmark([
        _make_benchmark_result(1000, 500),   # 50% reduction
        _make_benchmark_result(1000, 250),   # 75% reduction
    ])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    assert report.aggregate["avg_reduction_pct"] == pytest.approx(62.5)
    assert report.aggregate["avg_baseline_tokens"] == pytest.approx(1000.0)
    assert report.aggregate["avg_optimized_tokens"] == pytest.approx(375.0)


def test_run_dataset_benchmark_stored_per_entry(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A")]
    bm = _make_benchmark_result(500, 100)
    stub = _stub_benchmark([bm])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    assert report.entries[0].benchmark == bm


def test_run_dataset_task_name_propagated(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="Do something", name="My Task")]
    stub = _stub_benchmark([_make_benchmark_result(100, 50)])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    assert report.entries[0].task_name == "My Task"


def test_run_dataset_uses_compressed_pack_as_optimized(tmp_path: Path) -> None:
    """Optimized tokens should come from compressed_pack strategy, not top_k."""
    tasks = [DatasetTask(description="A")]
    bm = {
        "command": "benchmark",
        "baseline_full_context_tokens": 1000,
        "strategies": [
            {"strategy": "top_k_selection", "estimated_input_tokens": 800},
            {"strategy": "compressed_pack", "estimated_input_tokens": 300},
        ],
    }
    stub = _stub_benchmark([bm])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    assert report.entries[0].optimized_tokens == 300


def test_run_dataset_fallback_to_baseline_when_no_compressed_pack(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A")]
    bm = {"command": "benchmark", "baseline_full_context_tokens": 500, "strategies": []}
    stub = _stub_benchmark([bm])
    report = run_dataset(tasks, tmp_path / "repo", run_benchmark_fn=stub)
    assert report.entries[0].optimized_tokens == 500
    assert report.entries[0].reduction_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# dataset_as_dict
# ---------------------------------------------------------------------------


def test_dataset_as_dict_structure(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A"), DatasetTask(description="B")]
    stub = _stub_benchmark([
        _make_benchmark_result(1000, 400),
        _make_benchmark_result(500, 100),
    ])
    report = run_dataset(tasks, tmp_path, run_benchmark_fn=stub)
    d = dataset_as_dict(report)

    assert d["command"] == "dataset"
    assert "generated_at" in d
    assert d["task_count"] == 2
    assert "aggregate" in d
    assert isinstance(d["entries"], list)
    assert len(d["entries"]) == 2

    for entry in d["entries"]:
        for key in ("task", "task_name", "baseline_tokens", "optimized_tokens", "reduction_pct", "benchmark"):
            assert key in entry, f"Missing key '{key}' in entry"


def test_dataset_as_dict_is_json_serializable(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A")]
    stub = _stub_benchmark([_make_benchmark_result(200, 80)])
    report = run_dataset(tasks, tmp_path, run_benchmark_fn=stub)
    d = dataset_as_dict(report)
    # Should not raise
    json.dumps(d)


# ---------------------------------------------------------------------------
# render_dataset_markdown
# ---------------------------------------------------------------------------


def test_render_dataset_markdown_header(tmp_path: Path) -> None:
    data = {
        "command": "dataset",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "repo": "/repo",
        "task_count": 0,
        "aggregate": {
            "total_baseline_tokens": 0,
            "total_optimized_tokens": 0,
            "avg_baseline_tokens": 0,
            "avg_optimized_tokens": 0,
            "avg_reduction_pct": 0,
        },
        "entries": [],
    }
    md = render_dataset_markdown(data)
    assert "# ContextBudget Dataset Report" in md


def test_render_dataset_markdown_includes_entries(tmp_path: Path) -> None:
    data = {
        "command": "dataset",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "repo": "/repo",
        "task_count": 2,
        "aggregate": {
            "total_baseline_tokens": 1500,
            "total_optimized_tokens": 500,
            "avg_baseline_tokens": 750,
            "avg_optimized_tokens": 250,
            "avg_reduction_pct": 66.7,
        },
        "entries": [
            {"task": "Add auth", "task_name": "Auth", "baseline_tokens": 1000, "optimized_tokens": 300, "reduction_pct": 70.0},
            {"task": "Refactor DB", "task_name": "", "baseline_tokens": 500, "optimized_tokens": 200, "reduction_pct": 60.0},
        ],
    }
    md = render_dataset_markdown(data)
    assert "Auth" in md
    assert "Refactor DB" in md
    assert "66.7%" in md


# ---------------------------------------------------------------------------
# CLI — contextbudget dataset (integration)
# ---------------------------------------------------------------------------


def _make_flask_repo(tmp_path: Path) -> Path:
    """Create a small Python repo to benchmark against."""
    repo = tmp_path / "flask_app"
    repo.mkdir()
    _write(repo / "app.py", "from flask import Flask\napp = Flask(__name__)\n\n@app.route('/')\ndef index():\n    return 'hello'\n")
    _write(repo / "models.py", "class User:\n    pass\n\nclass Post:\n    pass\n")
    _write(repo / "utils.py", "def validate(data):\n    return bool(data)\n")
    return repo


def test_cli_dataset_writes_json_and_markdown(tmp_path: Path, monkeypatch) -> None:
    repo = _make_flask_repo(tmp_path)
    toml_path = tmp_path / "tasks.toml"
    toml_path.write_text(
        '[[tasks]]\nname = "Auth"\ndescription = "Add JWT authentication"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "dataset", str(toml_path), "--repo", str(repo), "--out-prefix", "ds"],
    )
    assert main() == 0
    assert (tmp_path / "ds.json").exists()
    assert (tmp_path / "ds.md").exists()


def test_cli_dataset_json_has_required_keys(tmp_path: Path, monkeypatch) -> None:
    repo = _make_flask_repo(tmp_path)
    toml_path = tmp_path / "tasks.toml"
    toml_path.write_text(
        '[[tasks]]\ndescription = "Refactor the DB layer"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "dataset", str(toml_path), "--repo", str(repo), "--out-prefix", "ds2"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "ds2.json").read_text(encoding="utf-8"))
    for key in ("command", "generated_at", "repo", "task_count", "aggregate", "entries"):
        assert key in data, f"Missing key: {key}"
    assert data["command"] == "dataset"
    assert data["task_count"] == 1
    assert len(data["entries"]) == 1


def test_cli_dataset_entry_has_reduction_metrics(tmp_path: Path, monkeypatch) -> None:
    repo = _make_flask_repo(tmp_path)
    toml_path = tmp_path / "tasks.toml"
    toml_path.write_text(
        '[[tasks]]\ndescription = "Add caching"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "dataset", str(toml_path), "--repo", str(repo), "--out-prefix", "ds3"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "ds3.json").read_text(encoding="utf-8"))
    entry = data["entries"][0]
    assert "baseline_tokens" in entry
    assert "optimized_tokens" in entry
    assert "reduction_pct" in entry
    assert isinstance(entry["reduction_pct"], float)
    assert entry["baseline_tokens"] >= 0
    assert entry["optimized_tokens"] >= 0


def test_cli_dataset_multiple_tasks(tmp_path: Path, monkeypatch) -> None:
    repo = _make_flask_repo(tmp_path)
    toml_path = tmp_path / "tasks.toml"
    toml_path.write_text(
        '[[tasks]]\ndescription = "Add auth"\n\n[[tasks]]\ndescription = "Add caching"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "dataset", str(toml_path), "--repo", str(repo), "--out-prefix", "ds4"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "ds4.json").read_text(encoding="utf-8"))
    assert data["task_count"] == 2
    assert len(data["entries"]) == 2


def test_cli_dataset_markdown_contains_header(tmp_path: Path, monkeypatch) -> None:
    repo = _make_flask_repo(tmp_path)
    toml_path = tmp_path / "tasks.toml"
    toml_path.write_text('[[tasks]]\ndescription = "Task"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "dataset", str(toml_path), "--repo", str(repo), "--out-prefix", "ds5"],
    )
    assert main() == 0
    md = (tmp_path / "ds5.md").read_text(encoding="utf-8")
    assert "# ContextBudget Dataset Report" in md


def test_cli_dataset_aggregate_in_json(tmp_path: Path, monkeypatch) -> None:
    repo = _make_flask_repo(tmp_path)
    toml_path = tmp_path / "tasks.toml"
    toml_path.write_text('[[tasks]]\ndescription = "Task A"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "dataset", str(toml_path), "--repo", str(repo), "--out-prefix", "ds6"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "ds6.json").read_text(encoding="utf-8"))
    agg = data["aggregate"]
    for key in ("total_baseline_tokens", "total_optimized_tokens", "avg_baseline_tokens", "avg_optimized_tokens", "avg_reduction_pct"):
        assert key in agg, f"Missing aggregate key: {key}"
