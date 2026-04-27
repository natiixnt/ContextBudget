"""
Quality regression harness for every registered compressor.

For each compressor we run the QualityCheck on a small realistic fixture
and a larger synthetic stress fixture. The synthetic ones are generated
deterministically so this file is self-contained: no external test data,
no flakiness from the host environment.
"""

from __future__ import annotations

import pytest

from redcon.cmd.compressors.cargo_test_compressor import CargoTestCompressor
from redcon.cmd.compressors.git_diff import GitDiffCompressor
from redcon.cmd.compressors.git_log import GitLogCompressor
from redcon.cmd.compressors.git_status import GitStatusCompressor
from redcon.cmd.compressors.go_test_compressor import GoTestCompressor
from redcon.cmd.compressors.grep_compressor import GrepCompressor
from redcon.cmd.compressors.listing_compressor import (
    FindCompressor,
    LsCompressor,
    TreeCompressor,
)
from redcon.cmd.compressors.npm_test_compressor import NpmTestCompressor
from redcon.cmd.compressors.profiler_compressor import ProfilerCompressor
from redcon.cmd.compressors.pytest_compressor import PytestCompressor
from redcon.cmd.quality import run_quality_check


def _make_lint():
    from redcon.cmd.compressors.lint_compressor import LintCompressor
    return LintCompressor()


def _make_docker():
    from redcon.cmd.compressors.docker_compressor import DockerCompressor
    return DockerCompressor()


def _make_pkg_install():
    from redcon.cmd.compressors.pkg_install_compressor import (
        PackageInstallCompressor,
    )
    return PackageInstallCompressor()


def _make_kubectl():
    from redcon.cmd.compressors.kubectl_compressor import KubectlGetCompressor
    return KubectlGetCompressor()


# --- synthetic fixture generators ---


def _huge_diff(num_files: int = 12, hunks_per_file: int = 20) -> bytes:
    blocks = []
    for f_idx in range(num_files):
        path = f"src/module_{f_idx}.py"
        head = (
            f"diff --git a/{path} b/{path}\n"
            f"index 1234567..89abcde 100644\n"
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
        )
        body = ""
        for h_idx in range(hunks_per_file):
            start = 10 + h_idx * 20
            body += (
                f"@@ -{start},5 +{start},6 @@ def fn_{h_idx}():\n"
                f"     before_a = 1\n"
                f"-    old_value = {h_idx}\n"
                f"+    new_value = {h_idx + 1}\n"
                f"+    extra_line = '{h_idx}'\n"
                f"     after_a = 2\n"
            )
        blocks.append(head + body)
    return "".join(blocks).encode()


def _massive_pytest(num_failures: int = 30, num_passed: int = 200) -> bytes:
    sections = [
        "============================= test session starts ==============================\n",
        "platform darwin -- Python 3.11.0\n",
        f"collected {num_passed + num_failures} items\n\n",
    ]
    sections.append(
        "tests/big_suite.py "
        + ("." * num_passed + "F" * num_failures)
        + f" [100%]\n\n"
    )
    sections.append("=================================== FAILURES ===================================\n")
    for i in range(num_failures):
        sections.append(
            f"_______________________ test_failure_number_{i} _______________________\n\n"
            f"    def test_failure_number_{i}():\n"
            f">       assert compute({i}) == {i + 100}\n"
            f"E       AssertionError: assert {i} == {i + 100}\n"
            f"E         where {i} = compute({i})\n\n"
            f"tests/big_suite.py:{42 + i}: AssertionError\n"
        )
    sections.append("=========================== short test summary info ============================\n")
    for i in range(num_failures):
        sections.append(
            f"FAILED tests/big_suite.py::test_failure_number_{i} - assert {i} == {i + 100}\n"
        )
    sections.append(
        f"========================= {num_failures} failed, {num_passed} passed in 12.34s ==========================\n"
    )
    return "".join(sections).encode()


def _massive_grep(num_files: int = 50, matches_per_file: int = 12) -> bytes:
    lines = []
    for f_idx in range(num_files):
        path = f"src/pkg_{f_idx // 5}/module_{f_idx}.py"
        for m_idx in range(matches_per_file):
            lines.append(f"{path}:{10 + m_idx * 7}:def function_{f_idx}_{m_idx}():")
    return "\n".join(lines).encode()


def _huge_ls(num_dirs: int = 30, files_per_dir: int = 15) -> bytes:
    out = []
    for d in range(num_dirs):
        out.append(f"./dir_{d}:")
        for f in range(files_per_dir):
            ext = ["py", "ts", "rs", "go", "md"][f % 5]
            out.append(f"file_{d}_{f}.{ext}")
        out.append("")
    return "\n".join(out).encode()


def _massive_find(num_files: int = 500) -> bytes:
    return "\n".join(
        f"./pkg_{i // 25}/sub_{i // 5}/file_{i}.py" for i in range(num_files)
    ).encode()


# --- tiny realistic fixtures (also used in unit tests) ---


_SMALL_DIFF = b"""\
diff --git a/foo.py b/foo.py
index 1234567..89abcde 100644
--- a/foo.py
+++ b/foo.py
@@ -10,7 +10,8 @@ def hello():
     a = 1
-    b = 2
+    b = 3
+    c = 4
     d = 5
diff --git a/bar.py b/bar.py
@@ -0,0 +1,3 @@
+print("hi")
+x = 1
+y = 2
"""

_SMALL_STATUS = b"""\
## main...origin/main [ahead 1]
 M foo.py
?? new.py
A  added.py
"""

_SMALL_LOG = b"""\
commit abc1234567890def1234567890abcdef12345678
Author: Foo <foo@example.com>
Date:   Mon Jan 1 12:00:00 2025

    Add new feature

commit def5678901234abc5678901234567890abcdef12
Author: Bar <bar@example.com>
Date:   Sun Dec 31 23:59:59 2024

    Fix bug
"""

_SMALL_PYTEST = b"""\
============================= test session starts ==============================
platform darwin -- Python 3.11.0
collected 5 items

tests/test_foo.py ..F.F                                                   [100%]

=================================== FAILURES ===================================
________________________ test_widget_renders_correctly _________________________

    def test_widget_renders_correctly(self):
>       assert widget.height == 200
E       AssertionError: assert 100 == 200

tests/test_foo.py:42: AssertionError
________________________________ test_database ________________________________

    def test_database():
>       assert db.is_connected()
E       AttributeError: NoneType

tests/test_foo.py:78: AttributeError
========================= 2 failed, 3 passed in 0.45s ==========================
"""

_SMALL_CARGO = b"""\
running 5 tests
test foo::test_basic ... ok
test foo::test_advanced ... FAILED

failures:

---- foo::test_advanced stdout ----
thread 'foo::test_advanced' panicked at 'assertion failed', src/foo.rs:42:5

failures:
    foo::test_advanced

test result: FAILED. 4 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.05s
"""

_SMALL_GREP = b"""\
src/foo.py:10:def foo():
src/foo.py:42:    return foo
src/bar.py:5:foo = 1
"""

_SMALL_LS_LONG = b"""\
total 24
-rw-r--r--  1 user staff  1234 Jan  1 12:00 foo.py
-rw-r--r--  1 user staff   567 Jan  1 12:00 bar.py
drwxr-xr-x  3 user staff    96 Jan  1 12:00 subdir
"""

_SMALL_TREE = b"""\
.
\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 foo.py
\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 subdir
    \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 baz.py
"""

_SMALL_FIND = b"""\
./src/foo.py
./src/bar.py
./tests/test_foo.py
"""

_SMALL_GO_TEST = b"""\
=== RUN   TestFoo
--- PASS: TestFoo (0.00s)
=== RUN   TestBar
    bar_test.go:42: expected 200, got 100
--- FAIL: TestBar (0.00s)
FAIL    github.com/foo/bar    0.012s
"""

# --- Tier 2 fixtures ---


_MYPY_LARGE = b"""\
src/foo.py:42: error: Argument 1 to "process" has incompatible type "str"; expected "int"  [arg-type]
src/foo.py:78: warning: Returning Any from function declared to return "int"  [no-any-return]
src/foo.py:120: error: Cannot find module 'missing'  [import]
src/bar.py:12: error: Cannot determine type of "result"  [name-defined]
src/bar.py:45: error: Cannot determine type of "result"  [name-defined]
src/bar.py:99: error: Missing return statement  [return]
src/baz.py:10: error: Argument 1 to "save" has incompatible type "Optional[str]"; expected "str"  [arg-type]
src/baz.py:32: warning: Unused import: 'os'  [unused-import]
src/baz.py:55: error: Type annotation needed  [var-annotated]
""" + b"\n".join(
    f"src/big_module_{i}.py:{i + 10}: error: Test error {i} [code-{i}]".encode()
    for i in range(40)
) + b"\nFound 49 errors in 4 files\n"


_RUFF_FIXTURE = b"\n".join(
    [
        b"src/api/auth.py:1:1: E402 module level import not at top of file",
        b"src/api/auth.py:42:8: F401 'os' imported but unused",
        b"src/api/handlers.py:55:1: D100 Missing docstring in public module",
        b"src/api/handlers.py:78:5: E501 line too long (102 > 100 characters)",
        b"src/api/handlers.py:128:1: F841 local variable 'x' is assigned to but never used",
        b"src/api/handlers.py:200:1: B007 Loop control variable 'i' not used",
        b"src/db/connection.py:10:5: E501 line too long (110 > 100 characters)",
        b"src/db/connection.py:18:1: F841 local variable 'conn' is assigned to but never used",
        b"src/utils/strings.py:5:1: D100 Missing docstring in public module",
    ]
    + [
        f"src/big/module_{i}.py:{20 + i}:1: E501 line too long ({110 + i} > 100 characters)".encode()
        for i in range(60)
    ]
    + [b"Found 69 errors."]
) + b"\n"


_DOCKER_BUILD_FIXTURE = b"""\
Sending build context to Docker daemon  4.096kB
Step 1/8 : FROM node:20-alpine
 ---> abc123def456
Step 2/8 : WORKDIR /app
 ---> Using cache
 ---> 4567abcdef01
Step 3/8 : COPY package.json package-lock.json ./
 ---> Using cache
 ---> 89abcdef0123
Step 4/8 : RUN npm ci --only=production
 ---> Running in container1
npm warn deprecated foo@1.0
npm warn deprecated bar@2.0
added 142 packages, audited 143 packages in 12s
 ---> 234567890abc
Step 5/8 : COPY src ./src
 ---> def012345678
Step 6/8 : COPY tsconfig.json ./
 ---> Using cache
 ---> aaa111222333
Step 7/8 : RUN npm run build
 ---> Running in container2
> tsc -p tsconfig.json
 ---> bbb222333444
Step 8/8 : CMD ["node", "dist/server.js"]
 ---> ccc333444555
Successfully built ccc333444555
Successfully tagged my-app:latest
"""


_PIP_INSTALL_FIXTURE = b"""\
Collecting numpy>=1.24
  Downloading numpy-1.26.4-cp311-cp311-macosx_11_0_arm64.whl (14.0 MB)
Collecting pandas>=2.0
  Downloading pandas-2.2.1-cp311-cp311-macosx_11_0_arm64.whl (11.4 MB)
Collecting fastapi
  Downloading fastapi-0.110.0-py3-none-any.whl (92 kB)
Collecting uvicorn[standard]
  Downloading uvicorn-0.29.0-py3-none-any.whl (60 kB)
Collecting pydantic>=2.0
  Downloading pydantic-2.6.4-py3-none-any.whl (394 kB)
Installing collected packages: numpy, pandas, fastapi, uvicorn, pydantic
Successfully installed fastapi-0.110.0 numpy-1.26.4 pandas-2.2.1 pydantic-2.6.4 uvicorn-0.29.0
DEPRECATION: Loading egg at /path/to/old.egg is deprecated and will be removed in pip 25
"""


_KUBECTL_PODS_FIXTURE = b"""\
NAME                                  READY   STATUS    RESTARTS   AGE
nginx-deployment-66b6c48dd5-aaaaa     1/1     Running   0          5d
nginx-deployment-66b6c48dd5-bbbbb     1/1     Running   0          5d
nginx-deployment-66b6c48dd5-ccccc     1/1     Running   0          5d
api-server-7d59c5f5b-ddddd            1/1     Running   2          1d
api-server-7d59c5f5b-eeeee            0/1     Pending   3          1h
worker-job-fffff                      0/1     Failed    0          10m
worker-job-ggggg                      0/1     Failed    0          5m
postgres-statefulset-0                1/1     Running   0          90d
redis-master-7bc8b9ddc7-hhhhh         1/1     Running   0          30d
"""

_SMALL_JEST = b"""\
PASS  src/widget.test.js
FAIL  src/database.test.js
  \xe2\x97\x8f Database connection should succeed

    expect(received).toBe(expected)

    Expected: true
    Received: false

Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 4 passed, 5 total
Time:        2.345 s
"""


def _profiler_fixture() -> bytes:
    """Synthetic 200-line py-spy collapsed-stack output, deterministic seed."""
    import random

    rng = random.Random(0)
    modules = ["main", "parse", "compile", "run", "cache", "render", "store"]
    funcs = ["init", "load", "process", "serialize", "lookup", "fetch"]
    lines: list[str] = []
    for _ in range(200):
        depth = rng.randint(2, 5)
        stack = ";".join(
            f"{rng.choice(modules)}.{rng.choice(funcs)}" for _ in range(depth)
        )
        samples = rng.randint(1, 50)
        lines.append(f"{stack} {samples}")
    return ("\n".join(lines) + "\n").encode("utf-8")


# --- parametrized fixtures ---


CASES = [
    (
        "git_diff_small",
        GitDiffCompressor(),
        _SMALL_DIFF,
        b"",
        ("git", "diff"),
    ),
    (
        "git_diff_huge",
        GitDiffCompressor(),
        _huge_diff(),
        b"",
        ("git", "diff"),
    ),
    (
        "git_status",
        GitStatusCompressor(),
        _SMALL_STATUS,
        b"",
        ("git", "status"),
    ),
    (
        "git_log",
        GitLogCompressor(),
        _SMALL_LOG,
        b"",
        ("git", "log"),
    ),
    (
        "pytest_small",
        PytestCompressor(),
        _SMALL_PYTEST,
        b"",
        ("pytest",),
    ),
    (
        "pytest_massive",
        PytestCompressor(),
        _massive_pytest(),
        b"",
        ("pytest",),
    ),
    (
        "cargo_test",
        CargoTestCompressor(),
        _SMALL_CARGO,
        b"",
        ("cargo", "test"),
    ),
    (
        "go_test",
        GoTestCompressor(),
        _SMALL_GO_TEST,
        b"",
        ("go", "test"),
    ),
    (
        "npm_test_jest",
        NpmTestCompressor(),
        _SMALL_JEST,
        b"",
        ("npm", "test"),
    ),
    (
        "grep_small",
        GrepCompressor(),
        _SMALL_GREP,
        b"",
        ("rg", "foo"),
    ),
    (
        "grep_massive",
        GrepCompressor(),
        _massive_grep(),
        b"",
        ("rg", "function"),
    ),
    (
        "ls",
        LsCompressor(),
        _SMALL_LS_LONG,
        b"",
        ("ls", "-l"),
    ),
    (
        "ls_huge",
        LsCompressor(),
        _huge_ls(),
        b"",
        ("ls", "-R"),
    ),
    (
        "tree",
        TreeCompressor(),
        _SMALL_TREE,
        b"",
        ("tree",),
    ),
    (
        "find",
        FindCompressor(),
        _SMALL_FIND,
        b"",
        ("find", ".", "-name", "*.py"),
    ),
    (
        "find_massive",
        FindCompressor(),
        _massive_find(),
        b"",
        ("find", ".", "-type", "f"),
    ),
    # Tier 2 compressors
    (
        "mypy_large",
        _make_lint(),
        _MYPY_LARGE,
        b"",
        ("mypy", "src/"),
    ),
    (
        "ruff_typical",
        _make_lint(),
        _RUFF_FIXTURE,
        b"",
        ("ruff", "check", "."),
    ),
    (
        "docker_build_typical",
        _make_docker(),
        _DOCKER_BUILD_FIXTURE,
        b"",
        ("docker", "build", "."),
    ),
    (
        "pip_install_typical",
        _make_pkg_install(),
        _PIP_INSTALL_FIXTURE,
        b"",
        ("pip", "install", "fastapi"),
    ),
    (
        "kubectl_pods_typical",
        _make_kubectl(),
        _KUBECTL_PODS_FIXTURE,
        b"",
        ("kubectl", "get", "pods"),
    ),
    (
        "profiler_typical",
        ProfilerCompressor(),
        _profiler_fixture(),
        b"",
        ("py-spy", "record"),
    ),
]


@pytest.mark.parametrize(
    "name,compressor,raw_stdout,raw_stderr,argv",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_quality_check(
    name: str,
    compressor,
    raw_stdout: bytes,
    raw_stderr: bytes,
    argv: tuple[str, ...],
):
    check = run_quality_check(
        compressor,
        raw_stdout=raw_stdout,
        raw_stderr=raw_stderr,
        argv=argv,
    )
    failures = check.failures()
    assert not failures, "\n".join(failures)


def test_harness_detects_inflation_regression():
    """A compressor whose verbose level inflates by >10% must fail the gate."""
    from redcon.cmd.compressors.base import CompressorContext
    from redcon.cmd.types import CompressedOutput, CompressionLevel
    from redcon.cmd.budget import BudgetHint

    class InflatingCompressor:
        schema = "fake_inflate"
        must_preserve_patterns = ()

        def matches(self, argv):
            return False

        def compress(self, stdout, stderr, ctx):
            text = stdout.decode("utf-8", errors="replace")
            inflated = text + "\n" + ("padding\n" * 200)
            from redcon.core.tokens import estimate_tokens

            return CompressedOutput(
                text=inflated,
                level=CompressionLevel.VERBOSE,
                schema=self.schema,
                original_tokens=estimate_tokens(text),
                compressed_tokens=estimate_tokens(inflated),
                must_preserve_ok=True,
                truncated=False,
            )

    big = ("input " * 200).encode()
    check = run_quality_check(
        InflatingCompressor(),
        raw_stdout=big,
        argv=("fake",),
    )
    failures = check.failures()
    # We expect at least one threshold violation across the three levels.
    assert any("below floor" in f for f in failures)
