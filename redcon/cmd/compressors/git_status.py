"""
git status compressor.

Targets `git status --porcelain=v1 -b` (and the bare `git status` with branch
header). Parses every entry into StatusEntry and counts ahead/behind from the
branch header. Compression levels:
  - VERBOSE: full list with branch + ahead/behind + every entry
  - COMPACT: branch + counts by status + first 20 paths
  - ULTRA: branch + total counts only
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
    StatusEntry,
    StatusResult,
)
from redcon.cmd._tokens_lite import estimate_tokens

_BRANCH_HEADER = re.compile(
    r"^## (?P<branch>[^\s.]+)"
    r"(?:\.\.\.(?P<upstream>\S+))?"
    r"(?: \[(?P<tracking>[^\]]+)\])?$"
)
_AHEAD = re.compile(r"ahead (\d+)")
_BEHIND = re.compile(r"behind (\d+)")
_RENAMED_LINE = re.compile(r"^(?P<idx>.)(?P<wt>.) (?P<from>.+) -> (?P<to>.+)$")


class GitStatusCompressor:
    schema = "git_status"
    must_preserve_patterns = (
        r"branch:|^[ MADRCU?!]{2} ",
    )

    def matches(self, argv: tuple[str, ...]) -> bool:
        if len(argv) < 2:
            return False
        return argv[0] == "git" and argv[1] == "status"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        result = parse_status(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        # Header inflation guard: on tiny noisy inputs the 'branch: ?\\n...'
        # header plus per-line passthrough can exceed raw. Fall through to
        # raw passthrough so the contract stays non-regressive on the
        # margin (V85 finding 3).
        if (
            level != CompressionLevel.ULTRA
            and raw_tokens < 80
            and compressed_tokens >= raw_tokens
            and text.strip()
        ):
            formatted = text.rstrip()
            compressed_tokens = estimate_tokens(formatted)
        preserved = verify_must_preserve(formatted, self.must_preserve_patterns, text)
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


def parse_status(text: str) -> StatusResult:
    """Parse `git status --porcelain=v1 -b` (or plain porcelain) output."""
    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    entries: list[StatusEntry] = []

    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("## "):
            branch_match = _BRANCH_HEADER.match(line)
            if branch_match:
                branch = branch_match.group("branch")
                upstream = branch_match.group("upstream")
                tracking = branch_match.group("tracking") or ""
                ahead_match = _AHEAD.search(tracking)
                behind_match = _BEHIND.search(tracking)
                if ahead_match:
                    ahead = int(ahead_match.group(1))
                if behind_match:
                    behind = int(behind_match.group(1))
            continue
        if len(line) < 3:
            continue
        idx_status = line[0]
        wt_status = line[1]
        rest = line[3:]

        if idx_status == "?" and wt_status == "?":
            entries.append(
                StatusEntry(
                    path=rest,
                    index_status="?",
                    worktree_status="?",
                    untracked=True,
                )
            )
            continue

        rename_match = _RENAMED_LINE.match(line)
        if rename_match and (idx_status == "R" or wt_status == "R"):
            entries.append(
                StatusEntry(
                    path=rename_match.group("to"),
                    index_status=idx_status,
                    worktree_status=wt_status,
                    untracked=False,
                    renamed_from=rename_match.group("from"),
                )
            )
            continue

        entries.append(
            StatusEntry(
                path=rest,
                index_status=idx_status,
                worktree_status=wt_status,
                untracked=False,
            )
        )

    return StatusResult(
        branch=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        entries=tuple(entries),
    )


def _format(result: StatusResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: StatusResult) -> str:
    counts = _count_by_status(result)
    branch = result.branch or "?"
    tracking = ""
    if result.ahead or result.behind:
        bits = []
        if result.ahead:
            bits.append(f"+{result.ahead}")
        if result.behind:
            bits.append(f"-{result.behind}")
        tracking = f" [{' '.join(bits)}]"
    if not result.entries:
        return f"branch:{branch}{tracking} clean"
    summary_parts = [f"{k}:{v}" for k, v in counts.items() if v]
    return f"branch:{branch}{tracking} {' '.join(summary_parts)}"


def _format_compact(result: StatusResult) -> str:
    lines: list[str] = []
    branch = result.branch or "?"
    head = f"branch: {branch}"
    if result.upstream:
        head += f" -> {result.upstream}"
    if result.ahead or result.behind:
        head += f" (ahead {result.ahead}, behind {result.behind})"
    lines.append(head)

    counts = _count_by_status(result)
    summary = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    if summary:
        lines.append(summary)

    for entry in result.entries[:20]:
        lines.append(_format_entry(entry))
    if len(result.entries) > 20:
        lines.append(f"... +{len(result.entries) - 20} more entries")
    return "\n".join(lines)


def _format_verbose(result: StatusResult) -> str:
    lines: list[str] = []
    branch = result.branch or "?"
    head = f"branch: {branch}"
    if result.upstream:
        head += f" -> {result.upstream}"
    if result.ahead or result.behind:
        head += f" (ahead {result.ahead}, behind {result.behind})"
    lines.append(head)

    if not result.entries:
        lines.append("(working tree clean)")
        return "\n".join(lines)

    for entry in result.entries:
        lines.append(_format_entry(entry))
    return "\n".join(lines)


def _format_entry(entry: StatusEntry) -> str:
    code = f"{entry.index_status}{entry.worktree_status}"
    if entry.renamed_from:
        return f"{code} {entry.renamed_from} -> {entry.path}"
    return f"{code} {entry.path}"


def _count_by_status(result: StatusResult) -> dict[str, int]:
    counts: dict[str, int] = {
        "modified": 0,
        "added": 0,
        "deleted": 0,
        "renamed": 0,
        "untracked": 0,
    }
    for entry in result.entries:
        if entry.untracked:
            counts["untracked"] += 1
            continue
        if entry.renamed_from:
            counts["renamed"] += 1
            continue
        any_status = entry.index_status if entry.index_status != " " else entry.worktree_status
        if any_status == "M":
            counts["modified"] += 1
        elif any_status == "A":
            counts["added"] += 1
        elif any_status == "D":
            counts["deleted"] += 1
    return counts
