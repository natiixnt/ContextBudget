from __future__ import annotations

import json
from pathlib import Path

import pytest

from redcon.core.pipeline import run_pack
from redcon.core.run_feed import (
    RUN_FEED_DISABLE_ENV,
    prune_run_feed,
    run_feed_dir,
    write_run_feed_artifact,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_pack_writes_feed_artifact(tmp_path: Path) -> None:
    """Every pack run leaves a full report in .redcon/runs/.

    This is the plug and play contract for editor integrations: the
    pipeline is the chokepoint every entry point (CLI, SDK, MCP tools,
    middleware) flows through, so a pack made by an agent in Cursor or
    Claude Code is visible to the VS Code panel without any manual step.
    """
    _write(tmp_path / "src" / "auth.py", "def auth():\n    return True\n" * 10)

    run_pack("add rate limiting", repo=tmp_path, max_tokens=500)

    artifacts = list(run_feed_dir(tmp_path).glob("run-*.json"))
    assert len(artifacts) == 1
    data = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert data["command"] == "pack"
    assert data["task"] == "add rate limiting"
    assert "estimated_saved_tokens" in data["budget"]
    assert data["generated_at"]


def test_feed_disabled_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUN_FEED_DISABLE_ENV, "1")
    _write(tmp_path / "src" / "auth.py", "def auth():\n    return True\n")

    run_pack("add rate limiting", repo=tmp_path, max_tokens=500)

    assert not run_feed_dir(tmp_path).exists()


def test_feed_respects_history_disabled(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def auth():\n    return True\n")
    _write(
        tmp_path / "redcon.toml",
        "[cache]\nrun_history_enabled = false\n",
    )

    run_pack("add rate limiting", repo=tmp_path, max_tokens=500)

    assert not run_feed_dir(tmp_path).exists()


def test_feed_artifacts_get_unique_names(tmp_path: Path) -> None:
    report = {"generated_at": "2026-07-13T09:00:00Z", "command": "pack"}
    first = write_run_feed_artifact(tmp_path, report)
    second = write_run_feed_artifact(tmp_path, report)

    assert first is not None and second is not None
    assert first != second
    assert first.parent == second.parent == run_feed_dir(tmp_path)


def test_feed_prunes_to_newest(tmp_path: Path) -> None:
    feed = run_feed_dir(tmp_path)
    feed.mkdir(parents=True)
    for i in range(12):
        artifact = feed / f"run-2026071310{i:02d}.json"
        artifact.write_text("{}", encoding="utf-8")
        # Distinct mtimes so newest-first ordering is deterministic.
        import os

        os.utime(artifact, (1000 + i, 1000 + i))

    prune_run_feed(feed, keep=5)

    remaining = sorted(p.name for p in feed.glob("run-*.json"))
    assert len(remaining) == 5
    assert remaining == [f"run-2026071310{i:02d}.json" for i in range(7, 12)]


def test_feed_write_failure_is_silent(tmp_path: Path) -> None:
    # A file where the .redcon directory should be makes mkdir fail;
    # the feed must swallow it and return None instead of raising.
    (tmp_path / ".redcon").write_text("not a directory", encoding="utf-8")

    result = write_run_feed_artifact(tmp_path, {"generated_at": "x"})

    assert result is None
