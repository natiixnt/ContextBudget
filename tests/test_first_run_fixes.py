"""Regression guards for the first-run correctness fixes (Tier A)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from redcon.scanners.incremental import refresh_scan_index
from redcon.stages.workflow import _get_git_recent_paths


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def test_python_m_redcon_runs_cli():
    # The VS Code setup step invokes `python -m redcon`; it must work even when
    # the console script is not on PATH.
    result = subprocess.run(
        [sys.executable, "-m", "redcon", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_scan_index_keys_on_repo_label(tmp_path: Path):
    (tmp_path / "auth.py").write_text("value = 1\n", encoding="utf-8")

    standalone = refresh_scan_index(tmp_path)
    assert standalone.records
    assert all(r.repo_label == "" for r in standalone.records)

    # Same repo and unchanged files, now scanned under a workspace label. The
    # records must be re-classified under the new label, not served stale from
    # the unlabeled index (which would attribute files to the wrong repo).
    labeled = refresh_scan_index(tmp_path, repo_label="svc")
    assert labeled.records
    assert all(r.repo_label == "svc" for r in labeled.records)


@pytest.mark.skipif(sys.platform != "linux", reason="filesystem unicode normalization varies")
def test_recent_paths_handle_non_ascii(tmp_path: Path):
    # With git's default core.quotePath, a Cyrillic path comes back quoted and
    # never matches the scanned relative_path, silently losing the boost.
    _git(tmp_path, "init")
    (tmp_path / "модуль.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "unicode")

    recent = _get_git_recent_paths(tmp_path, 10)
    assert "модуль.py" in recent
