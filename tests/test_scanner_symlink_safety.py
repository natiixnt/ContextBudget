"""The scanner must never follow a symlink whose target escapes the repo.

A repository is untrusted input. A symlink inside it that points at a host file
(SSH keys, cloud credentials, /etc/passwd) must not be read and packed into
LLM-bound context. Broken and circular links must be skipped without crashing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from redcon.scanners.incremental import refresh_scan_index


def _symlink(src: Path, dst: Path) -> None:
    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")


def _previews(records) -> str:
    return "\n".join(r.content_preview for r in records)


def _rel_paths(records) -> set[str]:
    return {r.relative_path for r in records}


def test_symlink_escaping_repo_is_not_read(tmp_path: Path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP_SECRET_SSH_KEY_MARKER\n", encoding="utf-8")
    (repo / "real.py").write_text("value = 1\n", encoding="utf-8")
    # A repo-supplied symlink pointing at a file outside the repo root.
    _symlink(outside / "secret.txt", repo / "leak.txt")

    result = refresh_scan_index(repo)
    rels = _rel_paths(result.records)

    assert "real.py" in rels  # the genuine file is scanned
    assert "leak.txt" not in rels  # the escaping symlink is not
    assert "TOP_SECRET" not in _previews(result.records)  # its content never read


def test_relative_dotdot_symlink_escape_is_blocked(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "host_secret").write_text("HOST_SECRET_MARKER\n", encoding="utf-8")
    (repo / "keep.py").write_text("x = 1\n", encoding="utf-8")
    # ../host_secret climbs out of the repo via a relative link.
    _symlink(Path("..") / "host_secret", repo / "escape.txt")

    result = refresh_scan_index(repo)

    assert "HOST_SECRET" not in _previews(result.records)
    assert "escape.txt" not in _rel_paths(result.records)
    assert "keep.py" in _rel_paths(result.records)


def test_broken_symlink_is_skipped(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("value = 1\n", encoding="utf-8")
    _symlink(repo / "does_not_exist.txt", repo / "dangling.txt")

    result = refresh_scan_index(repo)  # must not raise

    assert "real.py" in _rel_paths(result.records)
    assert "dangling.txt" not in _rel_paths(result.records)


def test_circular_symlinks_terminate(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("value = 1\n", encoding="utf-8")
    _symlink(repo / "b.txt", repo / "a.txt")
    _symlink(repo / "a.txt", repo / "b.txt")  # a -> b -> a

    result = refresh_scan_index(repo)  # must terminate, not hang or crash

    assert "real.py" in _rel_paths(result.records)
    assert "a.txt" not in _rel_paths(result.records)
    assert "b.txt" not in _rel_paths(result.records)


def test_internal_symlink_is_still_followed(tmp_path: Path):
    # The containment check must not block a legitimate symlink whose target
    # stays inside the repo.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "target.py").write_text("INSIDE_REPO_MARKER = 1\n", encoding="utf-8")
    _symlink(repo / "src" / "target.py", repo / "alias.py")

    result = refresh_scan_index(repo)

    # The real target is scanned; the content is available either way.
    assert "INSIDE_REPO_MARKER" in _previews(result.records)
