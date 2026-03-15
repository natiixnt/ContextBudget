from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextbudget.core.heatmap import build_heatmap_report


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pack_run(
    *,
    generated_at: str,
    task: str,
    repo: str,
    entries: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "command": "pack",
        "generated_at": generated_at,
        "task": task,
        "repo": repo,
        "files_included": [str(item["path"]) for item in entries],
        "compressed_context": entries,
        "budget": {
            "estimated_input_tokens": sum(int(item["compressed_tokens"]) for item in entries),
            "estimated_saved_tokens": sum(
                max(0, int(item["original_tokens"]) - int(item["compressed_tokens"])) for item in entries
            ),
        },
    }


def test_build_heatmap_report_aggregates_files_and_directories(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    run_one = _pack_run(
        generated_at="2026-03-10T10:00:00+00:00",
        task="run one",
        repo=str(tmp_path / "repo"),
        entries=[
            {"path": "src/auth.py", "original_tokens": 100, "compressed_tokens": 60},
            {"path": "src/cache.py", "original_tokens": 50, "compressed_tokens": 25},
        ],
    )
    run_two = _pack_run(
        generated_at="2026-03-11T10:00:00+00:00",
        task="run two",
        repo=str(tmp_path / "repo"),
        entries=[
            {"path": "src/auth.py", "original_tokens": 120, "compressed_tokens": 70},
            {"path": "src/services/payment.py", "original_tokens": 90, "compressed_tokens": 40},
        ],
    )
    _write(history_dir / "run-1.json", json.dumps(run_one))
    _write(history_dir / "run-2.json", json.dumps(run_two))

    report = build_heatmap_report([history_dir], limit=2)

    assert report.runs_analyzed == 2
    assert report.unique_files == 3
    assert report.unique_directories == 2
    assert report.top_token_heavy_files[0].path == "src/auth.py"
    assert report.most_frequently_included_files[0].path == "src/auth.py"
    assert report.largest_token_savings_opportunities[0].path == "src/auth.py"

    files = {item.path: item for item in report.files}
    auth = files["src/auth.py"]
    assert auth.total_original_tokens == 220
    assert auth.total_compressed_tokens == 130
    assert auth.total_saved_tokens == 90
    assert auth.inclusion_count == 2
    assert auth.run_count == 2
    assert auth.inclusion_rate == 1.0
    assert [item.generated_at for item in auth.history] == [
        "2026-03-10T10:00:00+00:00",
        "2026-03-11T10:00:00+00:00",
    ]

    directories = {item.path: item for item in report.directories}
    src = directories["src"]
    assert src.total_original_tokens == 360
    assert src.total_compressed_tokens == 195
    assert src.total_saved_tokens == 165
    assert src.run_count == 2
    assert src.inclusion_rate == 1.0
    assert directories["src/services"].total_compressed_tokens == 40


def test_build_heatmap_report_handles_workspace_paths_and_skipped_artifacts(tmp_path: Path) -> None:
    run = _pack_run(
        generated_at="2026-03-12T09:00:00+00:00",
        task="workspace run",
        repo=str(tmp_path / "workspace"),
        entries=[
            {"path": "auth-service:src/auth.py", "original_tokens": 80, "compressed_tokens": 50},
            {"path": "billing-service:src/billing.py", "original_tokens": 70, "compressed_tokens": 30},
        ],
    )
    _write(tmp_path / "workspace-run.json", json.dumps(run))
    _write(tmp_path / "broken.json", "{not-json")
    _write(tmp_path / "plan.json", json.dumps({"task": "plan only", "ranked_files": []}))

    report = build_heatmap_report([tmp_path], limit=5)

    assert report.runs_analyzed == 1
    assert report.unique_directories == 4

    directories = {item.path: item for item in report.directories}
    assert directories["auth-service"].total_compressed_tokens == 50
    assert directories["auth-service/src"].total_compressed_tokens == 50
    assert directories["billing-service"].total_compressed_tokens == 30
    assert directories["billing-service/src"].total_compressed_tokens == 30

    skipped = {Path(item.artifact_path).name: item.reason for item in report.skipped_artifacts}
    assert "invalid JSON" in skipped["broken.json"]
    assert skipped["plan.json"] == "not a pack run artifact"


def test_build_heatmap_report_requires_pack_run_artifacts(tmp_path: Path) -> None:
    _write(tmp_path / "plan.json", json.dumps({"task": "plan only", "ranked_files": []}))

    with pytest.raises(ValueError, match="No pack run artifacts found"):
        build_heatmap_report([tmp_path], limit=3)
