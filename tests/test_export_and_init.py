from __future__ import annotations

import json
from pathlib import Path

from redcon.cli import main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# redcon export
# ---------------------------------------------------------------------------


def test_cli_export_to_stdout(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "pack", "update auth", "--repo", str(repo), "--out-prefix", str(tmp_path / "run")],
    )
    assert main() == 0
    capsys.readouterr()  # clear pack output

    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "export", str(tmp_path / "run.json")],
    )
    assert main() == 0
    output = capsys.readouterr().out
    assert "# File:" in output
    assert "def login" in output


def test_cli_export_to_file(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "pack", "update auth", "--repo", str(repo), "--out-prefix", str(tmp_path / "erun")],
    )
    assert main() == 0
    capsys.readouterr()

    out_file = tmp_path / "context.txt"
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "export", str(tmp_path / "erun.json"), "--out", str(out_file)],
    )
    assert main() == 0
    content = out_file.read_text(encoding="utf-8")
    assert "# File:" in content
    assert "def login" in content


def test_cli_export_missing_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "export", str(tmp_path / "nonexistent.json")],
    )
    assert main() == 2


# ---------------------------------------------------------------------------
# redcon init - CI workflow
# ---------------------------------------------------------------------------


def test_cli_init_generates_ci_workflow(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "app.py", "print('hello')\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "init", "--repo", str(repo)],
    )
    assert main() == 0

    assert (repo / "redcon.toml").exists()
    assert (repo / "policy.toml").exists()
    assert (repo / ".github" / "workflows" / "redcon-pr-audit.yml").exists()

    workflow = (repo / ".github" / "workflows" / "redcon-pr-audit.yml").read_text()
    assert "redcon pr-audit" in workflow
    assert "pull_request" in workflow

    output = capsys.readouterr().out
    assert "redcon-pr-audit.yml" in output


def test_cli_init_does_not_overwrite_existing_workflow(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "app.py", "print('hello')\n")

    # Create existing workflow
    wf_path = repo / ".github" / "workflows" / "redcon-pr-audit.yml"
    _write(wf_path, "# custom workflow\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["redcon", "init", "--repo", str(repo), "--force"],
    )
    assert main() == 0

    # --force should overwrite
    workflow = wf_path.read_text()
    assert "redcon pr-audit" in workflow


def test_cli_init_next_steps_include_doctor(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "app.py", "print('hello')\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["redcon", "init", "--repo", str(repo)])
    main()
    output = capsys.readouterr().out
    assert "redcon doctor" in output
