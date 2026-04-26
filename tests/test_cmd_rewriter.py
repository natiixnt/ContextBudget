"""Tests for the argv rewriter."""

from __future__ import annotations

import pytest

from redcon.cmd.rewriter import rewrite_argv


# --- lossless: git diff always gets better algorithm flags ---


def test_git_diff_gets_histogram_and_rename_flags():
    out = rewrite_argv(("git", "diff"))
    assert "--histogram" in out
    assert "--find-copies-harder" in out
    assert "-M" in out
    assert "-C" in out


def test_git_diff_with_existing_path_arg_still_rewrites():
    out = rewrite_argv(("git", "diff", "HEAD"))
    assert "HEAD" in out
    assert "--histogram" in out


def test_git_diff_skips_rewrite_when_user_chose_algorithm():
    out = rewrite_argv(("git", "diff", "--myers"))
    assert "--histogram" not in out


def test_git_diff_skips_rewrite_when_user_disabled_renames():
    out = rewrite_argv(("git", "diff", "--no-renames"))
    assert "-M" not in out


def test_git_diff_idempotent_when_flags_already_present():
    once = rewrite_argv(("git", "diff"))
    twice = rewrite_argv(once)
    assert once == twice


def test_non_git_diff_is_untouched():
    out = rewrite_argv(("git", "log"))
    assert out == ("git", "log")


# --- compact mode opt-in ---


def test_pytest_gets_tb_line_and_quiet_in_compact_mode():
    out = rewrite_argv(("pytest",), prefer_compact=True)
    assert "--tb=line" in out
    assert "-q" in out


def test_pytest_skips_tb_when_user_set_one():
    out = rewrite_argv(("pytest", "--tb=long"), prefer_compact=True)
    assert "--tb=line" not in out
    # -q is still added because user chose -v elsewhere is the only opt-out
    assert "-q" in out


def test_pytest_skips_quiet_when_user_set_verbose():
    out = rewrite_argv(("pytest", "-v"), prefer_compact=True)
    assert "-q" not in out


def test_python_dash_m_pytest_also_rewritten():
    out = rewrite_argv(("python", "-m", "pytest"), prefer_compact=True)
    assert "--tb=line" in out
    assert "-q" in out


def test_cargo_test_gets_quiet_in_compact_mode():
    out = rewrite_argv(("cargo", "test"), prefer_compact=True)
    assert "--quiet" in out


def test_cargo_test_skips_when_already_verbose():
    out = rewrite_argv(("cargo", "test", "--verbose"), prefer_compact=True)
    assert "--quiet" not in out


def test_jest_gets_basic_reporter_in_compact_mode():
    out = rewrite_argv(("jest",), prefer_compact=True)
    assert "--reporter=basic" in out


def test_vitest_gets_basic_reporter_in_compact_mode():
    out = rewrite_argv(("vitest",), prefer_compact=True)
    assert "--reporter=basic" in out


def test_jest_skips_when_user_set_reporter():
    out = rewrite_argv(("jest", "--reporter=json"), prefer_compact=True)
    assert "--reporter=basic" not in out


def test_npx_jest_handled():
    out = rewrite_argv(("npx", "jest"), prefer_compact=True)
    assert "--reporter=basic" in out


def test_npx_unrelated_binary_left_alone():
    out = rewrite_argv(("npx", "tsc"), prefer_compact=True)
    assert out == ("npx", "tsc")


def test_compact_mode_off_leaves_test_runners_alone():
    out = rewrite_argv(("pytest",), prefer_compact=False)
    assert "--tb=line" not in out


def test_unknown_command_returned_unchanged():
    out = rewrite_argv(("uname", "-a"), prefer_compact=True)
    assert out == ("uname", "-a")
