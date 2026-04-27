"""
Regression coverage for the V47 snapshot-delta framework.

Verifies that the generic line-delta swap fires correctly on the highest
traffic schemas (git_status, git_diff, pytest, grep, find) and stays
non-regressive (preserves absolute output) on dissimilar inputs.

The tests exercise `_maybe_swap_to_delta` directly with synthetic
formatted outputs rather than running real subprocesses; we are checking
the framework, not the compressors. Each test resets baselines so
ordering between tests does not leak state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from redcon.cmd import delta as _delta
from redcon.cmd._tokens_lite import estimate_tokens
from redcon.cmd.pipeline import _maybe_swap_to_delta
from redcon.cmd.types import CompressedOutput, CompressionLevel


@pytest.fixture(autouse=True)
def _reset_delta_baselines():
    _delta.reset_baselines()
    yield
    _delta.reset_baselines()


def _co(text: str, schema: str) -> CompressedOutput:
    return CompressedOutput(
        text=text,
        level=CompressionLevel.COMPACT,
        schema=schema,
        original_tokens=max(1, estimate_tokens(text) * 4),
        compressed_tokens=estimate_tokens(text),
        must_preserve_ok=True,
        truncated=False,
    )


def _swap(out: CompressedOutput, argv: tuple[str, ...]) -> CompressedOutput:
    return _maybe_swap_to_delta(out, argv=argv, cwd=Path("."), raw_text="x")


# ---------- git_status ----------


def test_first_call_passes_through_unchanged():
    out = _co("branch: main\nM a.py\nM b.py", "git_status")
    swapped = _swap(out, ("git", "status"))
    assert swapped.schema == "git_status"
    assert swapped.text == out.text


def test_second_similar_call_swaps_to_delta_git_status():
    argv = ("git", "status")
    base_lines = ["branch: main"] + [f"M dir{i}/file_{i}.py" for i in range(20)]
    base = _co("\n".join(base_lines), "git_status")
    _swap(base, argv)
    # one path swapped, all others unchanged
    follow_lines = base_lines.copy()
    follow_lines[5] = "M dir5/file_NEW.py"
    follow = _co("\n".join(follow_lines), "git_status")
    swapped = _swap(follow, argv)
    assert swapped.schema == "git_status_delta"
    assert "delta vs prior git_status" in swapped.text
    assert swapped.compressed_tokens < follow.compressed_tokens


# ---------- git_diff ----------


def test_git_diff_delta_swap():
    argv = ("git", "diff")
    base_text = (
        "diff: 5 files, +20 -5\n"
        "M redcon/cmd/pipeline.py: +5 -1\n"
        "M redcon/cmd/runner.py: +5 -1\n"
        "M redcon/cmd/types.py: +3 -1\n"
        "M redcon/cmd/aliasing.py: +4 -1\n"
        "M redcon/cmd/delta.py: +3 -1"
    )
    follow_text = base_text + "\nA redcon/cmd/_subst_table.py: +5 -0"
    _swap(_co(base_text, "git_diff"), argv)
    swapped = _swap(_co(follow_text, "git_diff"), argv)
    assert swapped.schema == "git_diff_delta"
    assert "+ A redcon/cmd/_subst_table.py: +5 -0" in swapped.text


# ---------- pytest ----------


def test_pytest_delta_on_repeated_run():
    argv = ("pytest", "-q")
    # 30 stable failures + 1 difference between calls -> delta beats absolute
    stable_lines = [
        f"FAIL tests/test_pkg{i}.py::test_case_{i} (tests/test_pkg{i}.py:{10 + i})"
        for i in range(30)
    ]
    base_text = "pytest: 100 passed (131 total) in 5.42s\n\n" + "\n".join(stable_lines)
    follow_text = (
        "pytest: 99 passed (131 total) in 5.39s\n\n"
        + "\n".join(stable_lines)
        + "\nFAIL tests/test_b.py::test_y (tests/test_b.py:51)"
    )
    _swap(_co(base_text, "pytest"), argv)
    swapped = _swap(_co(follow_text, "pytest"), argv)
    assert swapped.schema == "pytest_delta"
    assert "+ FAIL tests/test_b.py::test_y" in swapped.text
    assert swapped.compressed_tokens < estimate_tokens(follow_text)


# ---------- grep ----------


def test_grep_delta_swap():
    argv = ("grep", "-rn", "TODO", ".")
    stable = [f"src/file_{i:02d}.py:{10 + i}: TODO note {i}" for i in range(25)]
    base_text = "grep: 25 matches in 25 files\n" + "\n".join(stable)
    follow_text = (
        "grep: 26 matches in 26 files\n"
        + "\n".join(stable)
        + "\nsrc/new.py:99: TODO new"
    )
    _swap(_co(base_text, "grep"), argv)
    swapped = _swap(_co(follow_text, "grep"), argv)
    assert swapped.schema == "grep_delta"
    assert "+ src/new.py:99: TODO new" in swapped.text


# ---------- find ----------


def test_find_delta_swap():
    argv = ("find", ".", "-name", "*.py")
    stable = [f"src/sub{i // 5}/file_{i:02d}.py" for i in range(30)]
    base_text = "find: 30 entries\n" + "\n".join(stable)
    follow_text = "find: 31 entries\n" + "\n".join(stable) + "\nsrc/new/added.py"
    _swap(_co(base_text, "listing"), argv)
    swapped = _swap(_co(follow_text, "listing"), argv)
    assert swapped.schema == "listing_delta"
    assert "+ src/new/added.py" in swapped.text


# ---------- non-regressive on dissimilar inputs ----------


def test_dissimilar_inputs_keep_absolute():
    argv = ("git", "status")
    base = _co("branch: main\nM only_a.py\nM only_b.py", "git_status")
    _swap(base, argv)
    follow = _co(
        "branch: feature/x\nA totally/different/path.py\nA another/file.py\nA yet_another.py",
        "git_status",
    )
    swapped = _swap(follow, argv)
    # Jaccard between the two will be near-zero -> framework keeps absolute.
    assert swapped.schema == "git_status"


# ---------- baseline isolation per (argv, cwd) ----------


def test_baselines_keyed_on_argv():
    status_lines = ["branch: main"] + [f"M dir{i}/file_{i}.py" for i in range(20)]
    diff_lines = ["diff: 1 file"] + [
        f"M dir{i}/file_{i}.py: +{i} -0" for i in range(15)
    ]
    base_status = _co("\n".join(status_lines), "git_status")
    base_diff = _co("\n".join(diff_lines), "git_diff")
    _swap(base_status, ("git", "status"))
    _swap(base_diff, ("git", "diff"))
    # Wrong-schema baseline must not be picked up when argv differs.
    follow_lines = status_lines.copy()
    follow_lines.append("M extra.py")
    follow_status = _co("\n".join(follow_lines), "git_status")
    swapped = _swap(follow_status, ("git", "status"))
    assert swapped.schema == "git_status_delta"
    assert "diff:" not in swapped.text


def test_reset_baselines_clears_state():
    argv = ("git", "status")
    _swap(_co("branch: main\nM a.py", "git_status"), argv)
    _delta.reset_baselines()
    follow = _co("branch: main\nM a.py\nM b.py", "git_status")
    swapped = _swap(follow, argv)
    # Without a baseline, framework cannot delta - returns absolute.
    assert swapped.schema == "git_status"
    assert swapped.text == follow.text


# ---------- structured deltas (V47 schema-aware renderers) ----------


def test_structured_pytest_delta_set_diff_on_failure_names():
    """Structured renderer emits set-diff over failure names plus count delta."""
    from redcon.cmd.compressors.pytest_compressor import render_pytest_delta

    base_raw = (
        "============================= test session starts =============================\n"
        "collected 102 items\n\n"
        "=================================== FAILURES ===================================\n"
        "________________________________ test_old_failing __________________________________\n"
        "tests/test_a.py:42: in test_old_failing\n"
        "    assert x == y\n"
        "E   AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_old_failing - AssertionError\n"
        "============= 101 passed, 1 failed in 5.42s =============\n"
    )
    follow_raw = (
        "============================= test session starts =============================\n"
        "collected 102 items\n\n"
        "=================================== FAILURES ===================================\n"
        "________________________________ test_old_failing __________________________________\n"
        "tests/test_a.py:42: in test_old_failing\n"
        "    assert x == y\n"
        "E   AssertionError\n"
        "________________________________ test_new_failing __________________________________\n"
        "tests/test_b.py:51: in test_new_failing\n"
        "    assert foo() == bar\n"
        "E   AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_old_failing - AssertionError\n"
        "FAILED tests/test_b.py::test_new_failing - AssertionError\n"
        "============= 100 passed, 2 failed in 5.39s =============\n"
    )
    delta = render_pytest_delta(base_raw, follow_raw)
    assert "delta vs prior pytest" in delta
    assert "test_new_failing" in delta
    assert "100 passed" in delta
    assert "2 failed" in delta


def test_structured_git_diff_delta_file_set_with_counts():
    """Structured renderer emits file-set diff plus per-file counts."""
    from redcon.cmd.compressors.git_diff import render_git_diff_delta

    base_raw = (
        "diff --git a/foo.py b/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        " line\n"
        "+added\n"
        " line\n"
        " line\n"
        "diff --git a/bar.py b/bar.py\n"
        "@@ -10,2 +10,2 @@\n"
        "-old\n"
        "+new\n"
    )
    follow_raw = base_raw + (
        "diff --git a/baz.py b/baz.py\n"
        "new file mode 100644\n"
        "@@ -0,0 +1,3 @@\n"
        "+line1\n"
        "+line2\n"
        "+line3\n"
    )
    delta = render_git_diff_delta(base_raw, follow_raw)
    assert "delta vs prior git_diff" in delta
    assert "baz.py" in delta
    # Three files now, was two.
    assert "3 files" in delta
    assert "(+1)" in delta


def test_structured_renderer_returns_empty_on_unparseable_input():
    """Sentinel: parser extracts nothing -> renderer returns "" so the
    dispatcher knows to fall back to line-delta."""
    from redcon.cmd.compressors.pytest_compressor import render_pytest_delta
    from redcon.cmd.compressors.git_diff import render_git_diff_delta

    junk = "not a real pytest or diff output"
    assert render_pytest_delta(junk, junk) == ""
    assert render_git_diff_delta(junk, junk) == ""


def test_structured_coverage_delta_emits_per_file_moves():
    """Coverage delta renderer emits aggregate move + per-file pp shifts."""
    from redcon.cmd.compressors.coverage_compressor import render_coverage_delta

    base_raw = (
        "Name                          Stmts   Miss  Cover\n"
        "----------------\n"
        "redcon/cmd/pipeline.py            100      8  92.0%\n"
        "redcon/cmd/runner.py              200     20  90.0%\n"
        "redcon/cmd/quality.py             150     30  80.0%\n"
        "tests/test_a.py                    50      0 100.0%\n"
        "----------------\n"
        "TOTAL                             500     58  88.4%\n"
    )
    follow_raw = (
        "Name                          Stmts   Miss  Cover\n"
        "----------------\n"
        "redcon/cmd/pipeline.py            100     12  88.0%\n"
        "redcon/cmd/runner.py              200     20  90.0%\n"
        "redcon/cmd/quality.py             150     45  70.0%\n"
        "tests/test_a.py                    50      0 100.0%\n"
        "----------------\n"
        "TOTAL                             500     77  84.6%\n"
    )
    delta = render_coverage_delta(base_raw, follow_raw)
    assert "vs baseline 88.4%" in delta
    # quality.py dropped 10pp - should appear with negative delta.
    assert "redcon/cmd/quality.py" in delta
    assert "-10.0pp" in delta or "-10pp" in delta or "-10.0 pp" in delta
    # pipeline.py dropped 4pp - should also appear.
    assert "redcon/cmd/pipeline.py" in delta
    # runner.py unchanged (within threshold) - not in body.
    assert "redcon/cmd/runner.py: " not in delta


def test_structured_coverage_delta_returns_empty_on_unparseable():
    from redcon.cmd.compressors.coverage_compressor import render_coverage_delta

    junk = "this is not a coverage report"
    assert render_coverage_delta(junk, junk) == ""


def test_dispatcher_uses_structured_then_falls_back():
    """Dispatcher path: structured wins on real raw; fallback returns
    line-delta when structured returns sentinel."""
    from redcon.cmd.delta import (
        DeltaBaseline,
        render_delta_for_schema,
    )

    real_raw_a = (
        "=================================== FAILURES ===================================\n"
        "________________________________ test_x __________________________________\n"
        "tests/test_a.py:1: in test_x\n"
        "E   AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_x - AssertionError\n"
        "============= 50 passed, 1 failed in 1.0s =============\n"
    )
    real_raw_b = (
        "=================================== FAILURES ===================================\n"
        "________________________________ test_x __________________________________\n"
        "tests/test_a.py:1: in test_x\n"
        "E   AssertionError\n"
        "________________________________ test_y __________________________________\n"
        "tests/test_b.py:1: in test_y\n"
        "E   AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_x - AssertionError\n"
        "FAILED tests/test_b.py::test_y - AssertionError\n"
        "============= 49 passed, 2 failed in 1.0s =============\n"
    )
    structured = render_delta_for_schema(
        "pytest",
        baseline=DeltaBaseline(
            raw_text=real_raw_a,
            formatted_text="pytest: 50 passed",
            schema="pytest",
        ),
        current_formatted="pytest: 49 passed",
        current_raw=real_raw_b,
    )
    assert "test_y" in structured  # structured form fired

    # Now feed unparseable raw - dispatcher must fall back to line-delta.
    fallback = render_delta_for_schema(
        "pytest",
        baseline=DeltaBaseline(
            raw_text="bogus",
            formatted_text="line a\nline b\nline c",
            schema="pytest",
        ),
        current_formatted="line a\nline b\nline d",
        current_raw="bogus",
    )
    assert "delta vs prior pytest" in fallback
    assert "+ line d" in fallback  # line-delta marker
