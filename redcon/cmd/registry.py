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
    from redcon.cmd.compressors import (  # noqa: F401
        cargo_test_compressor,
        git_diff,
        git_log,
        git_status,
        go_test_compressor,
        grep_compressor,
        listing_compressor,
        npm_test_compressor,
        pytest_compressor,
    )

    register_compressor(git_diff.GitDiffCompressor())
    register_compressor(git_status.GitStatusCompressor())
    register_compressor(git_log.GitLogCompressor())
    register_compressor(pytest_compressor.PytestCompressor())
    register_compressor(cargo_test_compressor.CargoTestCompressor())
    register_compressor(npm_test_compressor.NpmTestCompressor())
    register_compressor(go_test_compressor.GoTestCompressor())
    register_compressor(grep_compressor.GrepCompressor())
    register_compressor(listing_compressor.LsCompressor())
    register_compressor(listing_compressor.TreeCompressor())
    register_compressor(listing_compressor.FindCompressor())


_bootstrap()
