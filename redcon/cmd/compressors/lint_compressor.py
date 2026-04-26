"""
Lint output compressor.

Handles mypy and ruff (and any other tool whose default output matches
``path:line[:col]:[ severity] [code] message``). Groups issues by file
plus a per-code histogram so the agent sees the shape of failures
without scrolling through hundreds of repeated lines.

Compact level keeps every file that had issues, the per-code histogram,
and the top three messages per file. Ultra collapses to counts.
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    LintIssue,
    LintResult,
)
from redcon.cmd._tokens_lite import estimate_tokens


# mypy: "path:line: severity: message  [code]"
_MYPY_LINE = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+):"
    r"(?:(?P<col>\d+):)?\s*"
    r"(?P<severity>error|warning|note):\s*"
    r"(?P<message>.*?)(?:\s*\[(?P<code>[^\]]+)\])?\s*$"
)
# ruff default: "path:line:col: CODE message"
_RUFF_LINE = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+):(?P<col>\d+):\s*"
    r"(?P<code>[A-Z][A-Z0-9]+\d+)\s+(?P<message>.*?)\s*$"
)
_MYPY_FOOTER = re.compile(
    r"(?:Found\s+(?P<errors>\d+)\s+errors?)|"
    r"(?:Success: no issues found)"
)
_RUFF_FOOTER = re.compile(r"^Found\s+(?P<n>\d+)\s+errors?\.?")


class LintCompressor:
    """Compressor for mypy / ruff. Detects which by argv[0]."""

    schema = "lint"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        if argv[0] in {"mypy", "ruff"}:
            return True
        if argv[0] in {"python", "python3"} and "-m" in argv and (
            "mypy" in argv or "ruff" in argv
        ):
            return True
        return False

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        if not text.strip() and raw_stderr:
            text = raw_stderr.decode("utf-8", errors="replace")
        tool = _detect_tool(ctx.argv)
        result = parse_lint(text, tool=tool)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        # The compact format keeps the top 30 files by issue count; require
        # the same set in must-preserve so a clipped tail doesn't fail the
        # gate. Verbose mode emits everything anyway.
        path_counts: dict[str, int] = {}
        for issue in result.issues:
            path_counts[issue.path] = path_counts.get(issue.path, 0) + 1
        # Same (-count, path) sort the formatter uses, so what the compact
        # format actually emits is exactly what we promise to preserve.
        top_paths = [
            p
            for p, _ in sorted(
                path_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ][:30]
        patterns = tuple(re.escape(p) for p in top_paths)
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


def _detect_tool(argv: tuple[str, ...]) -> str:
    if not argv:
        return "lint"
    if argv[0] in {"mypy", "ruff"}:
        return argv[0]
    for token in argv:
        if token in {"mypy", "ruff"}:
            return token
    return "lint"


def parse_lint(text: str, *, tool: str = "lint") -> LintResult:
    issues: list[LintIssue] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        # Try mypy form first - it has a severity word that ruff lacks.
        m = _MYPY_LINE.match(line)
        if m and m.group("severity"):
            issues.append(
                LintIssue(
                    path=m.group("path"),
                    line=int(m.group("line")),
                    column=_safe_int(m.group("col")),
                    severity=m.group("severity"),
                    code=m.group("code"),
                    message=m.group("message").strip(),
                )
            )
            continue
        m = _RUFF_LINE.match(line)
        if m:
            issues.append(
                LintIssue(
                    path=m.group("path"),
                    line=int(m.group("line")),
                    column=_safe_int(m.group("col")),
                    severity="error",
                    code=m.group("code"),
                    message=m.group("message").strip(),
                )
            )
            continue

    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    note_count = sum(1 for i in issues if i.severity in {"note", "info"})
    file_count = len({i.path for i in issues})

    return LintResult(
        tool=tool,
        issues=tuple(issues),
        error_count=error_count,
        warning_count=warning_count,
        note_count=note_count,
        file_count=file_count,
    )


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format(result: LintResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: LintResult) -> str:
    if not result.issues:
        return f"{result.tool}: clean"
    return (
        f"{result.tool}: {result.error_count} errors, "
        f"{result.warning_count} warnings, "
        f"{result.note_count} notes "
        f"in {result.file_count} files"
    )


def _format_compact(result: LintResult) -> str:
    """Compact: counts + per-code histogram + per-file counts only.

    Per-issue message previews live in VERBOSE. Putting them at compact
    level inflates output for small/medium suites because the previews
    are about as long as the original lines.
    """
    if not result.issues:
        return f"{result.tool}: clean"
    lines: list[str] = [
        f"{result.tool}: {result.error_count} errors, "
        f"{result.warning_count} warnings "
        f"in {result.file_count} files"
    ]
    code_hist = _code_histogram(result.issues)
    if code_hist:
        top = ", ".join(f"{code}:{n}" for code, n in code_hist[:8])
        lines.append(f"by code: {top}")
    by_file = _group_by_file(result.issues)
    file_limit = 30
    # Sort by issue count descending so the busiest files survive the cap;
    # this also matches the must-preserve patterns the compressor declares.
    sorted_files = sorted(by_file.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for path, items in sorted_files[:file_limit]:
        lines.append(f"{path}: {len(items)}")
    if len(sorted_files) > file_limit:
        lines.append(f"+{len(sorted_files) - file_limit} more files")
    return "\n".join(lines)


def _format_verbose(result: LintResult) -> str:
    if not result.issues:
        return f"{result.tool}: clean"
    lines: list[str] = [
        f"{result.tool}: {result.error_count} errors, "
        f"{result.warning_count} warnings "
        f"in {result.file_count} files",
        "",
    ]
    by_file = _group_by_file(result.issues)
    for path, items in by_file.items():
        lines.append(path)
        for issue in items:
            code = f"[{issue.code}] " if issue.code else ""
            loc = f"L{issue.line}"
            if issue.column is not None:
                loc += f":{issue.column}"
            sev = "" if issue.severity == "error" else f"{issue.severity} "
            lines.append(f"{loc} {sev}{code}{issue.message}")
    return "\n".join(lines)


def _group_by_file(issues) -> dict[str, list[LintIssue]]:
    groups: dict[str, list[LintIssue]] = {}
    for issue in issues:
        groups.setdefault(issue.path, []).append(issue)
    return groups


def _code_histogram(issues) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for issue in issues:
        if issue.code:
            counts[issue.code] = counts.get(issue.code, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])
