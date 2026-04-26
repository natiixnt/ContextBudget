"""
redcon.cmd - command output compression pipeline.

Sister module to redcon.compressors (which compresses source files for a task).
This module compresses the *output* of shell commands like `git diff`, `pytest`,
`grep`, etc. before it reaches the LLM context.

Pure-Python implementation. Native Rust acceleration is wired in via the optional
`redcon_cmd` companion package and falls back transparently to this code.
"""

from redcon.cmd.budget import BudgetHint, select_level
from redcon.cmd.cache import CommandCacheKey, build_cache_key
from redcon.cmd.pipeline import (
    CompressionReport,
    clear_default_cache,
    compress_command,
)
from redcon.cmd.registry import detect_compressor, register_compressor
from redcon.cmd.runner import (
    CommandNotAllowed,
    CommandTimeout,
    RunRequest,
    RunResult,
    run_command,
)
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    DiffFile,
    DiffHunk,
    DiffResult,
    LogEntry,
    LogResult,
    StatusEntry,
    StatusResult,
    TestFailure,
    TestRunResult,
)

__all__ = [
    "BudgetHint",
    "CommandCacheKey",
    "CommandNotAllowed",
    "CommandTimeout",
    "CompressedOutput",
    "CompressionLevel",
    "CompressionReport",
    "DiffFile",
    "DiffHunk",
    "DiffResult",
    "LogEntry",
    "LogResult",
    "RunRequest",
    "RunResult",
    "StatusEntry",
    "StatusResult",
    "TestFailure",
    "TestRunResult",
    "build_cache_key",
    "clear_default_cache",
    "compress_command",
    "detect_compressor",
    "register_compressor",
    "run_command",
    "select_level",
]
