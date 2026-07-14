"""The file-count cap is configurable and surfaced instead of silent.

A monorepo scan that hits the cap used to drop the alphabetically-last files
without any signal. These guard that the cap is reported on the scan summary,
propagated into run.json, and adjustable via [scan].max_file_count.
"""

from __future__ import annotations

from pathlib import Path

from redcon.config import default_config
from redcon.core.pipeline import as_json_dict, run_pack
from redcon.scanners.incremental import refresh_scan_index


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_reports_cap_when_limit_hit(tmp_path: Path) -> None:
    for index in range(10):
        _write(tmp_path / f"file_{index}.py", "x = 1\n")

    result = refresh_scan_index(tmp_path, max_file_count=5)

    assert result.summary.file_count_capped is True
    assert result.summary.file_count_limit == 5


def test_scan_does_not_report_cap_under_limit(tmp_path: Path) -> None:
    for index in range(5):
        _write(tmp_path / f"file_{index}.py", "x = 1\n")

    result = refresh_scan_index(tmp_path, max_file_count=1000)

    assert result.summary.file_count_capped is False


def test_run_pack_surfaces_cap_in_report(tmp_path: Path) -> None:
    for index in range(8):
        _write(tmp_path / f"mod_{index}.py", "def f():\n    return 1\n")

    config = default_config()
    config.scan.max_file_count = 3
    report = run_pack("touch modules", repo=tmp_path, max_tokens=1000, config=config)

    scan = as_json_dict(report)["scan"]
    assert scan["file_count_capped"] is True
    assert scan["file_count_limit"] == 3


def test_run_pack_has_no_cap_metadata_when_under_limit(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "def f():\n    return 1\n")
    _write(tmp_path / "b.py", "def g():\n    return 2\n")

    report = run_pack("touch modules", repo=tmp_path, max_tokens=1000)

    assert as_json_dict(report)["scan"] == {}


def test_default_config_file_cap_is_50k() -> None:
    assert default_config().scan.max_file_count == 50_000
