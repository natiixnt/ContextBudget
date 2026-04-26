"""
Subprocess execution for command-output compression.

Runs a shell command in a controlled way, captures stdout and stderr, and returns
the raw bytes for downstream parsing. The runner enforces an allowlist: only
recognised dev-tool binaries can be executed, never arbitrary shell.

Streaming variant lands in M6 - this version buffers the full output, which is
fine for the median command size (<1 MB) and keeps the M1 surface small.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# Commands we are willing to spawn from MCP. Extend as new compressors land.
DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "git",
        "ls",
        "tree",
        "find",
        "grep",
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
    }
)

DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_BYTES = 16 * 1024 * 1024  # 16 MiB hard cap on captured output


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

    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(request.argv),
            cwd=str(cwd),
            env=request.env,
            capture_output=True,
            timeout=request.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise CommandTimeout(
            f"command timed out after {request.timeout_seconds}s: {request.argv[0]}"
        ) from e
    except FileNotFoundError as e:
        # Distinguish missing binary from missing cwd: we already verified
        # cwd above, so this must mean the binary isn't on PATH.
        raise BinaryNotFound(
            f"binary '{request.argv[0]}' not found on PATH"
        ) from e
    duration = time.monotonic() - started

    stdout, truncated_out = _truncate_output(proc.stdout)
    stderr, truncated_err = _truncate_output(proc.stderr)

    notes: list[str] = []
    if truncated_out:
        notes.append(f"stdout truncated to {MAX_OUTPUT_BYTES} bytes")
    if truncated_err:
        notes.append(f"stderr truncated to {MAX_OUTPUT_BYTES} bytes")

    return RunResult(
        argv=request.argv,
        cwd=cwd,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        truncated_stdout=truncated_out,
        truncated_stderr=truncated_err,
        notes=tuple(notes),
    )


def _truncate_output(data: bytes | None) -> tuple[bytes, bool]:
    if not data:
        return b"", False
    if len(data) <= MAX_OUTPUT_BYTES:
        return data, False
    return data[:MAX_OUTPUT_BYTES], True
