"""
pytest output compressor.

Parses both the FAILURES section (the long stack trace blocks separated by
heavy === lines) and the short test summary at the bottom (`FAILED ...`).
Produces a TestRunResult that the shared test_format renders at the chosen
level. Compact level is the typical agent default - it keeps every failing
test name plus the first meaningful line of each failure.
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.compressors.test_format import (
    format_test_result,
    must_preserve_patterns_for_failures,
)
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    TestFailure,
    TestRunResult,
)
from redcon.core.tokens import estimate_tokens

_FAILURES_HEADER = re.compile(r"^=+\s*FAILURES\s*=+$")
_ERRORS_HEADER = re.compile(r"^=+\s*ERRORS\s*=+$")
_SHORT_SUMMARY_HEADER = re.compile(r"^=+\s*short test summary info\s*=+$")
# The block-name line uses 3+ contiguous underscores around the name. The
# section-internal divider that pytest also prints (`_ _ _ _ _ ...`) has
# spaces between underscores and is rejected by `_{3,}`.
_FAIL_NAME_BLOCK = re.compile(r"^_{3,}\s+(?P<name>\S(?:.*\S)?)\s+_{3,}$")
_LOCATION_LINE = re.compile(r"^(?P<file>[^:\s]+\.\w+):(?P<line>\d+):")
# Footer parts can appear in any order: `3 failed, 1 error, 96 passed in 5.34s`
# is just as valid as `96 passed, 3 failed in 5.34s`. We capture each
# `(\d+)\s+(label)` pair independently and look for the duration separately.
_FOOTER_PART = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|failed|skipped|errors?|warnings?|xfailed|xpassed)\b"
)
_FOOTER_DURATION = re.compile(r"\bin\s+(?P<duration>[\d.]+)s\b")
_FOOTER_LINE = re.compile(r"^=+\s+.*\s+in\s+[\d.]+s.*=+$")
_SHORT_FAIL_LINE = re.compile(r"^FAILED\s+(?P<name>\S+)\s*-?\s*(?P<msg>.*)$")
_SHORT_ERROR_LINE = re.compile(r"^ERROR\s+(?P<name>\S+)\s*-?\s*(?P<msg>.*)$")
_WARNING_LINE = re.compile(r"warnings? summary", re.IGNORECASE)


class PytestCompressor:
    schema = "pytest"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        # Patterns extended at compress time once we know which failures exist.
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        if argv[0] == "pytest":
            return True
        if argv[0] in {"python", "python3"} and "-m" in argv and "pytest" in argv:
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
        result = parse_pytest(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = format_test_result(result, level)
        compressed_tokens = estimate_tokens(formatted)
        patterns = must_preserve_patterns_for_failures(result.failures)
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


def parse_pytest(text: str) -> TestRunResult:
    """Parse pytest stdout into a TestRunResult."""
    lines = text.splitlines()
    failures = _parse_failure_blocks(lines)
    short_failures = _parse_short_summary(lines)
    failures = _merge_failures(failures, short_failures)
    counts = _parse_footer(lines)
    warnings = _parse_warnings(lines)

    total = (
        counts["passed"]
        + counts["failed"]
        + counts["skipped"]
        + counts["errors"]
    )
    return TestRunResult(
        runner="pytest",
        total=total,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        errored=counts["errors"],
        duration_seconds=counts["duration"],
        failures=tuple(failures),
        warnings=tuple(warnings),
    )


def _parse_failure_blocks(lines: list[str]) -> list[TestFailure]:
    """Walk the FAILURES / ERRORS section of pytest output."""
    failures: list[TestFailure] = []
    in_section = False
    current_name: str | None = None
    current_block: list[str] = []

    def flush() -> None:
        nonlocal current_name, current_block
        if current_name is None:
            return
        failure = _build_failure(current_name, current_block)
        if failure is not None:
            failures.append(failure)
        current_name = None
        current_block = []

    for line in lines:
        if _FAILURES_HEADER.match(line) or _ERRORS_HEADER.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if _SHORT_SUMMARY_HEADER.match(line):
            flush()
            in_section = False
            continue
        # New failure block starts with a heavy underscored name line.
        name_match = _FAIL_NAME_BLOCK.match(line)
        if name_match:
            flush()
            current_name = name_match.group("name").strip()
            current_block = []
            continue
        if current_name is not None:
            current_block.append(line)

    flush()
    return failures


def _build_failure(name: str, body_lines: list[str]) -> TestFailure | None:
    """Extract message + location + small snippet from a single pytest failure block."""
    # First location-like line in the block is the test source ref pytest prints.
    file: str | None = None
    line_no: int | None = None
    snippet: list[str] = []
    message_lines: list[str] = []
    for raw in body_lines:
        loc = _LOCATION_LINE.match(raw)
        if loc and file is None:
            file = loc.group("file")
            try:
                line_no = int(loc.group("line"))
            except ValueError:
                line_no = None
            continue
        stripped = raw.strip()
        if stripped.startswith("E "):
            message_lines.append(stripped[2:].lstrip())
            continue
        if stripped.startswith(">"):
            snippet.append(stripped)
            continue
    message = "\n".join(message_lines).strip() or "\n".join(body_lines[:5]).strip()
    return TestFailure(
        name=name,
        file=file,
        line=line_no,
        message=message,
        snippet=tuple(snippet[:8]),
    )


def _parse_short_summary(lines: list[str]) -> list[TestFailure]:
    """Parse the `=== short test summary info ===` block at the bottom."""
    in_section = False
    out: list[TestFailure] = []
    for line in lines:
        if _SHORT_SUMMARY_HEADER.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("="):
            break
        m = _SHORT_FAIL_LINE.match(line) or _SHORT_ERROR_LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        message = m.group("msg") or ""
        # Try to peel `path::test` apart for location.
        file = name.split("::", 1)[0] if "::" in name else None
        out.append(
            TestFailure(
                name=name,
                file=file,
                line=None,
                message=message.strip(),
                snippet=(),
            )
        )
    return out


def _merge_failures(
    primary: list[TestFailure], short: list[TestFailure]
) -> list[TestFailure]:
    """Prefer primary (full body), fall back to short summary entries."""
    if primary:
        # Backfill messages from short summary when primary message is empty.
        by_short = {f.name: f for f in short}
        merged: list[TestFailure] = []
        for f in primary:
            if not f.message and f.name in by_short:
                replacement = by_short[f.name]
                merged.append(
                    TestFailure(
                        name=f.name,
                        file=f.file or replacement.file,
                        line=f.line or replacement.line,
                        message=replacement.message,
                        snippet=f.snippet,
                    )
                )
            else:
                merged.append(f)
        return merged
    return short


def _parse_footer(lines: list[str]) -> dict:
    counts = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "warnings": 0,
        "duration": None,
    }
    for line in reversed(lines):
        stripped = line.strip()
        if not _FOOTER_LINE.match(stripped):
            continue
        for part in _FOOTER_PART.finditer(stripped):
            label = part.group("label")
            count = int(part.group("count"))
            # Normalise plural/error variants to our keys.
            if label.startswith("error"):
                counts["errors"] = count
            elif label.startswith("warning"):
                counts["warnings"] = count
            elif label in counts:
                counts[label] = count
        duration = _FOOTER_DURATION.search(stripped)
        if duration:
            try:
                counts["duration"] = float(duration.group("duration"))
            except (TypeError, ValueError):
                pass
        break
    return counts


def _parse_warnings(lines: list[str]) -> list[str]:
    out: list[str] = []
    in_section = False
    for line in lines:
        if _WARNING_LINE.search(line) and line.startswith("="):
            in_section = True
            continue
        if in_section:
            if line.startswith("=") or not line.strip():
                if out:
                    return out
                continue
            stripped = line.strip()
            if stripped:
                out.append(stripped)
    return out
