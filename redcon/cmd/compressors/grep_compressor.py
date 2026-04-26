"""
grep / ripgrep output compressor.

Handles both classic `path:line:text` form and ripgrep's grouped form
(path on its own line, indented `line:text` under it). Compact level
keeps every file plus the first three matches per file. Ultra reports
counts only.
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    GrepMatch,
    GrepResult,
)
from redcon.cmd._tokens_lite import estimate_tokens

_INLINE = re.compile(
    r"^(?P<path>[^\s:][^:]*):(?P<line>\d+):"
    r"(?:(?P<col>\d+):)?"
    r"(?P<text>.*)$"
)
_INDENTED = re.compile(r"^(?P<line>\d+):(?:(?P<col>\d+):)?(?P<text>.*)$")
# Path lines in grouped output have no leading digit and look like file paths.
_PATH_LIKE = re.compile(r"^[A-Za-z0-9_./\-][^:]+\.[A-Za-z0-9]+$")


class GrepCompressor:
    schema = "grep"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        return argv[0] in {"grep", "rg", "egrep", "fgrep"}

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        result = parse_grep(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        # Each path that had matches must survive in the formatted output.
        patterns = tuple(re.escape(p) for p in _unique_paths(result))
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


def parse_grep(text: str) -> GrepResult:
    matches: list[GrepMatch] = []
    current_file: str | None = None

    for line in text.splitlines():
        if not line:
            current_file = None
            continue
        # Fast path: indented form lines start with a digit (the line number).
        # Try _INDENTED first when the first char is a digit, _INLINE otherwise.
        first = line[0]
        if first.isdigit():
            indented = _INDENTED.match(line)
            if indented and current_file is not None:
                matches.append(
                    GrepMatch(
                        path=current_file,
                        line=int(indented.group("line")),
                        column=_safe_int(indented.group("col")),
                        text=indented.group("text").rstrip(),
                    )
                )
                continue
        inline = _INLINE.match(line)
        if inline:
            current_file = inline.group("path")
            matches.append(
                GrepMatch(
                    path=inline.group("path"),
                    line=int(inline.group("line")),
                    column=_safe_int(inline.group("col")),
                    text=inline.group("text").rstrip(),
                )
            )
            continue
        # Path header line in grouped form. Cheap structural check before regex.
        stripped = line.strip()
        if stripped and "." in stripped and ":" not in stripped:
            if _PATH_LIKE.match(stripped):
                current_file = stripped

    paths = _unique_paths_from_matches(matches)
    return GrepResult(
        matches=tuple(matches),
        file_count=len(paths),
        match_count=len(matches),
    )


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _unique_paths(result: GrepResult) -> list[str]:
    return _unique_paths_from_matches(result.matches)


def _unique_paths_from_matches(matches) -> list[str]:
    seen: list[str] = []
    in_seen: set[str] = set()
    for m in matches:
        if m.path not in in_seen:
            seen.append(m.path)
            in_seen.add(m.path)
    return seen


def _format(result: GrepResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: GrepResult) -> str:
    if result.match_count == 0:
        return "grep: no matches"
    return f"grep: {result.match_count} matches in {result.file_count} files"


def _format_compact(result: GrepResult) -> str:
    if not result.matches:
        return "grep: no matches"
    by_file = _group(result.matches)
    lines: list[str] = [
        f"grep: {result.match_count} matches in {result.file_count} files"
    ]
    for path, items in by_file.items():
        lines.append(f"{path} ({len(items)})")
        for match in items[:3]:
            text = match.text.strip()
            if len(text) > 200:
                text = text[:197] + "..."
            lines.append(f"   L{match.line}: {text}")
        if len(items) > 3:
            lines.append(f"   ... +{len(items) - 3} more")
    return "\n".join(lines)


def _format_verbose(result: GrepResult) -> str:
    if not result.matches:
        return "grep: no matches"
    by_file = _group(result.matches)
    lines: list[str] = [
        f"grep: {result.match_count} matches in {result.file_count} files"
    ]
    for path, items in by_file.items():
        lines.append(f"{path}")
        for match in items:
            text = match.text.rstrip()
            if len(text) > 300:
                text = text[:297] + "..."
            lines.append(f"   L{match.line}: {text}")
    return "\n".join(lines)


def _group(matches) -> dict[str, list[GrepMatch]]:
    groups: dict[str, list[GrepMatch]] = {}
    for m in matches:
        groups.setdefault(m.path, []).append(m)
    return groups
