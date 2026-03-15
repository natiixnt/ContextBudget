from __future__ import annotations

"""Tests for the Context Dataset Builder (core logic, engine, and CLI)."""

import json
from pathlib import Path
from typing import Any

import pytest

from redcon.cli import main
from redcon.core.context_dataset_builder import (
    BUILTIN_TASKS,
    ContextDatasetBuilderConfig,
    build_context_dataset,
    context_dataset_as_dict,
    load_extra_tasks_toml,
)
from redcon.core.dataset import DatasetTask
from redcon.core.render import render_context_dataset_markdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "app.py", "from flask import Flask\napp = Flask(__name__)\n")
    _write(repo / "models.py", "class User:\n    pass\n")
    _write(repo / "utils.py", "def validate(data):\n    return bool(data)\n")
    return repo


def _stub_benchmark(results: list[dict]) -> Any:
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
            {"strategy": "naive_full_context", "estimated_input_tokens": baseline, "estimated_saved_tokens": 0},
            {"strategy": "compressed_pack", "estimated_input_tokens": optimized, "estimated_saved_tokens": max(0, baseline - optimized)},
        ],
    }


def _tasks_toml(tasks: list[dict]) -> str:
    lines = []
    for t in tasks:
        lines.append("[[tasks]]")
        if "name" in t:
            lines.append(f'name = "{t["name"]}"')
        lines.append(f'description = "{t["description"]}"')
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# BUILTIN_TASKS
# ---------------------------------------------------------------------------


def test_builtin_tasks_are_defined() -> None:
    assert len(BUILTIN_TASKS) >= 3


def test_builtin_tasks_have_names_and_descriptions() -> None:
    for task in BUILTIN_TASKS:
        assert isinstance(task, DatasetTask)
        assert task.name.strip()
        assert task.description.strip()


def test_builtin_tasks_include_expected_benchmarks() -> None:
    descriptions = [t.description.lower() for t in BUILTIN_TASKS]
    assert any("cach" in d for d in descriptions), "Expected an 'add caching' task"
    assert any("auth" in d for d in descriptions), "Expected an 'add authentication' task"
    assert any("refactor" in d for d in descriptions), "Expected a 'refactor' task"


# ---------------------------------------------------------------------------
# ContextDatasetBuilderConfig
# ---------------------------------------------------------------------------


def test_config_defaults_to_builtin_tasks() -> None:
    cfg = ContextDatasetBuilderConfig()
    assert cfg.tasks == BUILTIN_TASKS
    assert cfg.max_tokens is None
    assert cfg.top_files is None


def test_config_accepts_custom_tasks() -> None:
    custom = [DatasetTask(description="Custom task")]
    cfg = ContextDatasetBuilderConfig(tasks=custom)
    assert cfg.tasks == custom


# ---------------------------------------------------------------------------
# build_context_dataset
# ---------------------------------------------------------------------------


def test_build_context_dataset_uses_builtin_by_default(tmp_path: Path) -> None:
    stub = _stub_benchmark([_make_benchmark_result(1000, 300)] * len(BUILTIN_TASKS))
    report = build_context_dataset(tmp_path / "repo", run_benchmark_fn=stub)
    assert report.task_count == len(BUILTIN_TASKS)


def test_build_context_dataset_uses_custom_tasks(tmp_path: Path) -> None:
    custom = [DatasetTask(description="A"), DatasetTask(description="B")]
    stub = _stub_benchmark([_make_benchmark_result(500, 100), _make_benchmark_result(800, 200)])
    report = build_context_dataset(tmp_path / "repo", run_benchmark_fn=stub, tasks=custom)
    assert report.task_count == 2


def test_build_context_dataset_reduction_pct(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="Task A")]
    stub = _stub_benchmark([_make_benchmark_result(1000, 250)])
    report = build_context_dataset(tmp_path / "repo", run_benchmark_fn=stub, tasks=tasks)
    assert report.entries[0].reduction_pct == pytest.approx(75.0)


def test_build_context_dataset_aggregate(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A"), DatasetTask(description="B")]
    stub = _stub_benchmark([_make_benchmark_result(1000, 200), _make_benchmark_result(2000, 600)])
    report = build_context_dataset(tmp_path / "repo", run_benchmark_fn=stub, tasks=tasks)
    assert report.aggregate["total_baseline_tokens"] == 3000
    assert report.aggregate["total_optimized_tokens"] == 800


# ---------------------------------------------------------------------------
# context_dataset_as_dict
# ---------------------------------------------------------------------------


def test_context_dataset_as_dict_has_required_keys(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A")]
    stub = _stub_benchmark([_make_benchmark_result(100, 50)])
    report = build_context_dataset(tmp_path, run_benchmark_fn=stub, tasks=tasks)
    d = context_dataset_as_dict(report, builtin_task_count=5, extra_task_count=1)

    for key in ("command", "generated_at", "repo", "task_count", "aggregate", "entries",
                "builtin_task_count", "extra_task_count"):
        assert key in d, f"Missing key: {key}"
    assert d["builtin_task_count"] == 5
    assert d["extra_task_count"] == 1


def test_context_dataset_as_dict_is_json_serializable(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A")]
    stub = _stub_benchmark([_make_benchmark_result(200, 80)])
    report = build_context_dataset(tmp_path, run_benchmark_fn=stub, tasks=tasks)
    d = context_dataset_as_dict(report)
    json.dumps(d)


def test_context_dataset_as_dict_defaults_counts_to_zero(tmp_path: Path) -> None:
    tasks = [DatasetTask(description="A")]
    stub = _stub_benchmark([_make_benchmark_result(100, 50)])
    report = build_context_dataset(tmp_path, run_benchmark_fn=stub, tasks=tasks)
    d = context_dataset_as_dict(report)
    assert d["builtin_task_count"] == 0
    assert d["extra_task_count"] == 0


# ---------------------------------------------------------------------------
# load_extra_tasks_toml
# ---------------------------------------------------------------------------


def test_load_extra_tasks_toml_basic(tmp_path: Path) -> None:
    p = tmp_path / "extra.toml"
    p.write_text(_tasks_toml([{"name": "Extra", "description": "Extra task"}]), encoding="utf-8")
    tasks = load_extra_tasks_toml(p)
    assert len(tasks) == 1
    assert tasks[0].name == "Extra"
    assert tasks[0].description == "Extra task"


def test_load_extra_tasks_toml_multiple(tmp_path: Path) -> None:
    p = tmp_path / "extra.toml"
    p.write_text(_tasks_toml([{"description": f"Task {i}"} for i in range(3)]), encoding="utf-8")
    tasks = load_extra_tasks_toml(p)
    assert len(tasks) == 3


# ---------------------------------------------------------------------------
# render_context_dataset_markdown
# ---------------------------------------------------------------------------


def test_render_context_dataset_markdown_header() -> None:
    data = {
        "command": "context-dataset",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "repo": "/repo",
        "task_count": 0,
        "builtin_task_count": 0,
        "extra_task_count": 0,
        "aggregate": {
            "total_baseline_tokens": 0,
            "total_optimized_tokens": 0,
            "avg_baseline_tokens": 0,
            "avg_optimized_tokens": 0,
            "avg_reduction_pct": 0,
        },
        "entries": [],
    }
    md = render_context_dataset_markdown(data)
    assert "# Redcon Context Dataset Report" in md


def test_render_context_dataset_markdown_builtin_source() -> None:
    data = {
        "task_count": 3,
        "builtin_task_count": 3,
        "extra_task_count": 0,
        "repo": "/repo",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "aggregate": {
            "total_baseline_tokens": 0,
            "total_optimized_tokens": 0,
            "avg_baseline_tokens": 0,
            "avg_optimized_tokens": 0,
            "avg_reduction_pct": 0,
        },
        "entries": [],
    }
    md = render_context_dataset_markdown(data)
    assert "built-in" in md


def test_render_context_dataset_markdown_mixed_source() -> None:
    data = {
        "task_count": 4,
        "builtin_task_count": 3,
        "extra_task_count": 1,
        "repo": "/repo",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "aggregate": {
            "total_baseline_tokens": 0,
            "total_optimized_tokens": 0,
            "avg_baseline_tokens": 0,
            "avg_optimized_tokens": 0,
            "avg_reduction_pct": 0,
        },
        "entries": [],
    }
    md = render_context_dataset_markdown(data)
    assert "built-in" in md
    assert "custom" in md


def test_render_context_dataset_markdown_entries() -> None:
    data = {
        "task_count": 2,
        "builtin_task_count": 2,
        "extra_task_count": 0,
        "repo": "/repo",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "aggregate": {
            "total_baseline_tokens": 1500,
            "total_optimized_tokens": 500,
            "avg_baseline_tokens": 750,
            "avg_optimized_tokens": 250,
            "avg_reduction_pct": 66.7,
        },
        "entries": [
            {"task": "Add Caching", "task_name": "Add Caching", "baseline_tokens": 1000, "optimized_tokens": 300, "reduction_pct": 70.0},
            {"task": "Add Auth", "task_name": "", "baseline_tokens": 500, "optimized_tokens": 200, "reduction_pct": 60.0},
        ],
    }
    md = render_context_dataset_markdown(data)
    assert "Add Caching" in md
    assert "Add Auth" in md
    assert "66.7%" in md


# ---------------------------------------------------------------------------
# CLI - redcon build-dataset (integration)
# ---------------------------------------------------------------------------


def test_cli_build_dataset_writes_json_and_markdown(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "build-dataset", "--repo", str(repo), "--out-prefix", "cbd"],
    )
    assert main() == 0
    assert (tmp_path / "cbd.json").exists()
    assert (tmp_path / "cbd.md").exists()


def test_cli_build_dataset_json_has_required_keys(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "build-dataset", "--repo", str(repo), "--out-prefix", "cbd2"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "cbd2.json").read_text(encoding="utf-8"))
    for key in ("command", "generated_at", "repo", "task_count", "aggregate", "entries",
                "builtin_task_count", "extra_task_count"):
        assert key in data, f"Missing key: {key}"
    assert data["task_count"] == len(BUILTIN_TASKS)
    assert data["builtin_task_count"] == len(BUILTIN_TASKS)
    assert data["extra_task_count"] == 0


def test_cli_build_dataset_entry_has_reduction_metrics(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "build-dataset", "--repo", str(repo), "--out-prefix", "cbd3"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "cbd3.json").read_text(encoding="utf-8"))
    for entry in data["entries"]:
        assert "baseline_tokens" in entry
        assert "optimized_tokens" in entry
        assert "reduction_pct" in entry
        assert isinstance(entry["reduction_pct"], float)
        assert entry["baseline_tokens"] >= 0
        assert entry["optimized_tokens"] >= 0


def test_cli_build_dataset_with_extra_toml(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    toml_path = tmp_path / "extra.toml"
    toml_path.write_text('[[tasks]]\nname = "Custom"\ndescription = "Custom benchmark task"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "redcon", "build-dataset",
            "--repo", str(repo),
            "--tasks-toml", str(toml_path),
            "--out-prefix", "cbd4",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "cbd4.json").read_text(encoding="utf-8"))
    assert data["task_count"] == len(BUILTIN_TASKS) + 1
    assert data["builtin_task_count"] == len(BUILTIN_TASKS)
    assert data["extra_task_count"] == 1


def test_cli_build_dataset_no_builtin_with_toml(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    toml_path = tmp_path / "tasks.toml"
    toml_path.write_text(
        '[[tasks]]\ndescription = "Only custom task"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "redcon", "build-dataset",
            "--repo", str(repo),
            "--tasks-toml", str(toml_path),
            "--no-builtin",
            "--out-prefix", "cbd5",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "cbd5.json").read_text(encoding="utf-8"))
    assert data["task_count"] == 1
    assert data["builtin_task_count"] == 0
    assert data["extra_task_count"] == 1


def test_cli_build_dataset_markdown_header(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "build-dataset", "--repo", str(repo), "--out-prefix", "cbd6"],
    )
    assert main() == 0
    md = (tmp_path / "cbd6.md").read_text(encoding="utf-8")
    assert "# Redcon Context Dataset Report" in md


def test_cli_build_dataset_aggregate_in_json(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "build-dataset", "--repo", str(repo), "--out-prefix", "cbd7"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "cbd7.json").read_text(encoding="utf-8"))
    agg = data["aggregate"]
    for key in ("total_baseline_tokens", "total_optimized_tokens", "avg_baseline_tokens",
                "avg_optimized_tokens", "avg_reduction_pct"):
        assert key in agg, f"Missing aggregate key: {key}"
