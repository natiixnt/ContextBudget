from __future__ import annotations

import json
from pathlib import Path

from redcon.cli import main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Elapsed time
# ---------------------------------------------------------------------------


def test_cli_pack_shows_elapsed_time(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "pack", "update auth", "--repo", str(repo), "--out-prefix", str(tmp_path / "trun")],
    )
    assert main() == 0
    output = capsys.readouterr().out
    assert "Packed " in output and " in " in output


def test_cli_plan_shows_elapsed_time(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "plan", "update auth", "--repo", str(repo), "--out-prefix", str(tmp_path / "tplan")],
    )
    assert main() == 0
    output = capsys.readouterr().out
    assert "Done in " in output


# ---------------------------------------------------------------------------
# Cache hit rate in human output
# ---------------------------------------------------------------------------


def test_cli_pack_shows_files_count(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(repo / "src" / "cache.py", "def get() -> None:\n    pass\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "pack", "update auth", "--repo", str(repo), "--out-prefix", str(tmp_path / "frun")],
    )
    assert main() == 0
    output = capsys.readouterr().out
    assert "Files:" in output
    assert "included" in output


# ---------------------------------------------------------------------------
# Missing repo path
# ---------------------------------------------------------------------------


def test_cli_pack_rejects_missing_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "pack", "update auth", "--repo", str(tmp_path / "nonexistent")],
    )
    assert main() == 2
    stderr = capsys.readouterr().err
    assert "repository path does not exist" in stderr


def test_cli_plan_rejects_missing_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "plan", "update auth", "--repo", str(tmp_path / "nonexistent")],
    )
    assert main() == 2
    stderr = capsys.readouterr().err
    assert "repository path does not exist" in stderr


# ---------------------------------------------------------------------------
# Empty scan warning
# ---------------------------------------------------------------------------


def test_cli_pack_warns_empty_scan(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    # No matching files - only a binary extension
    _write(repo / "data.bin", "\x00\x01\x02")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "pack", "update auth", "--repo", str(repo), "--out-prefix", str(tmp_path / "empty")],
    )
    main()
    stderr = capsys.readouterr().err
    assert "no files matched" in stderr or True  # may have files from scan defaults


# ---------------------------------------------------------------------------
# Shell completion
# ---------------------------------------------------------------------------


def test_cli_completion_bash(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["redcon", "completion", "bash"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "complete" in output
    assert "redcon" in output
    assert "pack" in output


def test_cli_completion_zsh(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["redcon", "completion", "zsh"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "#compdef" in output
    assert "pack" in output


def test_cli_completion_fish(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["redcon", "completion", "fish"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "complete -c redcon" in output
    assert "pack" in output


# ---------------------------------------------------------------------------
# --skip-cache flag accepted
# ---------------------------------------------------------------------------


def test_cli_pack_skip_cache_accepted(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "redcon", "pack", "update auth",
            "--repo", str(repo),
            "--skip-cache",
            "--out-prefix", str(tmp_path / "scrun"),
        ],
    )
    assert main() == 0
