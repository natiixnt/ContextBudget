"""
Canonical types for command output compression.

Every compressor parses raw text into one of these structured types and then
formats it back to a string at a chosen CompressionLevel. The structured form
is what guarantees information preservation - patterns required by quality
gates are expressed against the canonical type, not the formatted string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CompressionLevel(str, Enum):
    """How aggressively to compress. Higher level = fewer tokens, less detail."""

    VERBOSE = "verbose"
    COMPACT = "compact"
    ULTRA = "ultra"


@dataclass(frozen=True, slots=True)
class CompressedOutput:
    """Final result returned to the caller. The `text` field is what goes to the LLM."""

    text: str
    level: CompressionLevel
    schema: str
    original_tokens: int
    compressed_tokens: int
    must_preserve_ok: bool
    truncated: bool
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reduction_pct(self) -> float:
        if self.original_tokens <= 0:
            return 0.0
        return 100.0 * (1.0 - self.compressed_tokens / self.original_tokens)


@dataclass(frozen=True, slots=True)
class DiffHunk:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    header: str
    added: tuple[str, ...]
    removed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiffFile:
    path: str
    old_path: str | None
    status: str
    insertions: int
    deletions: int
    binary: bool
    hunks: tuple[DiffHunk, ...]


@dataclass(frozen=True, slots=True)
class DiffResult:
    files: tuple[DiffFile, ...]
    total_insertions: int
    total_deletions: int


@dataclass(frozen=True, slots=True)
class StatusEntry:
    path: str
    index_status: str
    worktree_status: str
    untracked: bool
    renamed_from: str | None = None


@dataclass(frozen=True, slots=True)
class StatusResult:
    branch: str | None
    upstream: str | None
    ahead: int
    behind: int
    entries: tuple[StatusEntry, ...]


@dataclass(frozen=True, slots=True)
class LogEntry:
    sha: str
    short_sha: str
    author: str
    date: str
    subject: str
    body: str
    files_changed: int


@dataclass(frozen=True, slots=True)
class LogResult:
    entries: tuple[LogEntry, ...]
    truncated_at: int | None
