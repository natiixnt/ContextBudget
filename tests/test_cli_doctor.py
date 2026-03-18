from __future__ import annotations

import json
from pathlib import Path

from redcon.cli import main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_cli_doctor_returns_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["redcon", "doctor", "--repo", str(tmp_path)])
    assert main() == 0


def test_cli_doctor_json_output(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["redcon", "doctor", "--repo", str(tmp_path), "--json"])
    assert main() == 0
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["command"] == "doctor"
    assert "checks" in data
    assert "summary" in data


def test_cli_doctor_human_output(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["redcon", "doctor", "--repo", str(tmp_path)])
    main()
    output = capsys.readouterr().out
    assert "Redcon Doctor" in output
    assert "passed" in output


def test_cli_doctor_fails_on_bad_config(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path / "redcon.toml", "[budget]\nmax_tokens = -1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["redcon", "doctor", "--repo", str(tmp_path)])
    assert main() == 1


def test_cli_pack_context_only_format(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "redcon", "pack", "update auth",
            "--repo", str(repo),
            "--format", "context-only",
        ],
    )
    assert main() == 0
    output = capsys.readouterr().out
    # Should contain the file header and code
    assert "# File:" in output
    assert "def login" in output
    # Should NOT contain run metadata
    assert "Wrote run JSON" not in output


def test_cli_pack_json_format(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "redcon", "pack", "update auth",
            "--repo", str(repo),
            "--format", "json",
            "--out-prefix", str(tmp_path / "jrun"),
        ],
    )
    assert main() == 0
    output = capsys.readouterr().out
    data = json.loads(output)
    assert "budget" in data
    assert "compressed_context" in data


def test_cli_pack_rejects_invalid_config(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "redcon.toml", "[budget]\nmax_tokens = -1\n")
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "pack", "update auth", "--repo", str(repo)],
    )
    assert main() == 2
    stderr = capsys.readouterr().err
    assert "invalid configuration" in stderr


def test_cli_plan_rejects_invalid_config(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "redcon.toml", "[budget]\nmax_tokens = 0\n")
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "plan", "update auth", "--repo", str(repo)],
    )
    assert main() == 2
    stderr = capsys.readouterr().err
    assert "invalid configuration" in stderr


def test_cli_verbose_flag_accepted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "--verbose", "doctor", "--repo", str(tmp_path)],
    )
    assert main() == 0


def test_cli_quiet_flag_accepted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "--quiet", "doctor", "--repo", str(tmp_path)],
    )
    assert main() == 0
