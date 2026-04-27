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


# --- Test runner canonical types (pytest, cargo test, npm test, go test) ---


@dataclass(frozen=True, slots=True)
class TestFailure:
    """One failing test. The snippet is up to ~5 lines of the actual failure context."""

    # Mark these dataclasses as non-collectable by pytest, which otherwise
    # warns about "Test*" classes that look like (but aren't) test classes.
    __test__ = False

    name: str
    file: str | None
    line: int | None
    message: str
    snippet: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TestRunResult:
    """Aggregated test-run output across runners. Same shape for pytest/cargo/jest/etc."""

    __test__ = False

    runner: str
    total: int
    passed: int
    failed: int
    skipped: int
    errored: int
    duration_seconds: float | None
    failures: tuple[TestFailure, ...]
    warnings: tuple[str, ...]


# --- Search canonical types (grep, ripgrep) ---


@dataclass(frozen=True, slots=True)
class GrepMatch:
    """One regex match in a file."""

    path: str
    line: int
    column: int | None
    text: str


@dataclass(frozen=True, slots=True)
class GrepResult:
    """Aggregated search output. Matches are grouped by path inside the same tuple."""

    matches: tuple[GrepMatch, ...]
    file_count: int
    match_count: int


# --- Listing canonical types (ls, tree, find) ---


@dataclass(frozen=True, slots=True)
class Listing:
    """One entry produced by a listing tool."""

    path: str
    kind: str  # "file" | "dir" | "symlink" | "other"
    size: int | None
    depth: int


@dataclass(frozen=True, slots=True)
class ListingResult:
    """Output of a directory listing or path search."""

    source: str  # "ls" | "tree" | "find"
    entries: tuple[Listing, ...]
    truncated: bool


# --- Lint canonical types (mypy, ruff, eslint-style) ---


@dataclass(frozen=True, slots=True)
class LintIssue:
    """One reported issue from a linter."""

    path: str
    line: int
    column: int | None
    severity: str  # "error" | "warning" | "note" | "info"
    code: str | None
    message: str


@dataclass(frozen=True, slots=True)
class LintResult:
    tool: str
    issues: tuple[LintIssue, ...]
    error_count: int
    warning_count: int
    note_count: int
    file_count: int


# --- Container / image canonical types (docker, podman) ---


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    container_id: str
    image: str
    status: str
    name: str
    ports: tuple[str, ...]
    age: str | None


@dataclass(frozen=True, slots=True)
class ContainerListResult:
    containers: tuple[ContainerInfo, ...]
    running_count: int


@dataclass(frozen=True, slots=True)
class ImageBuildStep:
    instruction: str
    cached: bool
    duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class ImageBuildResult:
    steps: tuple[ImageBuildStep, ...]
    final_image_id: str | None
    final_tags: tuple[str, ...]
    success: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


# --- Package install canonical types (pip / npm / pnpm / yarn) ---


@dataclass(frozen=True, slots=True)
class PackageOp:
    name: str
    version: str | None
    op: str  # "added" | "removed" | "updated" | "deprecated"


@dataclass(frozen=True, slots=True)
class PackageInstallResult:
    tool: str
    operations: tuple[PackageOp, ...]
    added: int
    removed: int
    updated: int
    deprecated_count: int
    vulnerabilities: tuple[str, ...]
    duration_seconds: float | None
    errors: tuple[str, ...]


# --- Kubectl canonical types ---


@dataclass(frozen=True, slots=True)
class KubeResource:
    kind: str
    name: str
    namespace: str | None
    status: str
    age: str | None
    extra: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class KubeListResult:
    resources: tuple[KubeResource, ...]
    kind: str


@dataclass(frozen=True, slots=True)
class KubeEventGroup:
    """Aggregated events sharing the same (type, reason, object_kind, object_name)."""

    event_type: str  # "Warning" | "Normal" | other
    reason: str
    object_kind: str
    object_name: str
    namespace: str | None
    count: int
    sample_message: str
    last_seen: str | None


@dataclass(frozen=True, slots=True)
class KubeEventsResult:
    groups: tuple[KubeEventGroup, ...]
    total_events: int
    warning_count: int


# --- Profiler canonical types (py-spy / perf collapsed stacks) ---


@dataclass(frozen=True, slots=True)
class HotPath:
    """One stack from a collapsed-stack profile sample."""

    stack: tuple[str, ...]  # frames root -> leaf
    samples: int

    @property
    def leaf(self) -> str:
        return self.stack[-1] if self.stack else ""


@dataclass(frozen=True, slots=True)
class ProfileResult:
    paths: tuple[HotPath, ...]  # sorted desc by samples
    total_samples: int
    distinct_stacks: int


# --- JSON-line log canonical types ---


@dataclass(frozen=True, slots=True)
class JsonLogRecord:
    """One parsed NDJSON line. `extras` carries keys outside the dominant schema."""

    fields: tuple[tuple[str, str], ...]  # canonical-schema (key, value) in schema order
    level: str | None
    timestamp: str | None
    raw_line: str  # kept for verbose emit and outlier fallback


@dataclass(frozen=True, slots=True)
class JsonLogResult:
    schema_keys: tuple[str, ...]  # canonical key order; "" when no schema mined
    records: tuple[JsonLogRecord, ...]
    outliers: tuple[str, ...]  # raw lines that failed to parse or fit the schema
    total_lines: int
    level_histogram: tuple[tuple[str, int], ...]  # sorted desc by count
