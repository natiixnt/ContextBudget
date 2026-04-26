"""
cargo test compressor.

Parses Rust's standard test output: the per-test `running N tests / test
foo::bar ... ok | FAILED | ignored` lines, the failure stdout blocks, and
the final `test result:` summary. Compilation warnings are captured
separately so the agent sees them at verbose level.
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
from redcon.cmd._tokens_lite import estimate_tokens

_TEST_LINE = re.compile(r"^test (?P<name>\S+)\s+\.\.\.\s+(?P<status>ok|FAILED|ignored)$")
_FAILURE_HEADER = re.compile(r"^---- (?P<name>\S+) stdout ----$")
_RESULT_LINE = re.compile(
    r"^test result:\s+(?P<status>\w+)\.\s+"
    r"(?P<passed>\d+)\s+passed;\s+"
    r"(?P<failed>\d+)\s+failed;\s+"
    r"(?P<ignored>\d+)\s+ignored;\s+"
    r"(?P<measured>\d+)\s+measured"
    r".*?finished in (?P<duration>[\d.]+)s"
)
_PANIC_LINE = re.compile(
    r"thread '.+?' panicked at '?(?P<msg>.+?)'?,\s*(?P<file>[^:\s]+):(?P<line>\d+)"
)
_WARNING_LINE = re.compile(r"^warning:\s*(?P<msg>.+)$")


class CargoTestCompressor:
    schema = "cargo_test"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if len(argv) < 2:
            return False
        return argv[0] == "cargo" and argv[1] == "test"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        # cargo prints test results to stdout, build warnings to stderr.
        stdout = raw_stdout.decode("utf-8", errors="replace")
        stderr = raw_stderr.decode("utf-8", errors="replace")
        text = stdout if stdout.strip() else stderr
        result = parse_cargo_test(stdout, stderr)
        raw_tokens = estimate_tokens(stdout) + estimate_tokens(stderr)
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


def parse_cargo_test(stdout: str, stderr: str = "") -> TestRunResult:
    """Parse cargo test stdout (+stderr for warnings) into TestRunResult."""
    lines = stdout.splitlines()
    failed_names = [
        m.group("name")
        for m in (_TEST_LINE.match(line) for line in lines)
        if m and m.group("status") == "FAILED"
    ]
    failure_blocks = _parse_failure_blocks(lines)
    by_name = {f.name: f for f in failure_blocks}

    failures: list[TestFailure] = []
    for name in failed_names:
        existing = by_name.get(name)
        if existing is not None:
            failures.append(existing)
        else:
            failures.append(
                TestFailure(name=name, file=None, line=None, message="", snippet=())
            )
    counts = _parse_result_line(lines)
    warnings = _parse_warnings(stderr.splitlines())

    return TestRunResult(
        runner="cargo test",
        total=counts["passed"] + counts["failed"] + counts["ignored"],
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["ignored"],
        errored=0,
        duration_seconds=counts["duration"],
        failures=tuple(failures),
        warnings=tuple(warnings),
    )


def _parse_failure_blocks(lines: list[str]) -> list[TestFailure]:
    """Each failure block: ---- foo::bar stdout ---- followed by panic output."""
    failures: list[TestFailure] = []
    current_name: str | None = None
    current_body: list[str] = []

    def flush() -> None:
        nonlocal current_name, current_body
        if current_name is None:
            return
        failure = _build_failure(current_name, current_body)
        if failure is not None:
            failures.append(failure)
        current_name = None
        current_body = []

    for line in lines:
        header = _FAILURE_HEADER.match(line)
        if header:
            flush()
            current_name = header.group("name")
            current_body = []
            continue
        if line.startswith("failures:") and current_name is not None:
            flush()
            continue
        if current_name is not None:
            current_body.append(line)
    flush()
    return failures


def _build_failure(name: str, body: list[str]) -> TestFailure:
    file: str | None = None
    line_no: int | None = None
    message_lines: list[str] = []
    for raw in body:
        if not raw.strip():
            continue
        panic = _PANIC_LINE.search(raw)
        if panic and file is None:
            file = panic.group("file")
            try:
                line_no = int(panic.group("line"))
            except ValueError:
                line_no = None
            message_lines.append(panic.group("msg").strip())
            continue
        message_lines.append(raw.strip())
    return TestFailure(
        name=name,
        file=file,
        line=line_no,
        message="\n".join(message_lines[:6]).strip(),
        snippet=tuple(message_lines[:8]),
    )


def _parse_result_line(lines: list[str]) -> dict:
    counts = {"passed": 0, "failed": 0, "ignored": 0, "duration": None}
    # Take the *last* result line - cargo emits one per test binary.
    for line in reversed(lines):
        m = _RESULT_LINE.match(line.strip())
        if m:
            counts["passed"] = int(m.group("passed"))
            counts["failed"] = int(m.group("failed"))
            counts["ignored"] = int(m.group("ignored"))
            try:
                counts["duration"] = float(m.group("duration"))
            except (TypeError, ValueError):
                pass
            return counts
    return counts


def _parse_warnings(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        m = _WARNING_LINE.match(line.strip())
        if m:
            out.append(m.group("msg").strip())
    return out
