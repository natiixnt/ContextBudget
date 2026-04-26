"""
Registry of available compressors.

Compressors register themselves at import time. `detect_compressor(argv)`
walks the registry and returns the first compressor that claims the argv.
Order is insertion order - more specific compressors should register first.
"""

from __future__ import annotations

from redcon.cmd.compressors.base import Compressor

_REGISTRY: list[Compressor] = []


def register_compressor(compressor: Compressor) -> None:
    """Append a compressor to the registry. Idempotent on identity."""
    for existing in _REGISTRY:
        if existing is compressor:
            return
    _REGISTRY.append(compressor)


def detect_compressor(argv: tuple[str, ...]) -> Compressor | None:
    """Return the first registered compressor that matches the argv."""
    for compressor in _REGISTRY:
        if compressor.matches(argv):
            return compressor
    return None


def registered_schemas() -> tuple[str, ...]:
    """List schemas for registered compressors. Useful for diagnostics."""
    return tuple(c.schema for c in _REGISTRY)


def _bootstrap() -> None:
    """Import all built-in compressors so they self-register."""
    from redcon.cmd.compressors import git_diff, git_log, git_status  # noqa: F401

    register_compressor(git_diff.GitDiffCompressor())
    register_compressor(git_status.GitStatusCompressor())
    register_compressor(git_log.GitLogCompressor())


_bootstrap()
