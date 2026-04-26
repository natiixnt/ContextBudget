"""Tests for redcon run / cmd-bench / cmd-quality CLI subcommands and history."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from redcon.cli import build_parser
from redcon.cmd import history
from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import CompressorContext
from redcon.cmd.compressors.git_diff import GitDiffCompressor
from redcon.cmd.pipeline import CompressionReport, clear_default_cache, compress_command
from redcon.cmd.types import CompressionLevel


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_default_cache()
    yield
    clear_default_cache()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A tiny git repo with one staged change so diff/log produce real output."""
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
    (tmp_path / "foo.py").write_text("a = 1\nb = 999\nc = 3\nd = 4\n")
    return tmp_path


# --- redcon run ---


def _run_cli(parser, argv: list[str]) -> int:
    args = parser.parse_args(argv)
    return int(args.func(args))


def test_run_subcommand_exits_zero_on_clean_diff(
    git_repo: Path, capsys: pytest.CaptureFixture
):
    parser = build_parser()
    rc = _run_cli(
        parser,
        [
            "run",
            "git diff",
            "--cwd",
            str(git_repo),
            "--max-output-tokens",
            "2000",
            "--remaining-tokens",
            "10000",
            "--quality-floor",
            "compact",
            "--no-history",
        ],
    )
    captured = capsys.readouterr()
    assert rc == 0
    # The compressed text goes to stdout, the one-line summary to stderr.
    assert "foo.py" in captured.out
    assert "git_diff" in captured.err


def test_run_subcommand_json_output_is_parseable(
    git_repo: Path, capsys: pytest.CaptureFixture
):
    parser = build_parser()
    rc = _run_cli(
        parser,
        [
            "run",
            "git status --porcelain=v1 -b",
            "--cwd",
            str(git_repo),
            "--json",
            "--no-history",
        ],
    )
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["schema"] == "git_status"
    assert "text" in payload
    assert "reduction_pct" in payload


def test_run_subcommand_rejects_invalid_quality_floor(
    capsys: pytest.CaptureFixture,
):
    parser = build_parser()
    # argparse already enforces choices, but the handler also has its own
    # validation in case the CLI is invoked programmatically with a bad value.
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "git diff", "--quality-floor", "invalid"])


def test_run_subcommand_blocks_unallowed_command(
    git_repo: Path, capsys: pytest.CaptureFixture
):
    parser = build_parser()
    rc = _run_cli(
        parser,
        ["run", "rm -rf /", "--cwd", str(git_repo), "--no-history"],
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "not in the allowlist" in captured.err.lower() or "Error" in captured.err


# --- redcon cmd-bench ---


def test_cmd_bench_json_emits_valid_json(capsys: pytest.CaptureFixture):
    parser = build_parser()
    rc = _run_cli(parser, ["cmd-bench", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert all("schema" in entry and "levels" in entry for entry in data)


def test_cmd_bench_markdown_default(capsys: pytest.CaptureFixture):
    parser = build_parser()
    rc = _run_cli(parser, ["cmd-bench"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fixture" in out
    assert "verbose" in out
    assert "compact" in out
    assert "ultra" in out


# --- redcon cmd-quality ---


def test_cmd_quality_passes_on_current_registry(capsys: pytest.CaptureFixture):
    parser = build_parser()
    rc = _run_cli(parser, ["cmd-quality"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "passed" in out


# --- history module ---


def test_history_record_and_read(tmp_path: Path):
    db_path = tmp_path / "history.db"
    history.ensure_schema(db_path)

    # Build a real CompressionReport via the pipeline so we exercise the path.
    raw = b"diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    comp = GitDiffCompressor()
    ctx = CompressorContext(
        argv=("git", "diff"),
        cwd=str(tmp_path),
        returncode=0,
        hint=BudgetHint(
            remaining_tokens=10000,
            max_output_tokens=4000,
            quality_floor=CompressionLevel.COMPACT,
        ),
    )
    output = comp.compress(raw, b"", ctx)
    from redcon.cmd.cache import build_cache_key

    cache_key = build_cache_key(("git", "diff"), tmp_path)
    report = CompressionReport(
        output=output,
        cache_key=cache_key,
        cache_hit=False,
        raw_stdout_bytes=len(raw),
        raw_stderr_bytes=0,
        duration_seconds=0.001,
        returncode=0,
    )

    rid = history.record_run(report, command="git diff", db_path=db_path)
    assert rid is not None and rid > 0

    rows = history.recent_runs(db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["schema"] == "git_diff"
    assert rows[0]["command"] == "git diff"

    totals = history.aggregate_savings(db_path=db_path)
    assert totals["runs"] == 1
    assert totals["raw_tokens"] >= 0
    assert totals["compressed_tokens"] >= 0


def test_history_swallows_errors_when_db_unwritable(tmp_path: Path):
    """If the DB path can't be created, record_run returns None instead of raising."""
    bad_path = tmp_path / "no_such_dir" / "nested" / "history.db"
    # Make the parent unwritable by pointing into a file-not-dir.
    blocker = tmp_path / "blocker"
    blocker.write_text("file, not dir")
    impossible = blocker / "history.db"

    raw = b"diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    comp = GitDiffCompressor()
    ctx = CompressorContext(
        argv=("git", "diff"),
        cwd=str(tmp_path),
        returncode=0,
        hint=BudgetHint(
            remaining_tokens=10000,
            max_output_tokens=4000,
            quality_floor=CompressionLevel.COMPACT,
        ),
    )
    output = comp.compress(raw, b"", ctx)
    from redcon.cmd.cache import build_cache_key

    cache_key = build_cache_key(("git", "diff"), tmp_path)
    report = CompressionReport(
        output=output,
        cache_key=cache_key,
        cache_hit=False,
        raw_stdout_bytes=len(raw),
        raw_stderr_bytes=0,
        duration_seconds=0.001,
        returncode=0,
    )
    # Should not raise; should return None.
    result = history.record_run(report, command="git diff", db_path=impossible)
    assert result is None


def test_pipeline_record_history_writes_row(git_repo: Path, monkeypatch):
    """compress_command(record_history=True) should produce one history row."""
    db_path = git_repo / ".redcon" / "history.db"
    report = compress_command(
        "git diff",
        cwd=git_repo,
        hint=BudgetHint(remaining_tokens=10000, max_output_tokens=2000),
        record_history=True,
    )
    assert report.output.schema == "git_diff"
    rows = history.recent_runs(db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["schema"] == "git_diff"
    assert rows[0]["command"] == "git diff"
