"""SQLite scan-index recovery paths that run by default in production.

use_sqlite defaults to True, so the SQLite store is the primary read source and
the JSON index is its fallback. These cover the two recovery paths that were
previously untested: a corrupt SQLite database falling back to the JSON index,
and an existing JSON index migrating into a fresh SQLite database.
"""

from __future__ import annotations

from pathlib import Path

from redcon.scanners.incremental import (
    SCAN_INDEX_DB_FILE,
    load_scan_index,
    refresh_scan_index,
)
from redcon.schemas.models import SCAN_INDEX_FILE


def _seed(repo: Path) -> None:
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")


def test_corrupt_sqlite_falls_back_to_json_index(tmp_path: Path) -> None:
    _seed(tmp_path)
    first = refresh_scan_index(tmp_path)  # writes both the JSON index and SQLite
    assert first.summary.added_count == 1

    db_path = tmp_path / SCAN_INDEX_DB_FILE
    json_path = tmp_path / SCAN_INDEX_FILE
    assert db_path.exists()
    assert json_path.exists()

    # Corrupt the SQLite database so its load returns None.
    db_path.write_bytes(b"this is not a valid sqlite database")

    # The scan must not crash: it falls back to the JSON index and still reuses
    # the unchanged file instead of rescanning from scratch.
    second = refresh_scan_index(tmp_path)
    assert second.summary.added_count == 0
    assert second.summary.reused_count >= 1
    # The JSON index remains readable and a fresh, valid SQLite db is rewritten.
    assert load_scan_index(json_path)


def test_existing_json_index_migrates_into_sqlite(tmp_path: Path) -> None:
    _seed(tmp_path)

    # Build only the JSON index, as a pre-SQLite install would have.
    refresh_scan_index(tmp_path, use_sqlite=False)
    db_path = tmp_path / SCAN_INDEX_DB_FILE
    assert not db_path.exists()
    assert (tmp_path / SCAN_INDEX_FILE).exists()

    # Enabling SQLite migrates the JSON index in, then reuses the unchanged file
    # from the migrated data (added=0, reused>=1) rather than rescanning.
    migrated = refresh_scan_index(tmp_path)  # use_sqlite=True (default)
    assert db_path.exists()
    assert migrated.summary.added_count == 0
    assert migrated.summary.reused_count >= 1
