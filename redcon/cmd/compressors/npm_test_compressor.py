"""
npm/pnpm/yarn test compressor.

Auto-detects whether the runner is vitest or jest based on output markers
and parses accordingly. Both produce roughly the same canonical TestRunResult.
For unknown runners we fall back to a regex-based heuristic (counting PASS/FAIL
markers) rather than failing.
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

# vitest markers
_VITEST_FILE = re.compile(r"^(?P<status>FAIL|PASS)\s+(?P<file>\S+)")
_VITEST_TEST = re.compile(
    r"^\s*(?P<marker>[✓✗×→]|x|✓|✗)\s+(?P<name>.+?)(?:\s+\((?P<dur>[\d.]+)\s*ms\))?$"
)
_VITEST_FOOTER = re.compile(
    r"^\s*Tests\s+(?:(?P<failed>\d+)\s+failed[\s|]*)?(?:(?P<passed>\d+)\s+passed[\s|]*)?\((?P<total>\d+)\)"
)
_VITEST_DURATION = re.compile(r"^\s*Duration\s+(?P<dur>[\d.]+)\s*(?P<unit>m?s)")

# jest markers
_JEST_FILE = re.compile(r"^(?P<status>FAIL|PASS)\s+(?P<file>\S+\.\S+)")
_JEST_FAIL_BULLET = re.compile(r"^\s*●\s+(?P<name>.+)$")
_JEST_RESULT_LINE = re.compile(
    r"^Tests:\s+"
    r"(?:(?P<failed>\d+)\s+failed,\s+)?"
    r"(?:(?P<skipped>\d+)\s+skipped,\s+)?"
    r"(?:(?P<passed>\d+)\s+passed,\s+)?"
    r"(?P<total>\d+)\s+total"
)
_JEST_TIME = re.compile(r"^Time:\s+(?P<dur>[\d.]+)\s*s")


class NpmTestCompressor:
    schema = "npm_test"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        # `npm test`, `pnpm test`, `yarn test`, `npm run test` all map here.
        if argv[0] in {"npm", "pnpm", "yarn"}:
            if "test" in argv:
                return True
        # Direct vitest / jest invocation.
        if argv[0] in {"vitest", "jest", "npx"}:
            if argv[0] == "npx" and len(argv) >= 2 and argv[1] not in {"vitest", "jest"}:
                return False
            return True
        return False

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        stdout = raw_stdout.decode("utf-8", errors="replace")
        stderr = raw_stderr.decode("utf-8", errors="replace")
        text = stdout + ("\n" + stderr if stderr.strip() else "")
        result = parse_npm_test(text)
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


def parse_npm_test(text: str) -> TestRunResult:
    """Detect runner and parse accordingly."""
    if _looks_like_jest(text):
        return _parse_jest(text)
    if _looks_like_vitest(text):
        return _parse_vitest(text)
    return _parse_heuristic(text)


def _looks_like_jest(text: str) -> bool:
    return "Test Suites:" in text or bool(_JEST_RESULT_LINE.search(text))


def _looks_like_vitest(text: str) -> bool:
    return "RUN  v" in text or "Vitest" in text or bool(_VITEST_FOOTER.search(text))


# --- jest ---


def _parse_jest(text: str) -> TestRunResult:
    lines = text.splitlines()
    counts = {"passed": 0, "failed": 0, "skipped": 0, "total": 0, "duration": None}
    for line in lines:
        m = _JEST_RESULT_LINE.match(line.strip())
        if m:
            for key in ("passed", "failed", "skipped", "total"):
                value = m.group(key)
                if value:
                    counts[key] = int(value)
            continue
        t = _JEST_TIME.match(line.strip())
        if t:
            counts["duration"] = float(t.group("dur"))
    failures = _parse_jest_failures(lines)
    return TestRunResult(
        runner="jest",
        total=counts["total"],
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        errored=0,
        duration_seconds=counts["duration"],
        failures=tuple(failures),
        warnings=(),
    )


def _parse_jest_failures(lines: list[str]) -> list[TestFailure]:
    failures: list[TestFailure] = []
    current_file: str | None = None
    for idx, line in enumerate(lines):
        file_match = _JEST_FILE.match(line)
        if file_match and file_match.group("status") == "FAIL":
            current_file = file_match.group("file")
            continue
        bullet = _JEST_FAIL_BULLET.match(line)
        if bullet:
            name = bullet.group("name").strip()
            message = ""
            for following in lines[idx + 1 : idx + 12]:
                stripped = following.strip()
                if stripped and not stripped.startswith("●") and not _JEST_FILE.match(following):
                    message = stripped
                    break
            failures.append(
                TestFailure(
                    name=name,
                    file=current_file,
                    line=None,
                    message=message,
                    snippet=(),
                )
            )
    return failures


# --- vitest ---


def _parse_vitest(text: str) -> TestRunResult:
    lines = text.splitlines()
    counts = {"passed": 0, "failed": 0, "total": 0, "duration": None}
    for line in lines:
        m = _VITEST_FOOTER.match(line)
        if m:
            for key in ("failed", "passed", "total"):
                value = m.group(key)
                if value:
                    counts[key] = int(value)
        d = _VITEST_DURATION.match(line)
        if d:
            try:
                duration = float(d.group("dur"))
                if d.group("unit") == "ms":
                    duration = duration / 1000.0
                counts["duration"] = duration
            except ValueError:
                pass

    failures = _parse_vitest_failures(lines)
    return TestRunResult(
        runner="vitest",
        total=counts["total"],
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=0,
        errored=0,
        duration_seconds=counts["duration"],
        failures=tuple(failures),
        warnings=(),
    )


def _parse_vitest_failures(lines: list[str]) -> list[TestFailure]:
    failures: list[TestFailure] = []
    current_file: str | None = None
    for idx, line in enumerate(lines):
        file_match = _VITEST_FILE.match(line)
        if file_match and file_match.group("status") == "FAIL":
            current_file = file_match.group("file")
            continue
        if line.lstrip().startswith("FAIL ") and ">" in line:
            tail = line.split("FAIL", 1)[1].strip()
            name = tail
            message = ""
            for following in lines[idx + 1 : idx + 6]:
                stripped = following.strip()
                if stripped and "Error" in stripped or "Assert" in stripped:
                    message = stripped
                    break
            failures.append(
                TestFailure(
                    name=name,
                    file=current_file,
                    line=None,
                    message=message,
                    snippet=(),
                )
            )
    return failures


# --- heuristic fallback ---


def _parse_heuristic(text: str) -> TestRunResult:
    """Last-resort parsing: count PASS / FAIL markers, no per-failure detail."""
    pass_count = sum(1 for line in text.splitlines() if line.startswith("PASS"))
    fail_count = sum(1 for line in text.splitlines() if line.startswith("FAIL"))
    return TestRunResult(
        runner="npm test",
        total=pass_count + fail_count,
        passed=pass_count,
        failed=fail_count,
        skipped=0,
        errored=0,
        duration_seconds=None,
        failures=(),
        warnings=(),
    )
