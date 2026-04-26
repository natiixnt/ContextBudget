"""Tests for pytest, cargo test, npm test (vitest+jest), and go test compressors."""

from __future__ import annotations

import pytest

from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import CompressorContext
from redcon.cmd.compressors.cargo_test_compressor import (
    CargoTestCompressor,
    parse_cargo_test,
)
from redcon.cmd.compressors.go_test_compressor import (
    GoTestCompressor,
    parse_go_test,
)
from redcon.cmd.compressors.npm_test_compressor import (
    NpmTestCompressor,
    parse_npm_test,
)
from redcon.cmd.compressors.pytest_compressor import (
    PytestCompressor,
    parse_pytest,
)
from redcon.cmd.compressors.test_format import format_test_result
from redcon.cmd.registry import detect_compressor
from redcon.cmd.types import CompressionLevel, TestFailure, TestRunResult

# --- pytest fixtures ---

PYTEST_OUTPUT = b"""\
============================= test session starts ==============================
platform darwin -- Python 3.11.0
collected 5 items

tests/test_foo.py ..F.F                                                  [100%]

=================================== FAILURES ===================================
________________________ test_widget_renders_correctly _________________________

self = <test_foo.TestWidget object at 0x7f...>

    def test_widget_renders_correctly(self):
>       assert widget.height == 200
E       AssertionError: assert 100 == 200
E        +  where 100 = <Widget>.height

tests/test_foo.py:42: AssertionError
________________________________ test_database ________________________________

    def test_database():
>       assert db.is_connected()
E       AttributeError: 'NoneType' object has no attribute 'is_connected'

tests/test_foo.py:78: AttributeError
=========================== short test summary info ============================
FAILED tests/test_foo.py::TestWidget::test_widget_renders_correctly - assert 100 == 200
FAILED tests/test_foo.py::test_database - AttributeError: 'NoneType' object has no attribute 'is_connected'
========================= 2 failed, 3 passed in 0.45s ==========================
"""

# --- cargo test fixtures ---

CARGO_TEST_OUTPUT = b"""\
running 5 tests
test foo::test_basic ... ok
test foo::test_advanced ... FAILED
test foo::test_skip ... ignored
test bar::test_simple ... ok
test bar::test_panic ... FAILED

failures:

---- foo::test_advanced stdout ----
thread 'foo::test_advanced' panicked at 'assertion failed: x == y', src/foo.rs:42:5
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

---- bar::test_panic stdout ----
thread 'bar::test_panic' panicked at 'unwrap on None', src/bar.rs:88:9

failures:
    foo::test_advanced
    bar::test_panic

test result: FAILED. 2 passed; 2 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.05s
"""

CARGO_TEST_STDERR = b"""\
   Compiling foo v0.1.0 (/repo)
warning: unused variable: `x`
  --> src/foo.rs:10:9
warning: unused import: `crate::bar`
"""

# --- jest fixture ---

JEST_OUTPUT = b"""\
PASS  src/widget.test.js
FAIL  src/database.test.js
  \xe2\x97\x8f Database connection should succeed

    expect(received).toBe(expected)

    Expected: true
    Received: false

      45 |   it('should succeed', () => {
    > 46 |     expect(db.connected).toBe(true);
         |                          ^
      47 |   });

      at Object.<anonymous> (src/database.test.js:46:23)

Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 4 passed, 5 total
Time:        2.345 s
"""

# --- vitest fixture ---

VITEST_OUTPUT = b"""\
 RUN  v1.0.0 /repo

 \xe2\x9c\x93 src/foo.test.ts (3)
 FAIL  src/bar.test.ts (2)

 FAIL  src/bar.test.ts > Bar > should compute
AssertionError: expected 100 to be 200
 \xe2\x9d\xaf src/bar.test.ts:23:34

 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 4 passed (5)
   Duration  234ms
"""

# --- go test fixture ---

GO_TEST_OUTPUT = b"""\
=== RUN   TestFoo
--- PASS: TestFoo (0.00s)
=== RUN   TestBar
    bar_test.go:42: expected 200, got 100
--- FAIL: TestBar (0.00s)
=== RUN   TestSkippy
--- SKIP: TestSkippy (0.00s)
=== RUN   TestPanic
--- FAIL: TestPanic (0.01s)
FAIL
exit status 1
FAIL    github.com/foo/bar    0.012s
"""


# --- pytest tests ---


def test_pytest_parser_basic():
    result = parse_pytest(PYTEST_OUTPUT.decode())
    assert result.runner == "pytest"
    assert result.passed == 3
    assert result.failed == 2
    assert result.duration_seconds == pytest.approx(0.45)
    names = {f.name for f in result.failures}
    assert "test_widget_renders_correctly" in names
    assert "test_database" in names


def test_pytest_compressor_compact_keeps_failures():
    comp = PytestCompressor()
    ctx = _ctx(("pytest",), CompressionLevel.COMPACT)
    out = comp.compress(PYTEST_OUTPUT, b"", ctx)
    assert "test_widget_renders_correctly" in out.text
    assert "test_database" in out.text
    assert out.must_preserve_ok is True


def test_pytest_compressor_ultra_summary():
    comp = PytestCompressor()
    ctx = _ctx(("pytest",), CompressionLevel.ULTRA, remaining=10, cap=2)
    out = comp.compress(PYTEST_OUTPUT, b"", ctx)
    assert out.level == CompressionLevel.ULTRA
    assert "3/5 passed" in out.text or "3 passed" in out.text


def test_pytest_compressor_real_reduction():
    comp = PytestCompressor()
    ctx = _ctx(("pytest",), CompressionLevel.COMPACT, remaining=400, cap=4000)
    out = comp.compress(PYTEST_OUTPUT, b"", ctx)
    assert out.compressed_tokens < out.original_tokens
    assert out.reduction_pct > 0


def test_pytest_matches_python_dash_m():
    comp = PytestCompressor()
    assert comp.matches(("pytest",))
    assert comp.matches(("python", "-m", "pytest"))
    assert comp.matches(("python3", "-m", "pytest", "tests/"))
    assert not comp.matches(("git", "diff"))


# --- cargo test tests ---


def test_cargo_parser():
    result = parse_cargo_test(CARGO_TEST_OUTPUT.decode(), CARGO_TEST_STDERR.decode())
    assert result.runner == "cargo test"
    assert result.passed == 2
    assert result.failed == 2
    assert result.skipped == 1
    names = {f.name for f in result.failures}
    assert "foo::test_advanced" in names
    assert "bar::test_panic" in names
    assert any("unused variable" in w for w in result.warnings)


def test_cargo_compressor_compact_keeps_panic_locations():
    comp = CargoTestCompressor()
    ctx = _ctx(("cargo", "test"), CompressionLevel.COMPACT)
    out = comp.compress(CARGO_TEST_OUTPUT, CARGO_TEST_STDERR, ctx)
    assert "foo::test_advanced" in out.text
    assert "bar::test_panic" in out.text
    assert out.must_preserve_ok is True


def test_cargo_matches():
    comp = CargoTestCompressor()
    assert comp.matches(("cargo", "test"))
    assert comp.matches(("cargo", "test", "--release"))
    assert not comp.matches(("cargo", "build"))


# --- npm/jest tests ---


def test_jest_parser():
    result = parse_npm_test(JEST_OUTPUT.decode())
    assert result.runner == "jest"
    assert result.passed == 4
    assert result.failed == 1
    assert result.total == 5
    assert result.duration_seconds == pytest.approx(2.345)
    names = [f.name for f in result.failures]
    assert any("Database" in n for n in names)


def test_npm_compressor_compact_jest():
    comp = NpmTestCompressor()
    ctx = _ctx(("npm", "test"), CompressionLevel.COMPACT)
    out = comp.compress(JEST_OUTPUT, b"", ctx)
    assert "Database" in out.text
    assert out.must_preserve_ok is True


def test_vitest_parser():
    result = parse_npm_test(VITEST_OUTPUT.decode())
    assert result.runner == "vitest"
    assert result.passed == 4
    assert result.failed == 1
    assert result.total == 5


def test_npm_matches():
    comp = NpmTestCompressor()
    assert comp.matches(("npm", "test"))
    assert comp.matches(("pnpm", "test"))
    assert comp.matches(("yarn", "test"))
    assert comp.matches(("vitest",))
    assert comp.matches(("jest", "--coverage"))
    assert not comp.matches(("npm", "install"))


# --- go test tests ---


def test_go_test_parser():
    result = parse_go_test(GO_TEST_OUTPUT.decode())
    assert result.runner == "go test"
    assert result.passed == 1
    assert result.failed == 2
    assert result.skipped == 1
    names = {f.name for f in result.failures}
    assert "TestBar" in names
    assert "TestPanic" in names


def test_go_test_compressor_compact():
    comp = GoTestCompressor()
    ctx = _ctx(("go", "test"), CompressionLevel.COMPACT)
    out = comp.compress(GO_TEST_OUTPUT, b"", ctx)
    assert "TestBar" in out.text
    assert "TestPanic" in out.text
    assert out.must_preserve_ok is True


def test_go_matches():
    comp = GoTestCompressor()
    assert comp.matches(("go", "test"))
    assert comp.matches(("go", "test", "./..."))
    assert not comp.matches(("go", "build"))


# --- shared format tests ---


def test_format_test_result_no_failures_clean_summary():
    result = TestRunResult(
        runner="pytest",
        total=10,
        passed=10,
        failed=0,
        skipped=0,
        errored=0,
        duration_seconds=1.5,
        failures=(),
        warnings=(),
    )
    out = format_test_result(result, CompressionLevel.COMPACT)
    assert "10 passed" in out
    assert "FAIL" not in out


def test_format_ultra_includes_first_failure_name():
    failures = (
        TestFailure(name="my_test", file="t.py", line=1, message="boom", snippet=()),
    )
    result = TestRunResult(
        runner="pytest",
        total=2,
        passed=1,
        failed=1,
        skipped=0,
        errored=0,
        duration_seconds=0.1,
        failures=failures,
        warnings=(),
    )
    out = format_test_result(result, CompressionLevel.ULTRA)
    assert "my_test" in out


# --- registry integration ---


def test_registry_detects_test_runners():
    assert detect_compressor(("pytest",)).schema == "pytest"
    assert detect_compressor(("cargo", "test")).schema == "cargo_test"
    assert detect_compressor(("npm", "test")).schema == "npm_test"
    assert detect_compressor(("go", "test")).schema == "go_test"


# --- helpers ---


def _ctx(
    argv: tuple[str, ...],
    level: CompressionLevel,
    *,
    remaining: int | None = None,
    cap: int | None = None,
) -> CompressorContext:
    if level == CompressionLevel.VERBOSE:
        hint = BudgetHint(remaining_tokens=100_000, max_output_tokens=10_000)
    elif level == CompressionLevel.COMPACT:
        hint = BudgetHint(
            remaining_tokens=remaining or 400,
            max_output_tokens=cap or 4_000,
        )
    else:
        hint = BudgetHint(
            remaining_tokens=remaining or 10,
            max_output_tokens=cap or 2,
        )
    return CompressorContext(
        argv=argv, cwd=".", returncode=0, hint=hint
    )
