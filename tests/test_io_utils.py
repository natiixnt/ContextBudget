"""Tests for redcon.io_utils.atomic_write_text."""

from __future__ import annotations

from pathlib import Path

import pytest

from redcon.io_utils import atomic_write_text


def test_atomic_write_creates_file_and_parents(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "out.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_write_text(target, "old content")
    atomic_write_text(target, "new content")
    assert target.read_text(encoding="utf-8") == "new content"


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_write_text(target, "hello")
    # Only the target should remain - the temp file is replaced, not left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]


def test_atomic_write_failure_removes_temp_and_raises(tmp_path: Path) -> None:
    # A directory where the target name already exists as a directory makes the
    # final os.replace fail; the temp file must be cleaned up, not orphaned.
    target = tmp_path / "collision"
    target.mkdir()
    with pytest.raises(OSError):
        atomic_write_text(target, "data")
    # No leftover temp files: the directory 'collision' is the only entry.
    assert [p.name for p in tmp_path.iterdir()] == ["collision"]
