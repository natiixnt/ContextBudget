"""
git diff compressor.

Parses unified diff output into DiffResult and re-formats at the requested
compression level. Compact level keeps file paths and per-file +/- counts but
drops hunk bodies. Ultra collapses to a single-line summary.
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import (
    Compressor,
    CompressorContext,
    verify_must_preserve,
)
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    DiffFile,
    DiffHunk,
    DiffResult,
)
from redcon.cmd._tokens_lite import estimate_tokens

_DIFF_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)$")
_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@(?P<header>.*)$"
)
_RENAME_FROM = re.compile(r"^rename from (?P<p>.+)$")
_RENAME_TO = re.compile(r"^rename to (?P<p>.+)$")
_BINARY = re.compile(r"^Binary files .* differ$")
_NEW_FILE_MODE = re.compile(r"^new file mode")
_DELETED_FILE_MODE = re.compile(r"^deleted file mode")


class GitDiffCompressor:
    """Compressor for `git diff` (and friends - `git diff HEAD`, `git diff --cached`)."""

    schema = "git_diff"
    must_preserve_patterns = (
        # Every file mentioned in the diff must survive in the output text.
        # The harness in M8 expands this with concrete fixtures.
        r"\bfiles? changed\b|\bdiff --git\b|^[A-Z] [^\s]+|^- ?[^\s]+",
    )

    def matches(self, argv: tuple[str, ...]) -> bool:
        if len(argv) < 2:
            return False
        if argv[0] != "git":
            return False
        return argv[1] == "diff"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        if not text.strip():
            return _empty_diff(ctx)

        result = parse_diff(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        preserved = verify_must_preserve(
            formatted, self.must_preserve_patterns, text
        )
        return CompressedOutput(
            text=formatted,
            level=level,
            schema=self.schema,
            original_tokens=raw_tokens,
            compressed_tokens=compressed_tokens,
            must_preserve_ok=preserved,
            truncated=False,
            notes=ctx.notes,
        )


def _empty_diff(ctx: CompressorContext) -> CompressedOutput:
    return CompressedOutput(
        text="(no diff)",
        level=CompressionLevel.ULTRA,
        schema="git_diff",
        original_tokens=0,
        compressed_tokens=2,
        must_preserve_ok=True,
        truncated=False,
        notes=ctx.notes,
    )


def parse_diff(text: str) -> DiffResult:
    """Parse a unified diff into a DiffResult. Tolerant of malformed input."""
    files: list[DiffFile] = []
    total_ins = 0
    total_del = 0

    blocks = _split_into_file_blocks(text)
    for block in blocks:
        diff_file = _parse_file_block(block)
        if diff_file is None:
            continue
        files.append(diff_file)
        total_ins += diff_file.insertions
        total_del += diff_file.deletions

    return DiffResult(
        files=tuple(files),
        total_insertions=total_ins,
        total_deletions=total_del,
    )


def _split_into_file_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _parse_file_block(block: list[str]) -> DiffFile | None:
    if not block:
        return None
    header_match = _DIFF_HEADER.match(block[0])
    if header_match is None:
        return None
    a_path = header_match.group("a")
    b_path = header_match.group("b")
    path = b_path
    old_path: str | None = a_path if a_path != b_path else None

    status = "modified"
    binary = False
    insertions = 0
    deletions = 0
    hunks: list[DiffHunk] = []

    current_hunk: dict | None = None

    for line in block[1:]:
        # Fast path for diff content lines (the vast majority): they always
        # start with '+', '-', ' '. Avoid every metadata regex check.
        if current_hunk is not None and line:
            first = line[0]
            if first == "+":
                if not line.startswith("+++"):
                    insertions += 1
                    current_hunk["added"].append(line[1:])
                    continue
            elif first == "-":
                if not line.startswith("---"):
                    deletions += 1
                    current_hunk["removed"].append(line[1:])
                    continue
            elif first == " ":
                # Context line: no insertion/deletion, no metadata.
                continue

        # Cheap prefix gates before each regex - most lines fail these so we
        # skip the regex engine entirely.
        if line.startswith("@@"):
            hunk_match = _HUNK_HEADER.match(line)
            if hunk_match:
                if current_hunk is not None:
                    hunks.append(_finalize_hunk(current_hunk))
                current_hunk = {
                    "old_start": int(hunk_match.group("old_start")),
                    "old_lines": int(hunk_match.group("old_lines") or "1"),
                    "new_start": int(hunk_match.group("new_start")),
                    "new_lines": int(hunk_match.group("new_lines") or "1"),
                    "header": hunk_match.group("header").strip(),
                    "added": [],
                    "removed": [],
                }
                continue
        if line.startswith("new file mode"):
            status = "added"
            old_path = None
            continue
        if line.startswith("deleted file mode"):
            status = "deleted"
            continue
        if line.startswith("rename from "):
            status = "renamed"
            old_path = line[len("rename from "):]
            continue
        if line.startswith("rename to "):
            path = line[len("rename to "):]
            continue
        if line.startswith("Binary files"):
            if _BINARY.match(line):
                binary = True
            continue

    if current_hunk is not None:
        hunks.append(_finalize_hunk(current_hunk))

    return DiffFile(
        path=path,
        old_path=old_path,
        status=status,
        insertions=insertions,
        deletions=deletions,
        binary=binary,
        hunks=tuple(hunks),
    )


def _finalize_hunk(d: dict) -> DiffHunk:
    return DiffHunk(
        old_start=d["old_start"],
        old_lines=d["old_lines"],
        new_start=d["new_start"],
        new_lines=d["new_lines"],
        header=d["header"],
        added=tuple(d["added"]),
        removed=tuple(d["removed"]),
    )


def _format(result: DiffResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: DiffResult) -> str:
    if not result.files:
        return "(no diff)"
    paths = [f.path for f in result.files]
    paths_blob = ", ".join(paths[:8])
    if len(paths) > 8:
        paths_blob += f", +{len(paths) - 8} more"
    return (
        f"diff: {len(result.files)} files, "
        f"+{result.total_insertions} -{result.total_deletions} "
        f"[{paths_blob}]"
    )


def _format_compact(result: DiffResult) -> str:
    lines: list[str] = [
        f"diff: {len(result.files)} files, "
        f"+{result.total_insertions} -{result.total_deletions}",
    ]
    for f in result.files:
        marker = _status_marker(f.status)
        if f.binary:
            lines.append(f"{marker} {f.path} (binary)")
            continue
        rename = f" (from {f.old_path})" if f.status == "renamed" and f.old_path else ""
        lines.append(
            f"{marker} {f.path}{rename}: +{f.insertions} -{f.deletions}"
        )
        if f.hunks:
            first = f.hunks[0]
            header = first.header.strip()
            loc = f"@@ -{first.old_start},{first.old_lines} +{first.new_start},{first.new_lines}"
            if header:
                lines.append(f"   {loc} {header}")
            else:
                lines.append(f"   {loc}")
            if len(f.hunks) > 1:
                lines.append(f"   ... +{len(f.hunks) - 1} more hunks")
    return "\n".join(lines)


def _format_verbose(result: DiffResult) -> str:
    lines: list[str] = [
        f"diff --git summary: {len(result.files)} files, "
        f"+{result.total_insertions} -{result.total_deletions}",
        "",
    ]
    for f in result.files:
        marker = _status_marker(f.status)
        rename = f" (from {f.old_path})" if f.status == "renamed" and f.old_path else ""
        lines.append(f"{marker} {f.path}{rename}: +{f.insertions} -{f.deletions}")
        if f.binary:
            lines.append("   (binary file)")
            continue
        for hunk in f.hunks:
            loc = f"@@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines}"
            header = hunk.header.strip()
            lines.append(f"   {loc}" + (f" {header}" if header else ""))
            for added in hunk.added[:5]:
                lines.append(f"   +{added}")
            if len(hunk.added) > 5:
                lines.append(f"   +... ({len(hunk.added) - 5} more)")
            for removed in hunk.removed[:5]:
                lines.append(f"   -{removed}")
            if len(hunk.removed) > 5:
                lines.append(f"   -... ({len(hunk.removed) - 5} more)")
    return "\n".join(lines)


def _status_marker(status: str) -> str:
    return {
        "added": "A",
        "modified": "M",
        "deleted": "D",
        "renamed": "R",
    }.get(status, "?")
