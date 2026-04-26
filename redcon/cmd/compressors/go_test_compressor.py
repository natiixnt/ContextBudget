"""
go test compressor.

Parses `go test ./...` output: per-test PASS/FAIL/SKIP lines, the panic blocks
that follow a FAIL, the package-level summary lines, and the final ok/FAIL
package totals. Captures `go vet` style warnings if present.
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

_TEST_RUN = re.compile(r"^=== RUN\s+(?P<name>\S+)$")
_TEST_RESULT = re.compile(
    r"^--- (?P<status>PASS|FAIL|SKIP):\s+(?P<name>\S+)\s+\((?P<dur>[\d.]+)s\)$"
)
_PKG_LINE = re.compile(
    r"^(?P<status>ok|FAIL|---)\s+(?P<pkg>\S+)\s+(?P<dur>[\d.]+)s"
)
_FILE_LINE = re.compile(r"^\s*(?P<file>[^:\s]+\.go):(?P<line>\d+):\s*(?P<msg>.+)$")


class GoTestCompressor:
    schema = "go_test"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if len(argv) < 2:
            return False
        return argv[0] == "go" and argv[1] == "test"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        stderr_text = raw_stderr.decode("utf-8", errors="replace")
        result = parse_go_test(text, stderr_text)
        raw_tokens = estimate_tokens(text) + estimate_tokens(stderr_text)
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


def parse_go_test(stdout: str, stderr: str = "") -> TestRunResult:
    lines = stdout.splitlines()
    passed = 0
    failed = 0
    skipped = 0
    duration_total = 0.0
    failures: list[TestFailure] = []
    current_failure: str | None = None
    failure_body: list[str] = []

    def flush_failure() -> None:
        nonlocal current_failure, failure_body
        if current_failure is None:
            return
        failures.append(_build_failure(current_failure, failure_body))
        current_failure = None
        failure_body = []

    for line in lines:
        result = _TEST_RESULT.match(line)
        if result:
            status = result.group("status")
            name = result.group("name")
            try:
                duration_total += float(result.group("dur"))
            except ValueError:
                pass
            if status == "PASS":
                passed += 1
            elif status == "FAIL":
                failed += 1
                flush_failure()
                current_failure = name
                failure_body = []
            elif status == "SKIP":
                skipped += 1
            continue
        if current_failure is not None:
            if line.startswith("=== ") or _PKG_LINE.match(line):
                flush_failure()
                continue
            failure_body.append(line)
    flush_failure()

    warnings = [w.strip() for w in stderr.splitlines() if w.strip()]
    return TestRunResult(
        runner="go test",
        total=passed + failed + skipped,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errored=0,
        duration_seconds=duration_total or None,
        failures=tuple(failures),
        warnings=tuple(warnings[:10]),
    )


def _build_failure(name: str, body: list[str]) -> TestFailure:
    file: str | None = None
    line_no: int | None = None
    message_lines: list[str] = []
    for raw in body:
        m = _FILE_LINE.match(raw)
        if m and file is None:
            file = m.group("file")
            try:
                line_no = int(m.group("line"))
            except ValueError:
                line_no = None
            message_lines.append(m.group("msg").strip())
            continue
        stripped = raw.strip()
        if stripped:
            message_lines.append(stripped)
    return TestFailure(
        name=name,
        file=file,
        line=line_no,
        message="\n".join(message_lines[:6]).strip(),
        snippet=tuple(message_lines[:8]),
    )
