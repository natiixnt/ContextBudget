"""The import graph reuses scan-index import specs instead of re-reading files.

The scanner extracts each file's import specs once (while it already has the
text in memory) and stores them on the FileRecord. Later import-graph builds
resolve from those cached specs, so an unchanged repo is not re-read every
process. Correctness is unchanged: the graph is identical whether specs come
from the cache or from a fresh read.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from redcon.scanners.incremental import refresh_scan_index
from redcon.scorers.import_graph import build_import_graph, extract_import_specs


def _outgoing(graph) -> dict[str, list[str]]:
    return {src: sorted(dst) for src, dst in graph.outgoing.items() if dst}


def test_scan_populates_import_specs(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import b\nfrom c import x\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("x = 2\n", encoding="utf-8")

    records = {r.relative_path: r for r in refresh_scan_index(tmp_path).records}

    # a.py's specs are captured; b.py has none but is still recorded as "[]"
    # (extracted, no imports) rather than "" (not extracted).
    assert json.loads(records["a.py"].import_specs) == ["b", "c", "c.x"]
    assert records["b.py"].import_specs == "[]"


def test_graph_built_from_cached_specs_reads_no_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("value = 1\n", encoding="utf-8")
    records = refresh_scan_index(tmp_path).records

    def _no_reads(*_args, **_kwargs):
        raise AssertionError("import graph re-read a file despite cached specs")

    with patch("redcon.scorers.import_graph.Path.read_text", _no_reads):
        graph = build_import_graph(records)

    by = {r.relative_path: r for r in records}
    assert by["b.py"].path in graph.outgoing[by["a.py"].path]


def test_cached_and_read_graphs_are_identical_python(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import b\nfrom pkg import c\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import c\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    _assert_identical(tmp_path)


def test_cached_and_read_graphs_are_identical_js(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text("import {x} from './b';\nrequire('./c');\n", encoding="utf-8")
    (tmp_path / "b.js").write_text("export const x = 1;\n", encoding="utf-8")
    (tmp_path / "c.js").write_text("module.exports = 2;\n", encoding="utf-8")
    _assert_identical(tmp_path)


def test_cached_and_read_graphs_are_identical_go(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "main.go").write_text(
        'package main\nimport (\n\t"app/util"\n)\n', encoding="utf-8"
    )
    (tmp_path / "util").mkdir()
    (tmp_path / "util" / "u.go").write_text("package util\n", encoding="utf-8")
    _assert_identical(tmp_path)


def _assert_identical(repo: Path) -> None:
    records = refresh_scan_index(repo).records

    import redcon.scorers.import_graph as ig

    ig._GRAPH_CACHE.clear()
    cached = _outgoing(build_import_graph(records))

    # Force the read fallback by clearing the cached specs.
    for record in records:
        record.import_specs = ""
    ig._GRAPH_CACHE.clear()
    read = _outgoing(build_import_graph(records))

    assert cached == read


def test_empty_import_specs_falls_back_to_read(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("value = 1\n", encoding="utf-8")
    records = refresh_scan_index(tmp_path).records
    for record in records:
        record.import_specs = ""  # simulate a pre-v2 record

    import redcon.scorers.import_graph as ig

    ig._GRAPH_CACHE.clear()
    graph = build_import_graph(records)

    by = {r.relative_path: r for r in records}
    assert by["b.py"].path in graph.outgoing[by["a.py"].path]


def test_extract_import_specs_by_extension() -> None:
    assert extract_import_specs(".py", "import os\nfrom a import b\n") == ["os", "a", "a.b"]
    assert extract_import_specs(".ts", "import x from './m';") == ["./m"]
    assert extract_import_specs(".go", 'import "fmt"\n') == ["fmt"]
    assert extract_import_specs(".txt", "import nothing here") == []
