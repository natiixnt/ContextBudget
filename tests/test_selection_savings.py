"""Selection-savings reporting (A1).

The "saved" budget number only counts in-file compression, which is frequently
zero. Redcon's real win is picking a subset of files, so a run reports a
selection baseline - what dumping the whole scanned universe would cost - so the
value delivered is visible even when no in-file compression happened.
"""

from __future__ import annotations

from pathlib import Path

from redcon.core.pipeline import as_json_dict, run_pack
from redcon.core.render import _selection_savings_md_lines, render_pack_markdown


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _repo_with_many_files(root: Path, count: int = 30) -> int:
    total_bytes = 0
    for i in range(count):
        body = (
            f"def feature_{i}(payload):\n"
            + "    # gateway auth and rate limiting logic\n" * 20
            + f"    return process_{i}(payload)\n"
        )
        path = root / "src" / f"mod_{i:02d}.py"
        _write(path, body)
        total_bytes += len(body.encode("utf-8"))
    return total_bytes


def test_run_pack_reports_selection_baseline(tmp_path: Path) -> None:
    total_bytes = _repo_with_many_files(tmp_path, count=30)

    report = run_pack("refactor feature_01 auth", repo=tmp_path, max_tokens=500)
    data = as_json_dict(report)

    # The whole scanned universe is accounted for by the char/4 heuristic.
    assert data["files_scanned"] == 30
    assert data["context_baseline_tokens"] == total_bytes // 4

    # A tight budget forces a subset, so the baseline is strictly larger than
    # what was actually sent - a real, honest saving.
    sent = data["budget"]["estimated_input_tokens"]
    assert data["context_baseline_tokens"] > sent
    assert len(data["files_included"]) < data["files_scanned"]


def test_selection_savings_line_in_markdown(tmp_path: Path) -> None:
    _repo_with_many_files(tmp_path, count=30)
    data = as_json_dict(run_pack("refactor feature_01 auth", repo=tmp_path, max_tokens=500))

    markdown = render_pack_markdown(data)
    assert "Context sent:" in markdown
    assert "% less" in markdown


def test_savings_lines_reported_when_subset_chosen() -> None:
    data = {
        "context_baseline_tokens": 10_000,
        "files_scanned": 40,
        "files_included": ["a.py", "b.py"],
    }
    lines = _selection_savings_md_lines(data, {"estimated_input_tokens": 2_000})
    assert len(lines) == 1
    # 2000 of 10000 sent -> 80% less.
    assert "80% less" in lines[0]
    assert "2 of 40 files" in lines[0]


def test_no_savings_line_when_nothing_was_dropped() -> None:
    # Every scanned file was included: there is no selection saving to claim.
    data = {
        "context_baseline_tokens": 5_000,
        "files_scanned": 3,
        "files_included": ["a.py", "b.py", "c.py"],
    }
    assert _selection_savings_md_lines(data, {"estimated_input_tokens": 4_000}) == []


def test_no_savings_line_when_baseline_not_larger() -> None:
    # Baseline is not above what was sent (tiny repo): no honest saving.
    data = {
        "context_baseline_tokens": 100,
        "files_scanned": 5,
        "files_included": ["a.py"],
    }
    assert _selection_savings_md_lines(data, {"estimated_input_tokens": 100}) == []


def test_no_savings_line_when_baseline_absent() -> None:
    # Older run.json without the fields must not raise or fabricate a saving.
    data = {"files_included": ["a.py"]}
    assert _selection_savings_md_lines(data, {"estimated_input_tokens": 200}) == []
