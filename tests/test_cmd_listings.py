"""Tests for grep/rg, ls, tree, find compressors."""

from __future__ import annotations

import pytest

from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import CompressorContext
from redcon.cmd.compressors.grep_compressor import GrepCompressor, parse_grep
from redcon.cmd.compressors.listing_compressor import (
    FindCompressor,
    LsCompressor,
    TreeCompressor,
    parse_find,
    parse_ls,
    parse_tree,
)
from redcon.cmd.registry import detect_compressor
from redcon.cmd.types import CompressionLevel

# --- grep / rg fixtures ---

GREP_INLINE = b"""\
src/foo.py:10:def foo():
src/foo.py:42:    return foo
src/bar.py:5:foo = 1
src/bar.py:88:    foo()
tests/test_foo.py:3:from foo import foo
"""

RG_GROUPED = b"""\
src/foo.py
10:def foo():
42:    return foo

src/bar.py
5:foo = 1
88:    foo()

tests/test_foo.py
3:from foo import foo
"""

# --- ls fixtures ---

LS_SHORT = b"""\
foo.py
bar.py
__init__.py
subdir/
"""

LS_LONG = b"""\
total 24
drwxr-xr-x  5 user staff   160 Jan  1 12:00 .
drwxr-xr-x 10 user staff   320 Jan  1 12:00 ..
-rw-r--r--  1 user staff  1234 Jan  1 12:00 foo.py
-rw-r--r--  1 user staff   567 Jan  1 12:00 bar.py
drwxr-xr-x  3 user staff    96 Jan  1 12:00 subdir
"""

LS_RECURSIVE = b"""\
.:
foo.py
bar.py
subdir

./subdir:
baz.py
qux.py
"""

# --- tree fixture ---

TREE_OUTPUT = b"""\
.
\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 foo.py
\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 bar.py
\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 subdir
    \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 baz.py
    \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 qux.py

1 directories, 4 files
"""

# --- find fixture ---

FIND_OUTPUT = b"""\
./src/foo.py
./src/bar.py
./src/utils/
./tests/test_foo.py
./tests/test_bar.py
"""


# --- grep parser ---


def test_parse_grep_inline_form():
    result = parse_grep(GREP_INLINE.decode())
    assert result.match_count == 5
    assert result.file_count == 3
    paths = {m.path for m in result.matches}
    assert paths == {"src/foo.py", "src/bar.py", "tests/test_foo.py"}


def test_parse_grep_grouped_form():
    result = parse_grep(RG_GROUPED.decode())
    assert result.match_count == 5
    assert result.file_count == 3


RG_JSON = b"""\
{"type":"begin","data":{"path":{"text":"src/foo.py"}}}
{"type":"match","data":{"path":{"text":"src/foo.py"},"lines":{"text":"def foo():\\n"},"line_number":10,"absolute_offset":120,"submatches":[{"match":{"text":"foo"},"start":4,"end":7}]}}
{"type":"match","data":{"path":{"text":"src/foo.py"},"lines":{"text":"    return foo\\n"},"line_number":42,"absolute_offset":900,"submatches":[{"match":{"text":"foo"},"start":11,"end":14}]}}
{"type":"end","data":{"path":{"text":"src/foo.py"},"binary_offset":null,"stats":{"bytes_searched":2000,"bytes_printed":40,"matches":2,"matched_lines":2}}}
{"type":"begin","data":{"path":{"text":"tests/test_foo.py"}}}
{"type":"match","data":{"path":{"text":"tests/test_foo.py"},"lines":{"text":"from foo import foo\\n"},"line_number":3,"absolute_offset":0,"submatches":[{"match":{"text":"foo"},"start":5,"end":8}]}}
{"type":"end","data":{"path":{"text":"tests/test_foo.py"},"binary_offset":null,"stats":{}}}
{"type":"summary","data":{"stats":{}}}
"""


def test_parse_grep_json_form():
    """rg --json output should be detected and parsed without falling back to text."""
    result = parse_grep(RG_JSON.decode())
    assert result.match_count == 3
    assert result.file_count == 2
    paths = {m.path for m in result.matches}
    assert paths == {"src/foo.py", "tests/test_foo.py"}
    by_line = {(m.path, m.line): m.text for m in result.matches}
    assert by_line[("src/foo.py", 10)] == "def foo():"
    assert by_line[("tests/test_foo.py", 3)] == "from foo import foo"


def test_grep_compressor_handles_rg_json_at_compact():
    """End-to-end: feed rg --json bytes to the compressor and verify output."""
    from redcon.cmd.compressors.grep_compressor import GrepCompressor

    comp = GrepCompressor()
    ctx = _ctx(("rg", "--json", "foo"), CompressionLevel.COMPACT)
    out = comp.compress(RG_JSON, b"", ctx)
    assert "src/foo.py" in out.text
    assert "tests/test_foo.py" in out.text
    assert out.must_preserve_ok is True


def test_rg_json_strictly_smaller_than_text_for_same_query():
    """JSON form is verbose; our compressed re-emit must shrink it.

    The point of parsing rg --json is to drop redundant offsets/stats.
    Asserting compressed_tokens < raw_tokens by a wide margin guards against
    accidentally passing JSON through unchanged.
    """
    from redcon.cmd.compressors.grep_compressor import GrepCompressor

    comp = GrepCompressor()
    ctx = _ctx(("rg", "--json", "foo"), CompressionLevel.COMPACT)
    out = comp.compress(RG_JSON, b"", ctx)
    # Raw JSON Lines is heavy; compact-mode output should be at least 50% smaller.
    assert out.reduction_pct >= 50.0


def test_grep_compressor_compact_keeps_paths():
    comp = GrepCompressor()
    ctx = _ctx(("rg", "foo"), CompressionLevel.COMPACT)
    out = comp.compress(GREP_INLINE, b"", ctx)
    assert "src/foo.py" in out.text
    assert "src/bar.py" in out.text
    assert "tests/test_foo.py" in out.text
    assert out.must_preserve_ok is True


def test_grep_compressor_ultra_summary_only():
    comp = GrepCompressor()
    ctx = _ctx(("rg", "foo"), CompressionLevel.ULTRA, remaining=10, cap=2)
    out = comp.compress(GREP_INLINE, b"", ctx)
    assert "5 matches" in out.text
    assert "3 files" in out.text


def test_grep_matches():
    comp = GrepCompressor()
    assert comp.matches(("grep", "-r", "foo"))
    assert comp.matches(("rg", "foo"))
    assert comp.matches(("egrep", "-r", "foo"))
    assert not comp.matches(("git", "grep", "foo"))


# --- ls parser ---


def test_parse_ls_short():
    result = parse_ls(LS_SHORT.decode())
    paths = [e.path for e in result.entries]
    assert "foo.py" in paths
    assert "subdir" in paths
    assert any(e.kind == "dir" for e in result.entries)


def test_parse_ls_long_extracts_size():
    result = parse_ls(LS_LONG.decode())
    foo = next((e for e in result.entries if e.path.endswith("foo.py")), None)
    assert foo is not None
    assert foo.size == 1234


def test_parse_ls_recursive_with_directory_headers():
    result = parse_ls(LS_RECURSIVE.decode())
    paths = [e.path for e in result.entries]
    assert any(p.endswith("subdir/baz.py") or p == "./subdir/baz.py" for p in paths)


def test_ls_compressor_compact_includes_extension_histogram():
    comp = LsCompressor()
    ctx = _ctx(("ls",), CompressionLevel.COMPACT)
    out = comp.compress(LS_LONG, b"", ctx)
    assert "py" in out.text


# --- tree parser ---


def test_parse_tree_extracts_files_and_dirs():
    result = parse_tree(TREE_OUTPUT.decode())
    paths = [e.path for e in result.entries]
    assert any("foo.py" in p for p in paths)
    assert any("bar.py" in p for p in paths)


def test_tree_compressor_must_preserve_top_level():
    comp = TreeCompressor()
    ctx = _ctx(("tree",), CompressionLevel.COMPACT)
    out = comp.compress(TREE_OUTPUT, b"", ctx)
    assert "foo.py" in out.text or "bar.py" in out.text


# --- find parser ---


def test_parse_find_basic():
    result = parse_find(FIND_OUTPUT.decode())
    paths = [e.path for e in result.entries]
    assert "./src/foo.py" in paths
    assert "./tests/test_foo.py" in paths


def test_find_compressor_groups_by_dir():
    comp = FindCompressor()
    ctx = _ctx(("find", "."), CompressionLevel.COMPACT)
    out = comp.compress(FIND_OUTPUT, b"", ctx)
    assert "src" in out.text
    assert "tests" in out.text


# --- registry ---


def test_registry_detects_listing_tools():
    assert detect_compressor(("grep", "-r", "foo")).schema == "grep"
    assert detect_compressor(("rg", "foo")).schema == "grep"
    assert detect_compressor(("ls",)).schema == "ls"
    assert detect_compressor(("tree",)).schema == "tree"
    assert detect_compressor(("find", ".", "-name", "*.py")).schema == "find"


# --- helpers ---


def _ctx(
    argv: tuple[str, ...],
    level: CompressionLevel,
    *,
    remaining: int | None = None,
    cap: int | None = None,
) -> CompressorContext:
    if level == CompressionLevel.VERBOSE:
        hint = BudgetHint(remaining_tokens=100_000, max_output_tokens=10_000)
    elif level == CompressionLevel.COMPACT:
        hint = BudgetHint(
            remaining_tokens=remaining or 200,
            max_output_tokens=cap or 4_000,
        )
    else:
        hint = BudgetHint(
            remaining_tokens=remaining or 10,
            max_output_tokens=cap or 2,
        )
    return CompressorContext(
        argv=argv, cwd=".", returncode=0, hint=hint
    )
