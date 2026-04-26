"""Tests for redcon.cmd.pipeline and the redcon_run MCP tool."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from redcon.cmd import (
    BudgetHint,
    CommandNotAllowed,
    CompressionLevel,
    clear_default_cache,
    compress_command,
)
from redcon.cmd.cache import build_cache_key
from redcon.mcp.tools import tool_run


@pytest.fixture(autouse=True)
def _clear_pipeline_cache():
    clear_default_cache()
    yield
    clear_default_cache()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real on-disk git repo with one commit and one modified file."""
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(tmp_path),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True
    )
    (tmp_path / "foo.py").write_text("a = 1\nb = 2\nc = 3\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"], cwd=str(tmp_path), check=True
    )
    # Now produce a diff:
    (tmp_path / "foo.py").write_text("a = 1\nb = 999\nc = 3\nd = 4\n")
    return tmp_path


def test_compress_command_git_diff(git_repo: Path):
    report = compress_command(
        "git diff",
        cwd=git_repo,
        hint=BudgetHint(remaining_tokens=10_000, max_output_tokens=2_000),
    )
    assert report.output.schema == "git_diff"
    assert "foo.py" in report.output.text
    assert report.returncode == 0


def test_compress_command_git_status(git_repo: Path):
    report = compress_command(
        "git status --porcelain=v1 -b",
        cwd=git_repo,
        hint=BudgetHint(remaining_tokens=10_000, max_output_tokens=2_000),
    )
    assert report.output.schema == "git_status"
    assert "foo.py" in report.output.text


def test_compress_command_git_log(git_repo: Path):
    report = compress_command(
        "git log",
        cwd=git_repo,
        hint=BudgetHint(remaining_tokens=10_000, max_output_tokens=2_000),
    )
    assert report.output.schema == "git_log"
    assert "initial" in report.output.text


def test_compress_command_cache_hit(git_repo: Path):
    first = compress_command("git diff", cwd=git_repo)
    second = compress_command("git diff", cwd=git_repo)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.cache_key.digest == second.cache_key.digest


def test_compress_command_cache_invalidates_on_change(git_repo: Path):
    first = compress_command("git diff", cwd=git_repo)
    # Different argv -> different key.
    second = compress_command("git diff HEAD", cwd=git_repo)
    assert first.cache_key.digest != second.cache_key.digest


def test_compress_command_blocks_unallowed(git_repo: Path):
    with pytest.raises(CommandNotAllowed):
        compress_command("nonexistent_evil_cmd --foo", cwd=git_repo)


def test_compress_command_passthrough_when_no_compressor(git_repo: Path, monkeypatch):
    # Force a passthrough by stubbing detect_compressor to return None.
    from redcon.cmd import pipeline

    monkeypatch.setattr(pipeline, "detect_compressor", lambda _argv: None)
    report = compress_command(
        "git status", cwd=git_repo,
        hint=BudgetHint(remaining_tokens=10_000, max_output_tokens=4_000),
    )
    assert report.output.schema == "raw_passthrough"


def test_build_cache_key_stable(tmp_path: Path):
    a = build_cache_key(("git", "diff"), tmp_path)
    b = build_cache_key(("git", "diff"), tmp_path)
    assert a.digest == b.digest


def test_build_cache_key_distinguishes_argv(tmp_path: Path):
    a = build_cache_key(("git", "diff"), tmp_path)
    b = build_cache_key(("git", "diff", "HEAD"), tmp_path)
    assert a.digest != b.digest


# --- MCP tool_run integration ---


def test_tool_run_happy_path(git_repo: Path):
    result = tool_run(
        command="git diff",
        cwd=str(git_repo),
        max_output_tokens=2_000,
        remaining_tokens=10_000,
        quality_floor="compact",
    )
    assert "error" not in result
    assert result["schema"] == "git_diff"
    assert "foo.py" in result["text"]
    assert result["original_tokens"] >= result["compressed_tokens"]
    assert result["reduction_pct"] >= 0
    assert result["cache_hit"] is False


def test_tool_run_caches_second_call(git_repo: Path):
    first = tool_run(command="git status", cwd=str(git_repo))
    second = tool_run(command="git status", cwd=str(git_repo))
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True


def test_tool_run_rejects_unallowed():
    result = tool_run(command="rm -rf /")
    assert "error" in result
    assert result.get("kind") == "not_allowed"


def test_tool_run_validates_quality_floor():
    result = tool_run(command="git status", quality_floor="ridiculous")
    assert "error" in result


def test_tool_run_empty_command():
    result = tool_run(command="   ")
    assert "error" in result


def test_tool_run_quality_floor_verbose_keeps_detail(git_repo: Path):
    result = tool_run(
        command="git diff",
        cwd=str(git_repo),
        quality_floor="verbose",
        remaining_tokens=100_000,
        max_output_tokens=10_000,
    )
    assert result["level"] == CompressionLevel.VERBOSE.value
