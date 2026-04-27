"""
Subprocess execution for command-output compression.

Runs a shell command via Popen, reading stdout/stderr in 64 KiB chunks so we
can enforce a memory ceiling and terminate the subprocess as soon as the
configured byte cap is reached - instead of passively letting it produce
gigabytes the OS would then have to flush through pipes.

The runner enforces an allowlist: only recognised dev-tool binaries can be
executed, never arbitrary shell. Per-compressor streaming (turning parsers
into incremental state machines) is intentionally NOT here - the cost of
that refactor isn't justified by current parse times (sub-2ms even on
huge outputs).
"""

from __future__ import annotations

import logging
import os
import select
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_READ_CHUNK_BYTES = 64 * 1024
_KILL_GRACE_SECONDS = 1.0


# Commands we are willing to spawn from MCP. Extend as new compressors land.
DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "git",
        "ls",
        "tree",
        "find",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "pytest",
        "cargo",
        "npm",
        "pnpm",
        "yarn",
        "go",
        "ruff",
        "mypy",
        "tsc",
        "eslint",
        "docker",
        "podman",
        "pip",
        "kubectl",
        "vitest",
        "jest",
        "jq",
        "python",
        "python3",
        "npx",
        "py-spy",
        "perf",
        "flamegraph.pl",
        "cat",
        "tail",
        "less",
        "more",
        "journalctl",
        "coverage",
    }
)

DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_BYTES = 16 * 1024 * 1024  # 16 MiB hard cap on captured output


# Env vars suppressing colour / progress-bar output for tools we wrap. Caller
# overrides win (request.env merged on top). ANSI escapes tokenise badly on
# cl100k and add no agent value, so we silence them at source rather than
# stripping them post-hoc when possible.
DEFAULT_COLOR_OFF_ENV: dict[str, str] = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "FORCE_COLOR": "0",
    "CLICOLOR": "0",
    "CLICOLOR_FORCE": "0",
    "PY_COLORS": "0",
    "PYTEST_ADDOPTS": "--color=no",
    "MYPY_FORCE_COLOR": "0",
    "RUFF_NO_COLOR": "1",
    "DOCKER_CLI_HINTS": "false",
    "NPM_CONFIG_COLOR": "false",
}


class CommandNotAllowed(RuntimeError):
    pass


class CommandTimeout(RuntimeError):
    pass


class BinaryNotFound(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunRequest:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str] | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = MAX_OUTPUT_BYTES


@dataclass(frozen=True, slots=True)
class RunResult:
    argv: tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    truncated_stdout: bool = False
    truncated_stderr: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


def parse_command(command: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Parse a command string into argv. Accepts str, list, or tuple."""
    if isinstance(command, (list, tuple)):
        argv = tuple(str(a) for a in command)
    else:
        argv = tuple(shlex.split(command))
    if not argv:
        raise ValueError("command is empty")
    return argv


def run_command(
    request: RunRequest,
    *,
    allowlist: frozenset[str] = DEFAULT_ALLOWLIST,
) -> RunResult:
    """Execute the command, return captured output. Raises if not allowlisted."""
    if not request.argv:
        raise ValueError("argv is empty")
    binary = Path(request.argv[0]).name
    if binary not in allowlist:
        raise CommandNotAllowed(
            f"command '{binary}' is not in the allowlist. "
            f"Allowed: {sorted(allowlist)}"
        )

    cwd = request.cwd.resolve()
    if not cwd.is_dir():
        raise FileNotFoundError(f"cwd does not exist: {cwd}")

    cap = max(1, request.max_output_bytes)

    started = time.monotonic()
    spawn_env: dict[str, str] = dict(os.environ)
    spawn_env.update(DEFAULT_COLOR_OFF_ENV)
    if request.env is not None:
        spawn_env.update(request.env)
    try:
        proc = subprocess.Popen(
            list(request.argv),
            cwd=str(cwd),
            env=spawn_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise BinaryNotFound(
            f"binary '{request.argv[0]}' not found on PATH"
        ) from e

    stdout_buf = bytearray()
    stderr_buf = bytearray()
    truncated_stdout = False
    truncated_stderr = False
    deadline = started + request.timeout_seconds
    cap_hit_reason: str | None = None
    stdout_eof = proc.stdout is None
    stderr_eof = proc.stderr is None

    try:
        while not (stdout_eof and stderr_eof):
            now = time.monotonic()
            if now >= deadline:
                _terminate(proc)
                raise CommandTimeout(
                    f"command timed out after {request.timeout_seconds}s: "
                    f"{request.argv[0]}"
                )

            streams = []
            if not stdout_eof and proc.stdout is not None:
                streams.append(proc.stdout)
            if not stderr_eof and proc.stderr is not None:
                streams.append(proc.stderr)
            ready = _select_ready(streams, deadline - now)

            if proc.stdout in ready and not stdout_eof:
                chunk = _read_chunk(proc.stdout)
                if not chunk:
                    stdout_eof = True
                else:
                    truncated_stdout, capped = _append_capped(
                        stdout_buf, chunk, cap, truncated_stdout
                    )
                    if capped and cap_hit_reason is None:
                        cap_hit_reason = "stdout"
            if proc.stderr in ready and not stderr_eof:
                chunk = _read_chunk(proc.stderr)
                if not chunk:
                    stderr_eof = True
                else:
                    truncated_stderr, capped = _append_capped(
                        stderr_buf, chunk, cap, truncated_stderr
                    )
                    if capped and cap_hit_reason is None:
                        cap_hit_reason = "stderr"

            if cap_hit_reason is not None:
                # Stop draining further bytes; kill the subprocess.
                _terminate(proc)
                break
    finally:
        _drain_remaining(proc, stdout_buf, stderr_buf, cap, truncated_stdout, truncated_stderr)

    returncode = proc.wait(timeout=_KILL_GRACE_SECONDS) if proc.returncode is None else proc.returncode
    duration = time.monotonic() - started

    notes: list[str] = []
    if truncated_stdout:
        notes.append(f"stdout truncated to {cap} bytes")
    if truncated_stderr:
        notes.append(f"stderr truncated to {cap} bytes")
    if cap_hit_reason is not None:
        notes.append(f"output cap reached on {cap_hit_reason}, subprocess killed")

    return RunResult(
        argv=request.argv,
        cwd=cwd,
        returncode=returncode,
        stdout=bytes(stdout_buf),
        stderr=bytes(stderr_buf),
        duration_seconds=duration,
        truncated_stdout=truncated_stdout,
        truncated_stderr=truncated_stderr,
        notes=tuple(notes),
    )


def _select_ready(streams: list, timeout: float) -> set:
    """Block up to `timeout` seconds waiting for any of the pipes to be readable."""
    if not streams:
        return set()
    # Cap the per-iteration wait so we still check the deadline regularly.
    ready, _, _ = select.select(streams, [], [], min(0.5, max(0.0, timeout)))
    return set(ready)


def _read_chunk(stream) -> bytes:
    try:
        return stream.read1(_READ_CHUNK_BYTES) if hasattr(stream, "read1") else stream.read(_READ_CHUNK_BYTES)
    except OSError:
        return b""


def _append_capped(
    buf: bytearray,
    chunk: bytes,
    cap: int,
    already_truncated: bool,
) -> tuple[bool, bool]:
    """Append chunk respecting cap. Returns (truncated_flag, just_hit_cap)."""
    if already_truncated:
        return True, False
    remaining = cap - len(buf)
    if remaining <= 0:
        return True, True
    if len(chunk) <= remaining:
        buf.extend(chunk)
        return False, False
    buf.extend(chunk[:remaining])
    return True, True


def _drain_remaining(
    proc: subprocess.Popen,
    stdout_buf: bytearray,
    stderr_buf: bytearray,
    cap: int,
    truncated_stdout: bool,
    truncated_stderr: bool,
) -> None:
    """After the main loop exits, slurp any pending bytes still in pipes."""
    for stream, buf, flag in (
        (proc.stdout, stdout_buf, truncated_stdout),
        (proc.stderr, stderr_buf, truncated_stderr),
    ):
        if stream is None:
            continue
        try:
            remaining = stream.read()
        except (OSError, ValueError):
            remaining = b""
        if remaining:
            _append_capped(buf, remaining, cap, flag)
        try:
            stream.close()
        except OSError:
            pass


def _terminate(proc: subprocess.Popen) -> None:
    """Send SIGTERM, then SIGKILL if the subprocess doesn't exit quickly."""
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("subprocess did not exit after kill: pid=%s", proc.pid)
