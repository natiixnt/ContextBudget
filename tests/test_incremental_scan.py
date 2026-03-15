from __future__ import annotations

import os
from pathlib import Path

from redcon.scanners.incremental import load_scan_index, refresh_scan_index
from redcon.schemas.models import SCAN_INDEX_FILE


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _bump_mtime(path: Path) -> None:
    stat_result = path.stat()
    updated_ns = stat_result.st_mtime_ns + 1_000_000
    os.utime(path, ns=(updated_ns, updated_ns))


def test_incremental_scan_reuses_unchanged_file_metadata(tmp_path: Path, monkeypatch) -> None:
    tracked_file = tmp_path / "src" / "auth.py"
    _write(tracked_file, "def check_token() -> bool:\n    return True\n")

    first = refresh_scan_index(tmp_path)
    assert first.summary.added_count == 1

    def fail_if_rebuilt(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("unchanged files should reuse cached scan metadata")

    monkeypatch.setattr("redcon.scanners.incremental._build_file_record", fail_if_rebuilt)

    second = refresh_scan_index(tmp_path)

    assert second.summary.reused_count == 1
    assert second.summary.updated_count == 0
    assert second.records[0].content_hash == first.records[0].content_hash


def test_incremental_scan_invalidates_changed_file_metadata(tmp_path: Path) -> None:
    tracked_file = tmp_path / "src" / "auth.py"
    _write(tracked_file, "def check_token() -> bool:\n    return True\n")

    first = refresh_scan_index(tmp_path)

    _write(tracked_file, "def check_token() -> bool:\n    return False\n")
    _bump_mtime(tracked_file)

    second = refresh_scan_index(tmp_path)

    assert second.summary.updated_count == 1
    assert second.summary.reused_count == 0
    assert second.records[0].content_hash != first.records[0].content_hash

    index = load_scan_index(tmp_path / SCAN_INDEX_FILE)
    assert index["entries"][0]["content_hash"] == second.records[0].content_hash


def test_incremental_scan_removes_deleted_files_from_index(tmp_path: Path) -> None:
    removed_file = tmp_path / "src" / "old.py"
    kept_file = tmp_path / "src" / "current.py"
    _write(removed_file, "OLD = True\n")
    _write(kept_file, "CURRENT = True\n")

    refresh_scan_index(tmp_path)
    removed_file.unlink()

    result = refresh_scan_index(tmp_path)

    assert result.summary.removed_count == 1
    assert result.summary.removed_paths == ["src/old.py"]
    assert [record.path for record in result.records] == ["src/current.py"]

    index = load_scan_index(tmp_path / SCAN_INDEX_FILE)
    assert [entry["path"] for entry in index["entries"]] == ["src/current.py"]
