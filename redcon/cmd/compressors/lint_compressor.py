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
        # Both layouts (per-file and rule-pivot) emit the top rule codes
        # via the 'by code:' histogram. Asserting on rule codes rather
        # than file paths gives a stable contract regardless of which
        # layout the min-gate picks. The histogram is the canonical
        # 'what kinds of issues exist' signal the agent acts on.
        code_hist = _code_histogram(result.issues)
        patterns = tuple(re.escape(code) for code, _ in code_hist[:8])
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
    """Compact: counts + per-code histogram + per-file counts.

    Computes both a per-file layout and a rule-pivot layout (V62) and
    keeps whichever has fewer tokens. The rule-pivot layout adds one
    full exemplar plus top-3 affected paths per rule, which is more
    actionable on Zipfian distributions; the per-file layout is shorter
    when the failure set is small or has few distinct codes. The
    min-gate makes the change non-regressive by construction.
    """
    if not result.issues:
        return f"{result.tool}: clean"
    per_file = _format_compact_per_file(result)
    rule_pivot = _maybe_format_compact_rule_pivot(result)
    if rule_pivot is None:
        return per_file
    if estimate_tokens(rule_pivot) < estimate_tokens(per_file):
        return rule_pivot
    return per_file


def _format_compact_per_file(result: LintResult) -> str:
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


_RULE_PIVOT_MIN_CODES = 3
_RULE_PIVOT_MIN_ISSUES = 8
_RULE_PIVOT_FILE_REFS = 3
_RULE_PIVOT_RULES = 12


def _maybe_format_compact_rule_pivot(result: LintResult) -> str | None:
    """Per-rule blocks with one exemplar + top-K affected paths per rule.

    Returns None when the activation gate fails (small suite, few codes)
    so the caller can fall back to the per-file form. The pivot is most
    valuable when a single rule dominates: an exemplar lets the agent
    fix the class without scrolling through every per-file listing.
    """
    coded_issues = [i for i in result.issues if i.code]
    if (
        len(coded_issues) < _RULE_PIVOT_MIN_ISSUES
        or len({i.code for i in coded_issues}) < _RULE_PIVOT_MIN_CODES
    ):
        return None

    by_code: dict[str, list[LintIssue]] = {}
    for issue in coded_issues:
        by_code.setdefault(issue.code or "", []).append(issue)
    # Order matches the canonical `_code_histogram` (stable sort by
    # -count, ties keep first-seen) so the head line emitted here lines
    # up with the must_preserve pattern set the compressor declares.
    canonical_order = [code for code, _ in _code_histogram(coded_issues)]
    sorted_codes = [(code, by_code[code]) for code in canonical_order]

    lines: list[str] = [
        f"{result.tool}: {result.error_count} errors, "
        f"{result.warning_count} warnings "
        f"in {result.file_count} files"
    ]
    head_pairs = [f"{code}:{len(items)}" for code, items in sorted_codes[:8]]
    lines.append("by code: " + ", ".join(head_pairs))

    for code, items in sorted_codes[:_RULE_PIVOT_RULES]:
        exemplar = items[0]
        loc = f"L{exemplar.line}"
        if exemplar.column is not None:
            loc += f":{exemplar.column}"
        sev = "" if exemplar.severity == "error" else f"{exemplar.severity} "
        # Single-line per rule: '<code>(<n>) <path>:L<line> <msg>'.
        # Files-affected are summarised via the head histogram + the
        # exemplar path, so we do not emit a per-rule path list - on
        # most distributions that inflated the form past the per-file
        # equivalent and the min-gate dropped it.
        lines.append(
            f"{code}({len(items)}) {exemplar.path}:{loc} {sev}{exemplar.message}"
        )
    if len(sorted_codes) > _RULE_PIVOT_RULES:
        lines.append(
            f"+{len(sorted_codes) - _RULE_PIVOT_RULES} more rules"
        )
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
