"""Run feed - mirror every pack report into ``.redcon/runs/``.

Editor integrations (the VS Code panel and dashboard) can only show
runs they can find on disk. The CLI writes ``run.json`` next to the
repo, but packs triggered through the SDK, the MCP tools or the agent
middleware never produced an artifact, so those runs were invisible.

This module gives every entry point one shared sink: the pipeline
mirrors the full run report into ``.redcon/runs/`` right where the
run happened. Consumers just watch that directory; no polling of
SQLite history (which non-Python tooling cannot read) is needed.

The feed is best-effort by design: a failed write never fails a pack.
Set the ``REDCON_NO_RUN_FEED`` environment variable to disable it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUN_FEED_DIRNAME = "runs"
RUN_FEED_MAX_FILES = 30
RUN_FEED_DISABLE_ENV = "REDCON_NO_RUN_FEED"


def run_feed_dir(repo_path: Path | str) -> Path:
    return Path(repo_path) / ".redcon" / RUN_FEED_DIRNAME


def write_run_feed_artifact(repo_path: Path | str, report: dict[str, Any]) -> Path | None:
    """Write ``report`` (a serialized RunReport) into the feed directory.

    Returns the artifact path, or ``None`` when the feed is disabled or
    the write failed. Never raises: the feed is a side channel and must
    not break the pack that produced the report.
    """
    if os.environ.get(RUN_FEED_DISABLE_ENV):
        return None
    try:
        feed_dir = run_feed_dir(repo_path)
        feed_dir.mkdir(parents=True, exist_ok=True)
        path = _next_artifact_path(feed_dir, str(report.get("generated_at", "")))
        path.write_text(
            json.dumps(report, indent=2, default=str, sort_keys=False),
            encoding="utf-8",
        )
        prune_run_feed(feed_dir)
        return path
    except Exception:
        logger.debug("run feed: artifact write failed", exc_info=True)
        return None


def _next_artifact_path(feed_dir: Path, generated_at: str) -> Path:
    # "2026-07-13T09:01:23.456Z" -> "20260713T090123"; empty/odd inputs
    # fall back to a bare "run" stem instead of failing the write.
    stamp = re.sub(r"[^0-9T]", "", generated_at)[:15]
    base = f"run-{stamp}" if stamp else "run"
    path = feed_dir / f"{base}.json"
    counter = 1
    while path.exists():
        path = feed_dir / f"{base}-{counter}.json"
        counter += 1
    return path


def prune_run_feed(feed_dir: Path, keep: int = RUN_FEED_MAX_FILES) -> None:
    """Keep the newest ``keep`` artifacts, delete the rest."""
    try:
        files = sorted(
            feed_dir.glob("run-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            logger.debug("run feed: could not prune %s", stale, exc_info=True)
