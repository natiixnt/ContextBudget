"""Tests for git diff / status / log compressors."""

from __future__ import annotations

import re

import pytest

from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.compressors.git_diff import GitDiffCompressor, parse_diff
from redcon.cmd.compressors.git_log import (
    GitLogCompressor,
    parse_log_default,
    parse_log_oneline,
)
from redcon.cmd.compressors.git_status import GitStatusCompressor, parse_status
from redcon.cmd.types import CompressionLevel

# --- fixtures: realistic command outputs ---

DIFF_SIMPLE = b"""\
diff --git a/foo.py b/foo.py
index 1234567..89abcde 100644
--- a/foo.py
+++ b/foo.py
@@ -10,7 +10,8 @@ def hello():
     a = 1
-    b = 2
+    b = 3
+    c = 4
     d = 5
diff --git a/bar.py b/bar.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/bar.py
@@ -0,0 +1,3 @@
+print("hi")
+x = 1
+y = 2
"""

DIFF_RENAME = b"""\
diff --git a/old.py b/new.py
similarity index 95%
rename from old.py
rename to new.py
index aaa..bbb 100644
--- a/old.py
+++ b/new.py
@@ -1,3 +1,3 @@
-print(1)
+print(2)
 keep
"""

DIFF_BINARY = b"""\
diff --git a/img.png b/img.png
index aaaa..bbbb 100644
Binary files a/img.png and b/img.png differ
"""

STATUS_PORCELAIN = b"""\
## main...origin/main [ahead 2]
 M foo.py
?? new.py
A  added.py
R  old.py -> new.py
"""

LOG_DEFAULT = b"""\
commit abc1234567890def1234567890abcdef12345678
Author: Foo <foo@example.com>
Date:   Mon Jan 1 12:00:00 2025

    Add new feature

    With body details.

commit def5678901234abc5678901234567890abcdef12
Author: Bar <bar@example.com>
Date:   Sun Dec 31 23:59:59 2024

    Fix bug
"""

LOG_ONELINE = b"""\
abc1234 Add new feature
def5678 Fix bug
0011223 Refactor module
"""


# --- diff parser tests ---


def test_parse_diff_two_files():
    result = parse_diff(DIFF_SIMPLE.decode())
    assert len(result.files) == 2
    foo, bar = result.files
    assert foo.path == "foo.py"
    assert foo.status == "modified"
    assert foo.insertions == 2
    assert foo.deletions == 1
    assert bar.path == "bar.py"
    assert bar.status == "added"
    assert bar.insertions == 3
    assert bar.deletions == 0


def test_parse_diff_rename():
    result = parse_diff(DIFF_RENAME.decode())
    assert len(result.files) == 1
    f = result.files[0]
    assert f.status == "renamed"
    assert f.old_path == "old.py"
    assert f.path == "new.py"


def test_parse_diff_binary():
    result = parse_diff(DIFF_BINARY.decode())
    assert len(result.files) == 1
    assert result.files[0].binary is True


# --- diff compressor levels ---


@pytest.mark.parametrize(
    "level,must_substr",
    [
        (CompressionLevel.VERBOSE, "+    b = 3"),
        (CompressionLevel.COMPACT, "M foo.py"),
        (CompressionLevel.ULTRA, "diff:"),
    ],
)
def test_diff_compressor_levels(level: CompressionLevel, must_substr: str):
    comp = GitDiffCompressor()
    hint = _hint_for_level(level)
    ctx = CompressorContext(
        argv=("git", "diff"), cwd=".", returncode=0, hint=hint
    )
    out = comp.compress(DIFF_SIMPLE, b"", ctx)
    assert out.level == level
    assert must_substr in out.text


def test_diff_compressor_preserves_paths_at_compact():
    comp = GitDiffCompressor()
    ctx = CompressorContext(
        argv=("git", "diff"),
        cwd=".",
        returncode=0,
        hint=_hint_for_level(CompressionLevel.COMPACT),
    )
    out = comp.compress(DIFF_SIMPLE, b"", ctx)
    assert "foo.py" in out.text
    assert "bar.py" in out.text


def test_diff_compressor_must_preserve_ok():
    comp = GitDiffCompressor()
    ctx = CompressorContext(
        argv=("git", "diff"),
        cwd=".",
        returncode=0,
        hint=_hint_for_level(CompressionLevel.COMPACT),
    )
    out = comp.compress(DIFF_SIMPLE, b"", ctx)
    assert out.must_preserve_ok is True


def test_diff_compressor_real_reduction():
    comp = GitDiffCompressor()
    ctx = CompressorContext(
        argv=("git", "diff"),
        cwd=".",
        returncode=0,
        hint=_hint_for_level(CompressionLevel.ULTRA),
    )
    out = comp.compress(DIFF_SIMPLE, b"", ctx)
    assert out.compressed_tokens < out.original_tokens
    assert out.reduction_pct > 0


def test_diff_empty_output():
    comp = GitDiffCompressor()
    ctx = CompressorContext(
        argv=("git", "diff"),
        cwd=".",
        returncode=0,
        hint=_hint_for_level(CompressionLevel.COMPACT),
    )
    out = comp.compress(b"", b"", ctx)
    assert "no diff" in out.text.lower()


def test_diff_compressor_matches():
    comp = GitDiffCompressor()
    assert comp.matches(("git", "diff"))
    assert comp.matches(("git", "diff", "HEAD"))
    assert not comp.matches(("git", "status"))
    assert not comp.matches(("ls",))


# --- status parser tests ---


def test_parse_status_branch_and_entries():
    result = parse_status(STATUS_PORCELAIN.decode())
    assert result.branch == "main"
    assert result.upstream == "origin/main"
    assert result.ahead == 2
    assert len(result.entries) == 4
    untracked = [e for e in result.entries if e.untracked]
    assert len(untracked) == 1
    renamed = [e for e in result.entries if e.renamed_from]
    assert len(renamed) == 1
    assert renamed[0].renamed_from == "old.py"
    assert renamed[0].path == "new.py"


def test_status_compressor_levels_preserve_branch():
    comp = GitStatusCompressor()
    for level in CompressionLevel:
        ctx = CompressorContext(
            argv=("git", "status"),
            cwd=".",
            returncode=0,
            hint=_hint_for_level(level),
        )
        out = comp.compress(STATUS_PORCELAIN, b"", ctx)
        assert "main" in out.text


def test_status_compressor_compact_lists_entries():
    comp = GitStatusCompressor()
    ctx = CompressorContext(
        argv=("git", "status"),
        cwd=".",
        returncode=0,
        hint=_hint_for_level(CompressionLevel.COMPACT),
    )
    out = comp.compress(STATUS_PORCELAIN, b"", ctx)
    assert "foo.py" in out.text
    assert "new.py" in out.text


# --- log parser tests ---


def test_parse_log_default_two_commits():
    result = parse_log_default(LOG_DEFAULT.decode())
    assert len(result.entries) == 2
    e1 = result.entries[0]
    assert e1.short_sha == "abc1234"
    assert "Foo" in e1.author
    assert e1.subject == "Add new feature"
    assert "With body details" in e1.body


def test_parse_log_oneline():
    result = parse_log_oneline(LOG_ONELINE.decode())
    assert len(result.entries) == 3
    assert result.entries[0].short_sha == "abc1234"
    assert result.entries[2].subject == "Refactor module"


def test_log_compressor_matches():
    comp = GitLogCompressor()
    assert comp.matches(("git", "log"))
    assert comp.matches(("git", "log", "--oneline"))
    assert not comp.matches(("git", "diff"))


def test_log_compressor_compact_keeps_subjects():
    comp = GitLogCompressor()
    ctx = CompressorContext(
        argv=("git", "log"),
        cwd=".",
        returncode=0,
        hint=_hint_for_level(CompressionLevel.COMPACT),
    )
    out = comp.compress(LOG_DEFAULT, b"", ctx)
    assert "Add new feature" in out.text
    assert "Fix bug" in out.text


def test_log_compressor_ultra_summarises():
    comp = GitLogCompressor()
    ctx = CompressorContext(
        argv=("git", "log"),
        cwd=".",
        returncode=0,
        hint=_hint_for_level(CompressionLevel.ULTRA),
    )
    out = comp.compress(LOG_DEFAULT, b"", ctx)
    assert out.level == CompressionLevel.ULTRA
    assert "2 commits" in out.text


# --- must_preserve helper ---


def test_verify_must_preserve_ignores_patterns_absent_from_original():
    original = "nothing here"
    patterns = (r"\bfoo\b",)
    # Pattern wasn't in original, so it can't be required to be in compressed.
    assert verify_must_preserve("compressed text", patterns, original) is True


def test_verify_must_preserve_catches_lost_facts():
    original = "this has foo and bar"
    compressed = "summary only"
    patterns = (r"\bfoo\b",)
    assert verify_must_preserve(compressed, patterns, original) is False


# --- helpers ---


def _hint_for_level(level: CompressionLevel) -> BudgetHint:
    """Build a BudgetHint that forces the given level via the budget math."""
    if level == CompressionLevel.VERBOSE:
        # Plenty of budget -> verbose fits.
        return BudgetHint(remaining_tokens=100_000, max_output_tokens=10_000)
    if level == CompressionLevel.COMPACT:
        # Verbose share = 30% of 200 = 60 tokens, fixture diff is ~100 tokens raw,
        # so verbose doesn't fit but compact (15% of raw = 15) does.
        return BudgetHint(remaining_tokens=200, max_output_tokens=4_000)
    # ULTRA: hard cap below compact size for any non-trivial output.
    return BudgetHint(remaining_tokens=10, max_output_tokens=2)
