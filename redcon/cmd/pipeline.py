"""
Top-level command-output compression pipeline.

Wires runner + registry + cache + compressor into one call. The MCP tool
`redcon_run` and the planned CLI `redcon run` both go through this entry point.

Cache integration is deliberately optional - callers pass their own backend
if they want persistence. The default in-memory dict per-process is enough
for typical agent sessions where the same `git status` is hit many times.

Log-pointer tier: when the raw subprocess output exceeds
`LOG_POINTER_THRESHOLD_BYTES` (default 1 MiB) the pipeline spills the full
captured bytes to ``.redcon/cmd_runs/<digest>.log`` and returns a
CompressedOutput whose ``schema`` is ``log_pointer`` and whose text is a
one-paragraph summary plus the path. Agents can fetch the full log if the
summary isn't enough; the alternative would be a parser running on a 50 MB
docker build log and producing a still-too-big result.
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
from redcon.cmd.rewriter import rewrite_argv
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

# Outputs above this are spilled to disk; pipeline returns a pointer
# instead of attempting per-compressor parsing. 1 MiB is well above
# typical command output and below the runner's 16 MiB cap.
LOG_POINTER_THRESHOLD_BYTES: int = 1024 * 1024
LOG_POINTER_SUMMARY_TAIL_LINES: int = 30


def compress_command(
    command: str | list[str] | tuple[str, ...],
    *,
    cwd: str | Path = ".",
    hint: BudgetHint | None = None,
    timeout_seconds: int | None = None,
    cache: MutableMapping[str, CompressionReport] | None = None,
    use_default_cache: bool = True,
    record_history: bool = False,
    aliaser=None,
) -> CompressionReport:
    """
    Run a command, parse its output via the matching compressor, and return
    the compressed result. Cached by deterministic key.

    Returns CompressionReport so callers can see cache hit, raw size, duration.
    """
    raw_argv = parse_command(command)
    cwd_path = Path(cwd)
    effective_hint_for_rewrite = hint or BudgetHint(
        remaining_tokens=10_000,
        max_output_tokens=4_000,
        quality_floor=CompressionLevel.COMPACT,
    )
    # Rewriting happens before cache lookup so compact-mode runs cache
    # separately from default runs (different output, different key).
    argv = rewrite_argv(
        raw_argv,
        prefer_compact=effective_hint_for_rewrite.prefer_compact_output,
    )

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

    effective_hint = hint or effective_hint_for_rewrite

    stdout = _neutralise_terminal(run_result.stdout)
    stderr = _neutralise_terminal(run_result.stderr)

    raw_bytes = len(stdout) + len(stderr)
    if raw_bytes > LOG_POINTER_THRESHOLD_BYTES:
        compressed = _spill_to_log(
            stdout,
            stderr,
            argv=argv,
            cwd=cwd_path,
            cache_key=cache_key,
            returncode=run_result.returncode,
            notes=run_result.notes,
        )
    elif compressor is None:
        compressed = _semantic_or_passthrough(
            stdout, stderr, effective_hint
        )
    else:
        ctx = CompressorContext(
            argv=argv,
            cwd=str(cwd_path.resolve()),
            returncode=run_result.returncode,
            hint=effective_hint,
            notes=run_result.notes,
        )
        compressed = compressor.compress(stdout, stderr, ctx)
        compressed = _normalise_whitespace(compressed)
        compressed = _apply_subst_table(compressed)
        compressed = _stamp_invariant_cert(compressed, stdout, compressor)
        if aliaser is not None:
            compressed = _apply_aliaser(compressed, aliaser)

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

    if record_history:
        from redcon.cmd import history

        history.record_run(
            report,
            command=" ".join(argv) if isinstance(command, (list, tuple)) else str(command),
            repo_root=cwd_path,
        )
    return report


def clear_default_cache() -> None:
    """Drop in-memory pipeline cache. Used by tests."""
    _DEFAULT_CACHE.clear()


_TRIPLE_NEWLINE = None  # lazy-compiled below
_COMMA_GAP = None
_COLON_GAP = None


def _normalise_whitespace(output: CompressedOutput) -> CompressedOutput:
    """Collapse 3+ newlines and tighten ', ' / ': ' gaps; re-tokenise on change.

    Each blank line costs roughly one cl100k token. Compressors sometimes
    emit double-blanks at section transitions; collapsing them is free
    quality-wise and cheap. The two extra rewrites cover ', ' to ',' between
    non-space chars and 'word: word' to 'word:word' - both measured to be
    must-preserve-safe in V32 research and net-positive on cl100k tokens.
    Re-counts compressed_tokens after the rewrite so the reported reduction
    stays accurate.
    """
    global _TRIPLE_NEWLINE, _COMMA_GAP, _COLON_GAP
    import re

    if _TRIPLE_NEWLINE is None:
        _TRIPLE_NEWLINE = re.compile(r"\n{3,}")
        _COMMA_GAP = re.compile(r",[ \t]+(?=\S)")
        _COLON_GAP = re.compile(r"(?<=[A-Za-z]):[ \t]+(?=[A-Za-z0-9])")

    cleaned = _TRIPLE_NEWLINE.sub("\n\n", output.text).rstrip()
    cleaned = _COMMA_GAP.sub(",", cleaned)
    cleaned = _COLON_GAP.sub(":", cleaned)
    if cleaned == output.text:
        return output

    from redcon.cmd._tokens_lite import estimate_tokens

    return CompressedOutput(
        text=cleaned,
        level=output.level,
        schema=output.schema,
        original_tokens=output.original_tokens,
        compressed_tokens=estimate_tokens(cleaned),
        must_preserve_ok=output.must_preserve_ok,
        truncated=output.truncated,
        notes=output.notes,
    )


_CSI_RE: bytes | None = None
_OSC_RE: bytes | None = None
_SHORT_ESC_RE: bytes | None = None


def _neutralise_terminal(blob: bytes) -> bytes:
    """Strip ANSI/escape sequences and collapse CR-overwrite.

    Removes CSI (\\x1b[...x), OSC (\\x1b]...BEL or ESC\\), short ESC
    sequences, and BEL bytes. Then collapses CR-overwrite per line,
    keeping only the segment after the last CR. Idempotent on plain
    ASCII; deterministic.
    """
    if not blob:
        return blob
    global _CSI_RE, _OSC_RE, _SHORT_ESC_RE
    import re

    if _CSI_RE is None:
        _CSI_RE = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]")
        _OSC_RE = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
        _SHORT_ESC_RE = re.compile(rb"\x1b[@-Z\\-_]")

    cleaned = _OSC_RE.sub(b"", blob)
    cleaned = _CSI_RE.sub(b"", cleaned)
    cleaned = _SHORT_ESC_RE.sub(b"", cleaned)
    cleaned = cleaned.replace(b"\x07", b"")

    if b"\r" not in cleaned:
        return cleaned
    out_lines: list[bytes] = []
    for line in cleaned.split(b"\n"):
        if b"\r" in line:
            out_lines.append(line.rsplit(b"\r", 1)[-1])
        else:
            out_lines.append(line)
    return b"\n".join(out_lines)


def _apply_aliaser(output: CompressedOutput, aliaser) -> CompressedOutput:
    """Apply session-scoped path aliasing post all other rewrites.

    Re-tokenises after the rewrite so reported counts stay accurate.
    Keeps cache-friendly: caller passes the aliaser, cache holds canonical
    pre-alias text, so identical underlying output replays the same alias
    decisions across hits.
    """
    new_text = aliaser.apply(output.text)
    if new_text == output.text:
        return output

    from redcon.cmd._tokens_lite import estimate_tokens

    return CompressedOutput(
        text=new_text,
        level=output.level,
        schema=output.schema,
        original_tokens=output.original_tokens,
        compressed_tokens=estimate_tokens(new_text),
        must_preserve_ok=output.must_preserve_ok,
        truncated=output.truncated,
        notes=output.notes,
    )


def _stamp_invariant_cert(
    output: CompressedOutput,
    raw_stdout: bytes,
    compressor,
) -> CompressedOutput:
    """Attach mp_sha=<16hex> note for COMPACT/VERBOSE outputs.

    ULTRA is exempt because the BASELINE explicitly allows ULTRA to drop
    facts. Empty must_preserve_patterns produces no cert (no-op).
    """
    if output.level == CompressionLevel.ULTRA:
        return output
    patterns = getattr(compressor, "must_preserve_patterns", ())
    if not patterns:
        return output

    from redcon.cmd.compressors.base import compute_invariant_cert

    raw_text = raw_stdout.decode("utf-8", errors="replace")
    cert = compute_invariant_cert(raw_text, patterns)
    if not cert:
        return output

    return CompressedOutput(
        text=output.text,
        level=output.level,
        schema=output.schema,
        original_tokens=output.original_tokens,
        compressed_tokens=output.compressed_tokens,
        must_preserve_ok=output.must_preserve_ok,
        truncated=output.truncated,
        notes=output.notes + (f"mp_sha={cert}",),
    )


def _apply_subst_table(output: CompressedOutput) -> CompressedOutput:
    """Tokenizer-aware multi-token substitution.

    Walks the per-schema SUBST_TABLE and accepts each rewrite only when
    re-tokenisation shows a strict decrease. Monotone-safe by construction.
    Skipped at VERBOSE because that tier is the human-readable surface.
    """
    if output.level == CompressionLevel.VERBOSE:
        return output

    from redcon.cmd._subst_table import SUBST_TABLE
    from redcon.cmd._tokens_lite import estimate_tokens

    text = output.text
    cur = output.compressed_tokens or estimate_tokens(text)
    for sub in SUBST_TABLE:
        if sub.scope is not None and output.schema not in sub.scope:
            continue
        if sub.orig not in text:
            continue
        cand = text.replace(sub.orig, sub.repl)
        ct = estimate_tokens(cand)
        if ct < cur:
            text, cur = cand, ct

    if text == output.text:
        return output
    return CompressedOutput(
        text=text,
        level=output.level,
        schema=output.schema,
        original_tokens=output.original_tokens,
        compressed_tokens=cur,
        must_preserve_ok=output.must_preserve_ok,
        truncated=output.truncated,
        notes=output.notes,
    )


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


def _spill_to_log(
    stdout: bytes,
    stderr: bytes,
    *,
    argv: tuple[str, ...],
    cwd: Path,
    cache_key: CommandCacheKey,
    returncode: int,
    notes: tuple[str, ...],
) -> CompressedOutput:
    """Write full output to ``.redcon/cmd_runs/<digest>.log`` and return a pointer."""
    from redcon.cmd._tokens_lite import estimate_tokens

    log_dir = (cwd / ".redcon" / "cmd_runs").resolve()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("could not create log directory %s: %s", log_dir, e)

    log_path = log_dir / f"{cache_key.short()}.log"
    try:
        with open(log_path, "wb") as f:
            if stdout:
                f.write(b"--- stdout ---\n")
                f.write(stdout)
                if not stdout.endswith(b"\n"):
                    f.write(b"\n")
            if stderr:
                f.write(b"--- stderr ---\n")
                f.write(stderr)
        wrote = True
    except OSError as e:
        logger.warning("could not write spill log %s: %s", log_path, e)
        wrote = False

    raw_text = stdout.decode("utf-8", errors="replace") + (
        "\n" + stderr.decode("utf-8", errors="replace") if stderr else ""
    )
    raw_tokens = estimate_tokens(raw_text)
    raw_lines = raw_text.count("\n") + 1
    raw_kb = (len(stdout) + len(stderr)) / 1024.0

    tail_lines = raw_text.splitlines()[-LOG_POINTER_SUMMARY_TAIL_LINES:]
    tail = "\n".join(tail_lines)

    cmd_str = " ".join(argv)
    if wrote:
        try:
            log_path_display = log_path.relative_to(cwd)
        except ValueError:
            log_path_display = log_path
        summary = (
            f"command output spilled to disk: {raw_lines} lines / "
            f"{raw_kb:.1f} KiB exceeds in-context budget.\n"
            f"command: {cmd_str}\n"
            f"returncode: {returncode}\n"
            f"log: {log_path_display}\n"
            f"--- last {len(tail_lines)} lines ---\n"
            f"{tail}"
        )
    else:
        summary = (
            f"command output too large for context "
            f"({raw_lines} lines / {raw_kb:.1f} KiB) and the spill log "
            f"could not be written.\ncommand: {cmd_str}\n"
            f"returncode: {returncode}\n"
            f"--- last {len(tail_lines)} lines ---\n{tail}"
        )

    compressed = estimate_tokens(summary)
    return CompressedOutput(
        text=summary,
        level=CompressionLevel.ULTRA,
        schema="log_pointer",
        original_tokens=raw_tokens,
        compressed_tokens=compressed,
        must_preserve_ok=True,
        truncated=True,
        notes=notes,
    )


def _semantic_or_passthrough(
    stdout: bytes, stderr: bytes, hint: BudgetHint
) -> CompressedOutput:
    """When opt-in semantic_fallback is set and llmlingua is installed,
    attempt LLMLingua-2 compression on the raw output. Falls through to
    plain passthrough on any guard failure.
    """
    if hint.semantic_fallback:
        try:
            from redcon.cmd.semantic_fallback import maybe_compress

            text = stdout.decode("utf-8", errors="replace")
            if stderr:
                err_text = stderr.decode("utf-8", errors="replace").strip()
                if err_text:
                    text = (
                        f"{text}\n--- stderr ---\n{err_text}" if text else err_text
                    )
            attempt = maybe_compress(text)
            if attempt is not None:
                return attempt
        except Exception as e:
            logger.debug("semantic fallback skipped: %s", e)
    return _passthrough(stdout, stderr, hint)


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
