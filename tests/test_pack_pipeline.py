from __future__ import annotations

from pathlib import Path

from contextbudget.core.pipeline import as_json_dict, run_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_run_pack_builds_budget_report(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def auth():\n    return 'ok'\n" * 20)
    _write(tmp_path / "src" / "middleware.py", "def auth_middleware():\n    return auth()\n" * 20)

    report = run_pack("refactor auth middleware", repo=tmp_path, max_tokens=1000)
    data = as_json_dict(report)

    assert data["budget"]["estimated_input_tokens"] <= 1000
    assert "files_included" in data
    assert "files_skipped" in data
    assert data["budget"]["quality_risk_estimate"] in {"low", "medium", "high"}


def test_duplicate_reads_prevented_on_same_content(tmp_path: Path) -> None:
    content = "def same():\n    return 1\n" * 20
    _write(tmp_path / "src" / "a.py", content)
    _write(tmp_path / "src" / "b.py", content)

    report = run_pack("change same function", repo=tmp_path, max_tokens=1000)
    data = as_json_dict(report)

    assert data["budget"]["duplicate_reads_prevented"] >= 1


def test_summary_cache_hits_on_second_run(tmp_path: Path) -> None:
    long_text = "\n".join([f"line {i}" for i in range(2000)])
    _write(tmp_path / "src" / "large.py", long_text)

    first = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))
    second = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))

    assert first["cache_hits"] == 0
    assert second["cache_hits"] >= 1
