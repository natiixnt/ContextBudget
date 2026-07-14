"""Ranking signals: commit freshness and test/source pairing.

Guards the two signals added for #9 - a recently committed (now clean) file
still gets a boost, and a relevant source file pulls in its test counterpart
(and vice versa).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from redcon.config import ScoreSettings
from redcon.scanners.repository import scan_repository
from redcon.scorers.relevance import (
    _build_test_source_pairs,
    _test_base_name,
    score_files,
)
from redcon.stages.workflow import _get_git_recent_paths


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


# -- commit freshness --------------------------------------------------------


def test_recent_commit_boosts_relevance(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def authenticate():\n    return True\n")
    records = scan_repository(tmp_path)
    settings = ScoreSettings()

    without = {r.file.path: r for r in score_files("authenticate", records, settings)}
    with_recent = {
        r.file.path: r
        for r in score_files("authenticate", records, settings, recent_paths={"src/auth.py": 1.0})
    }

    assert with_recent["src/auth.py"].score > without["src/auth.py"].score
    assert any("recently committed" in reason for reason in with_recent["src/auth.py"].reasons)


def test_recent_paths_ignored_when_boost_zero(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def authenticate():\n    return True\n")
    records = scan_repository(tmp_path)
    settings = ScoreSettings(git_recent_boost=0.0)

    ranked = {
        r.file.path: r
        for r in score_files("authenticate", records, settings, recent_paths={"src/auth.py": 1.0})
    }
    assert not any("recently committed" in r for r in ranked["src/auth.py"].reasons)


def test_get_git_recent_paths_weights_by_recency(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _write(tmp_path / "old.py", "x = 1\n")
    _git(tmp_path, "add", "old.py")
    _git(tmp_path, "commit", "-m", "old")
    _write(tmp_path / "new.py", "y = 2\n")
    _git(tmp_path, "add", "new.py")
    _git(tmp_path, "commit", "-m", "new")

    recent = _get_git_recent_paths(tmp_path, 10)

    assert recent["new.py"] == 1.0  # most recent commit -> full weight
    assert 0 < recent["old.py"] < recent["new.py"]  # older commit -> decayed


def test_get_git_recent_paths_empty_outside_git(tmp_path: Path) -> None:
    assert _get_git_recent_paths(tmp_path, 10) == {}


# -- test/source pairing -----------------------------------------------------


def test_test_base_name_recognizes_conventions() -> None:
    assert _test_base_name("test_auth.py") == "auth"
    assert _test_base_name("auth_test.py") == "auth"
    assert _test_base_name("auth_test.go") == "auth"
    assert _test_base_name("auth.test.ts") == "auth"
    assert _test_base_name("auth.spec.tsx") == "auth"
    assert _test_base_name("auth.py") is None


def test_pairs_are_bidirectional(tmp_path: Path) -> None:
    _write(tmp_path / "billing.py", "def charge():\n    return 1\n")
    _write(tmp_path / "test_billing.py", "def test_charge():\n    assert True\n")
    records = scan_repository(tmp_path)

    pairs = _build_test_source_pairs(records)
    assert "billing.py" in pairs["test_billing.py"]
    assert "test_billing.py" in pairs["billing.py"]


def test_relevant_source_pulls_in_its_test(tmp_path: Path) -> None:
    # billing.py is strongly relevant to the task via content; test_billing.py
    # is not relevant on its own (task words never appear in it).
    _write(
        tmp_path / "billing.py", "charge card\n" * 20 + "def charge_the_card():\n    return True\n"
    )
    _write(tmp_path / "test_billing.py", "def test_it():\n    assert True\n")
    records = scan_repository(tmp_path)

    ranked = {r.file.path: r for r in score_files("charge card", records)}

    assert "test_billing.py" in ranked
    assert any("paired with relevant file" in r for r in ranked["test_billing.py"].reasons)


def test_pairing_disabled_when_boost_zero(tmp_path: Path) -> None:
    _write(
        tmp_path / "billing.py", "charge card\n" * 20 + "def charge_the_card():\n    return True\n"
    )
    _write(tmp_path / "test_billing.py", "def test_it():\n    assert True\n")
    records = scan_repository(tmp_path)

    ranked = {
        r.file.path: r
        for r in score_files("charge card", records, ScoreSettings(test_pair_boost=0.0))
    }
    if "test_billing.py" in ranked:
        assert not any("paired with relevant file" in r for r in ranked["test_billing.py"].reasons)
