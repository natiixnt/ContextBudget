"""Tests for the redcon.cmd.benchmark harness."""

from __future__ import annotations

import json

import pytest

from redcon.cmd.benchmark import (
    Benchmark,
    LevelBenchmark,
    render_json,
    render_markdown,
    run_benchmark,
    run_benchmarks,
)
from redcon.cmd.compressors.git_diff import GitDiffCompressor

# Use the same diff fixture as the quality / unit tests for consistency.
DIFF_FIXTURE = b"""\
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
@@ -0,0 +1,3 @@
+print("hi")
+x = 1
+y = 2
"""


def test_run_benchmark_returns_three_levels():
    result = run_benchmark(
        "git_diff_small",
        GitDiffCompressor(),
        DIFF_FIXTURE,
        b"",
        ("git", "diff"),
        warm_iterations=2,
    )
    assert isinstance(result, Benchmark)
    assert result.schema == "git_diff"
    assert result.fixture == "git_diff_small"
    levels = {lvl.level for lvl in result.levels}
    assert levels == {"verbose", "compact", "ultra"}


def test_warm_is_at_most_marginally_slower_than_cold():
    """Warm path should be at least as fast as cold; a wild jump signals a leak."""
    result = run_benchmark(
        "git_diff_small",
        GitDiffCompressor(),
        DIFF_FIXTURE,
        b"",
        ("git", "diff"),
        warm_iterations=10,
    )
    for lvl in result.levels:
        # Warm should never be more than 5x slower than cold; allows for
        # noise on a busy laptop without making the test flaky.
        assert lvl.warm_seconds <= lvl.cold_seconds * 5 + 0.005


def test_run_benchmarks_over_multiple_cases():
    cases = [
        ("c1", GitDiffCompressor(), DIFF_FIXTURE, b"", ("git", "diff")),
        ("c2", GitDiffCompressor(), DIFF_FIXTURE, b"", ("git", "diff")),
    ]
    results = run_benchmarks(cases)
    assert len(results) == 2
    assert all(isinstance(r, Benchmark) for r in results)


def test_render_json_round_trips():
    results = [
        run_benchmark(
            "x",
            GitDiffCompressor(),
            DIFF_FIXTURE,
            b"",
            ("git", "diff"),
            warm_iterations=1,
        )
    ]
    payload = render_json(results)
    decoded = json.loads(payload)
    assert decoded[0]["schema"] == "git_diff"
    assert len(decoded[0]["levels"]) == 3


def test_render_markdown_contains_reduction_columns():
    results = [
        run_benchmark(
            "x",
            GitDiffCompressor(),
            DIFF_FIXTURE,
            b"",
            ("git", "diff"),
            warm_iterations=1,
        )
    ]
    md = render_markdown(results)
    assert "fixture" in md
    assert "reduction" in md
    assert "verbose" in md
    assert "compact" in md
    assert "ultra" in md
    assert "Per-schema averages" in md


def test_default_cases_imports_from_quality_corpus():
    from redcon.cmd.benchmark import _default_cases

    cases = _default_cases()
    # M8's CASES list has 16 entries today; we don't pin the exact number
    # so adding fixtures there doesn't churn this test.
    assert len(cases) >= 8
    # Each case is (name, compressor, stdout, stderr, argv).
    for case in cases:
        assert len(case) == 5
        assert isinstance(case[0], str)
        assert isinstance(case[2], bytes)
        assert isinstance(case[4], tuple)


def test_main_runs_and_prints(capsys):
    from redcon.cmd.benchmark import main

    rc = main(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    # Every entry has the expected shape.
    for entry in parsed:
        assert "schema" in entry
        assert "levels" in entry
        assert len(entry["levels"]) == 3
