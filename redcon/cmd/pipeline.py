"""
Top-level command-output compression pipeline.

Wires runner + registry + cache + compressor into one call. The MCP tool
`redcon_run` and the planned CLI `redcon run` both go through this entry point.

Cache integration is deliberately optional - callers pass their own backend
if they want persistence. The default in-memory dict per-process is enough
for typical agent sessions where the same `git status` is hit many times.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping

from redcon.cmd.budget import BudgetHint
from redcon.cmd.cache import CommandCacheKey, build_cache_key
from redcon.cmd.compressors.base import CompressorContext
from redcon.cmd.registry import detect_compressor
from redcon.cmd.runner import (
    CommandNotAllowed,
    CommandTimeout,
    RunRequest,
    parse_command,
    run_command,
)
from redcon.cmd.types import CompressedOutput, CompressionLevel

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompressionReport:
    """Full outcome of a `redcon_run` call. Wraps CompressedOutput with metadata."""

    output: CompressedOutput
    cache_key: CommandCacheKey
    cache_hit: bool
    raw_stdout_bytes: int
    raw_stderr_bytes: int
    duration_seconds: float
    returncode: int


_DEFAULT_CACHE: MutableMapping[str, CompressionReport] = {}


def compress_command(
    command: str | list[str] | tuple[str, ...],
    *,
    cwd: str | Path = ".",
    hint: BudgetHint | None = None,
    timeout_seconds: int | None = None,
    cache: MutableMapping[str, CompressionReport] | None = None,
    use_default_cache: bool = True,
) -> CompressionReport:
    """
    Run a command, parse its output via the matching compressor, and return
    the compressed result. Cached by deterministic key.

    Returns CompressionReport so callers can see cache hit, raw size, duration.
    """
    argv = parse_command(command)
    cwd_path = Path(cwd)

    effective_cache = cache if cache is not None else (
        _DEFAULT_CACHE if use_default_cache else None
    )

    cache_key = build_cache_key(argv, cwd_path)
    if effective_cache is not None:
        cached = effective_cache.get(cache_key.digest)
        if cached is not None:
            logger.debug("redcon_run cache hit %s argv=%s", cache_key.short(), argv)
            return _with_cache_hit(cached)

    compressor = detect_compressor(argv)
    request = RunRequest(
        argv=argv,
        cwd=cwd_path,
        timeout_seconds=timeout_seconds or 120,
    )

    try:
        run_result = run_command(request)
    except CommandNotAllowed:
        raise
    except CommandTimeout:
        raise

    effective_hint = hint or BudgetHint(
        remaining_tokens=10_000,
        max_output_tokens=4_000,
        quality_floor=CompressionLevel.COMPACT,
    )

    if compressor is None:
        compressed = _passthrough(run_result.stdout, run_result.stderr, effective_hint)
    else:
        ctx = CompressorContext(
            argv=argv,
            cwd=str(cwd_path.resolve()),
            returncode=run_result.returncode,
            hint=effective_hint,
            notes=run_result.notes,
        )
        compressed = compressor.compress(
            run_result.stdout, run_result.stderr, ctx
        )

    report = CompressionReport(
        output=compressed,
        cache_key=cache_key,
        cache_hit=False,
        raw_stdout_bytes=len(run_result.stdout),
        raw_stderr_bytes=len(run_result.stderr),
        duration_seconds=run_result.duration_seconds,
        returncode=run_result.returncode,
    )
    if effective_cache is not None:
        effective_cache[cache_key.digest] = report
    return report


def clear_default_cache() -> None:
    """Drop in-memory pipeline cache. Used by tests."""
    _DEFAULT_CACHE.clear()


def _with_cache_hit(report: CompressionReport) -> CompressionReport:
    return CompressionReport(
        output=report.output,
        cache_key=report.cache_key,
        cache_hit=True,
        raw_stdout_bytes=report.raw_stdout_bytes,
        raw_stderr_bytes=report.raw_stderr_bytes,
        duration_seconds=report.duration_seconds,
        returncode=report.returncode,
    )


def _passthrough(
    stdout: bytes, stderr: bytes, hint: BudgetHint
) -> CompressedOutput:
    """When no compressor matches, return the raw text clipped to the budget."""
    from redcon.core.tokens import estimate_tokens

    text = stdout.decode("utf-8", errors="replace")
    if stderr:
        err_text = stderr.decode("utf-8", errors="replace").strip()
        if err_text:
            text = f"{text}\n--- stderr ---\n{err_text}" if text else err_text

    raw_tokens = estimate_tokens(text)
    cap = hint.max_output_tokens
    if cap > 0 and raw_tokens > cap:
        approx_chars = cap * 4
        text = text[:approx_chars] + "\n... [output truncated to fit budget]"
        truncated = True
    else:
        truncated = False
    return CompressedOutput(
        text=text,
        level=CompressionLevel.VERBOSE,
        schema="raw_passthrough",
        original_tokens=raw_tokens,
        compressed_tokens=estimate_tokens(text),
        must_preserve_ok=True,
        truncated=truncated,
    )
