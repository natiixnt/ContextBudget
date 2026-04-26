"""
Content-addressed cache key for command outputs.

The key encodes everything that can change the canonical command output:
  - argv (the command itself, including flags)
  - cwd (canonical absolute path)
  - git HEAD sha if cwd is inside a git repo (catches commits the agent made)
  - mtimes of any extra paths the compressor declares as inputs

A second `redcon_run` for the same command in the same state returns the
cached compressed output without re-parsing.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CommandCacheKey:
    """Stable hash of (argv, cwd, repo_state, watched paths)."""

    digest: str
    cwd: str
    argv: tuple[str, ...]
    git_head: str | None
    watched_count: int

    def short(self) -> str:
        return self.digest[:16]


def build_cache_key(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    watched_paths: tuple[Path, ...] = (),
    extra: str = "",
) -> CommandCacheKey:
    """Compute a deterministic cache key for a command invocation."""
    cwd_canonical = str(cwd.resolve())
    git_head = _read_git_head(cwd)
    watched_signature = _watched_signature(watched_paths)

    hasher = hashlib.sha256()
    hasher.update(b"v1\n")
    hasher.update(cwd_canonical.encode("utf-8"))
    hasher.update(b"\0")
    for arg in argv:
        hasher.update(arg.encode("utf-8"))
        hasher.update(b"\0")
    hasher.update((git_head or "").encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(watched_signature.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(extra.encode("utf-8"))

    return CommandCacheKey(
        digest=hasher.hexdigest(),
        cwd=cwd_canonical,
        argv=tuple(argv),
        git_head=git_head,
        watched_count=len(watched_paths),
    )


def _read_git_head(cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    head = proc.stdout.strip()
    return head or None


def _watched_signature(paths: tuple[Path, ...]) -> str:
    if not paths:
        return ""
    parts: list[str] = []
    for path in sorted(paths):
        try:
            stat = path.stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{path}:missing")
    return "|".join(parts)
