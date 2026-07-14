"""Memoization behavior for build_import_graph.

A single pack builds the import graph twice (scoring stage + compression
stage) and plan-agent/simulate rebuild it once per workflow step over the
same file set. Each build reads every source file from disk, so on a
monorepo that is the dominant cost. These guard that identical file sets
return the cached graph and that any content change invalidates it.
"""

from __future__ import annotations

from pathlib import Path

from redcon.scanners.repository import scan_repository
from redcon.scorers import import_graph
from redcon.scorers.import_graph import build_import_graph


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sample_repo(root: Path) -> None:
    _write(root / "src" / "main.py", "from src import auth\n")
    _write(root / "src" / "auth.py", "value = 1\n")
    _write(root / "src" / "__init__.py", "")


def test_build_import_graph_memoizes_identical_file_sets(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    import_graph._GRAPH_CACHE.clear()
    records = scan_repository(tmp_path)

    first = build_import_graph(records)
    second = build_import_graph(records)

    # Same paths + content hashes -> the second call returns the cached graph.
    assert first is second


def test_build_import_graph_rebuilds_after_content_change(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    import_graph._GRAPH_CACHE.clear()
    first = build_import_graph(scan_repository(tmp_path))

    (tmp_path / "src" / "main.py").write_text("from src import auth\n# changed\n", encoding="utf-8")
    second = build_import_graph(scan_repository(tmp_path))

    # A changed content_hash invalidates the key -> a fresh build.
    assert first is not second


def test_build_import_graph_entrypoints_are_part_of_the_key(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    import_graph._GRAPH_CACHE.clear()
    records = scan_repository(tmp_path)

    without = build_import_graph(records)
    with_entrypoints = build_import_graph(records, entrypoint_filenames={"main.py"})

    # Different entrypoints -> different key -> distinct cached graphs.
    assert without is not with_entrypoints


def test_build_import_graph_cache_is_bounded(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    import_graph._GRAPH_CACHE.clear()
    records = scan_repository(tmp_path)

    # Distinct entrypoint sets produce distinct keys; the cache must not grow
    # without bound as unique file sets accumulate across runs.
    for index in range(import_graph._GRAPH_CACHE_MAX + 3):
        build_import_graph(records, entrypoint_filenames={f"entry_{index}.py"})

    assert len(import_graph._GRAPH_CACHE) <= import_graph._GRAPH_CACHE_MAX
