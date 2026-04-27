"""
git log compressor.

Supports the default `commit/Author/Date/<blank>/    Subject/    body` format and
the `--oneline` format. Parses entries into LogResult and re-formats at the
selected compression level.
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
    LogEntry,
    LogResult,
)
from redcon.cmd._tokens_lite import estimate_tokens

_COMMIT_LINE = re.compile(r"^commit (?P<sha>[0-9a-f]{7,40})\b.*$")
_AUTHOR_LINE = re.compile(r"^Author: (?P<author>.+)$")
_DATE_LINE = re.compile(r"^Date:\s+(?P<date>.+)$")
_ONELINE = re.compile(r"^(?P<sha>[0-9a-f]{7,40}) (?P<subject>.+)$")


class GitLogCompressor:
    schema = "git_log"
    # must_preserve_patterns are computed per-call from the parsed log
    # entries (short shas) so we only assert on facts the parser
    # actually extracted - mirrors git_diff and listing compressors.
    # Static regex matched mutation-generated fragments that the
    # parser correctly drops (V85 finding).
    must_preserve_patterns: tuple[str, ...] = ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if len(argv) < 2:
            return False
        return argv[0] == "git" and argv[1] == "log"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        oneline = _is_oneline(ctx.argv) or _looks_like_oneline(text)
        result = parse_log_oneline(text) if oneline else parse_log_default(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        # Patterns from parsed entries: short shas survive in compact
        # output so this asserts a fact the formatter actually emits.
        patterns = tuple(
            re.escape(entry.short_sha)
            for entry in result.entries
            if entry.short_sha
        )[:50]
        preserved = verify_must_preserve(formatted, patterns, text)
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


def _is_oneline(argv: tuple[str, ...]) -> bool:
    return any(a == "--oneline" or a.startswith("--pretty=oneline") for a in argv)


def _looks_like_oneline(text: str) -> bool:
    sample = text.strip().splitlines()[:3]
    if not sample:
        return False
    return all(_ONELINE.match(line) for line in sample)


def parse_log_default(text: str) -> LogResult:
    entries: list[LogEntry] = []
    current: dict | None = None
    body_lines: list[str] = []
    subject_seen = False

    def flush() -> None:
        nonlocal current, body_lines, subject_seen
        if current is None:
            return
        subject = current.get("subject", "")
        body = "\n".join(body_lines).strip()
        sha = current.get("sha", "")
        entries.append(
            LogEntry(
                sha=sha,
                short_sha=sha[:7],
                author=current.get("author", ""),
                date=current.get("date", ""),
                subject=subject,
                body=body,
                files_changed=0,
            )
        )
        current = None
        body_lines = []
        subject_seen = False

    for line in text.splitlines():
        commit_match = _COMMIT_LINE.match(line)
        if commit_match:
            flush()
            current = {"sha": commit_match.group("sha")}
            continue
        if current is None:
            continue
        author_match = _AUTHOR_LINE.match(line)
        if author_match:
            current["author"] = author_match.group("author")
            continue
        date_match = _DATE_LINE.match(line)
        if date_match:
            current["date"] = date_match.group("date")
            continue
        if line.startswith("    "):
            stripped = line[4:]
            if not subject_seen:
                current["subject"] = stripped
                subject_seen = True
            else:
                body_lines.append(stripped)

    flush()
    return LogResult(entries=tuple(entries), truncated_at=None)


def parse_log_oneline(text: str) -> LogResult:
    entries: list[LogEntry] = []
    for line in text.splitlines():
        m = _ONELINE.match(line.strip())
        if not m:
            continue
        sha = m.group("sha")
        entries.append(
            LogEntry(
                sha=sha,
                short_sha=sha[:7],
                author="",
                date="",
                subject=m.group("subject"),
                body="",
                files_changed=0,
            )
        )
    return LogResult(entries=tuple(entries), truncated_at=None)


def _format(result: LogResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: LogResult) -> str:
    if not result.entries:
        return "log: (no commits)"
    first = result.entries[0]
    last = result.entries[-1]
    return (
        f"log: {len(result.entries)} commits, "
        f"newest={first.short_sha} '{_clip(first.subject, 50)}', "
        f"oldest={last.short_sha}"
    )


def _format_compact(result: LogResult) -> str:
    if not result.entries:
        return "log: (no commits)"
    lines = [f"log: {len(result.entries)} commits"]
    for e in result.entries[:30]:
        subject = _clip(e.subject, 80)
        if subject:
            lines.append(f"{e.short_sha} {subject}")
        else:
            # Empty subject: preserve the canonical 'commit <sha>' form so
            # downstream auditors can verify the entry survived. Otherwise
            # the row collapses to '<sha> ' which fails the must-preserve
            # contract (V85 finding).
            lines.append(f"commit {e.short_sha}")
    if len(result.entries) > 30:
        lines.append(f"... +{len(result.entries) - 30} more commits")
    return "\n".join(lines)


def _format_verbose(result: LogResult) -> str:
    if not result.entries:
        return "log: (no commits)"
    # Oneline input has no author/date/body - keeping one-line-per-commit
    # avoids inflating token count above the raw output.
    if _is_oneline_style(result):
        lines = [f"log: {len(result.entries)} commits"]
        for e in result.entries:
            if e.subject:
                lines.append(f"{e.short_sha} {e.subject}")
            else:
                lines.append(f"commit {e.short_sha}")
        return "\n".join(lines)

    lines = [f"log: {len(result.entries)} commits"]
    for e in result.entries:
        head = f"commit {e.short_sha}"
        if e.author:
            head += f" - {e.author}"
        if e.date:
            head += f" ({e.date})"
        lines.append(head)
        # Drop indent: subject + body lines are unambiguous after the commit
        # header and we save one cl100k token per body line.
        lines.append(e.subject)
        if e.body:
            for body_line in e.body.splitlines()[:3]:
                lines.append(body_line)
    return "\n".join(lines)


def _is_oneline_style(result: LogResult) -> bool:
    if not result.entries:
        return False
    return all(not e.author and not e.date and not e.body for e in result.entries)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."
