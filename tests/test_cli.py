from __future__ import annotations

from pathlib import Path

from contextbudget.cli import main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_cli_pack_and_report(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "pack", "add caching to search api", "--repo", str(repo), "--out-prefix", "run"],
    )
    assert main() == 0
    assert (tmp_path / "run.json").exists()
    assert (tmp_path / "run.md").exists()

    monkeypatch.setattr("sys.argv", ["contextbudget", "report", "run.json", "--out", "summary.md"])
    assert main() == 0
    assert (tmp_path / "summary.md").exists()
