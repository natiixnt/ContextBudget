from __future__ import annotations

from pathlib import Path

from contextbudget.scanners.repository import scan_repository
from contextbudget.scorers.relevance import score_files


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_repository_finds_text_files(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def check_token():\n    return True\n")
    _write(tmp_path / "README.md", "Authentication module")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    records = scan_repository(tmp_path)
    paths = [r.path for r in records]

    assert "src/auth.py" in paths
    assert "README.md" in paths
    assert "image.png" not in paths


def test_score_files_ranks_keyword_matches(tmp_path: Path) -> None:
    _write(tmp_path / "api" / "search.py", "def cache_search():\n    pass\n")
    _write(tmp_path / "docs" / "notes.md", "misc text")

    records = scan_repository(tmp_path)
    ranked = score_files("add caching to search API", records)

    assert ranked
    assert ranked[0].file.path == "api/search.py"
    assert ranked[0].score > 0
