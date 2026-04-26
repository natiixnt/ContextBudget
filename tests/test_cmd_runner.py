"""Tests for redcon.cmd.runner - subprocess execution with allowlist + timeout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from redcon.cmd.runner import (
    DEFAULT_ALLOWLIST,
    CommandNotAllowed,
    CommandTimeout,
    RunRequest,
    parse_command,
    run_command,
)


def test_parse_command_string():
    assert parse_command("git diff HEAD") == ("git", "diff", "HEAD")


def test_parse_command_list():
    assert parse_command(["git", "status", "-b"]) == ("git", "status", "-b")


def test_parse_command_empty():
    with pytest.raises(ValueError):
        parse_command("")


def test_run_blocks_unlisted_binary(tmp_path: Path):
    request = RunRequest(argv=("evilbinary", "--rm-rf", "/"), cwd=tmp_path)
    with pytest.raises(CommandNotAllowed):
        run_command(request)


def test_run_allows_git(tmp_path: Path):
    # `git --version` is safe everywhere git is installed.
    request = RunRequest(argv=("git", "--version"), cwd=tmp_path)
    result = run_command(request)
    assert result.returncode == 0
    assert b"git version" in result.stdout


def test_run_timeout_kills_process(tmp_path: Path):
    # Use the python interpreter (which is in our allowlist? no). Pick `git` with
    # a known-slow operation? Easier: extend allowlist for this test only.
    custom_allow = DEFAULT_ALLOWLIST | {Path(sys.executable).name}
    request = RunRequest(
        argv=(sys.executable, "-c", "import time; time.sleep(60)"),
        cwd=tmp_path,
        timeout_seconds=1,
    )
    with pytest.raises(CommandTimeout):
        run_command(request, allowlist=custom_allow)


def test_run_missing_cwd_raises(tmp_path: Path):
    request = RunRequest(argv=("git", "--version"), cwd=tmp_path / "does-not-exist")
    with pytest.raises(FileNotFoundError):
        run_command(request)


def test_run_caps_output_and_kills_subprocess(tmp_path: Path):
    """A producer that streams more than max_output_bytes is killed at the cap."""
    custom_allow = DEFAULT_ALLOWLIST | {Path(sys.executable).name}
    # Print 1 MiB worth of A's then sleep forever; runner should kill at cap.
    program = (
        "import sys, time; "
        "sys.stdout.buffer.write(b'A' * (1024 * 1024)); "
        "sys.stdout.flush(); "
        "time.sleep(60)"
    )
    request = RunRequest(
        argv=(sys.executable, "-c", program),
        cwd=tmp_path,
        timeout_seconds=10,
        max_output_bytes=4096,
    )
    result = run_command(request, allowlist=custom_allow)
    assert len(result.stdout) == 4096
    assert result.truncated_stdout is True
    assert any("output cap" in note for note in result.notes)
    # Kill grace ensures we returned in well under the 60s sleep budget.
    assert result.duration_seconds < 5
