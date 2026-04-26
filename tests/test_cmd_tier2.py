"""Tests for Tier 2 compressors: lint, docker, pkg_install, kubectl_get,
plus the log-pointer tier."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import CompressorContext
from redcon.cmd.compressors.docker_compressor import (
    DockerCompressor,
    parse_build,
    parse_ps,
)
from redcon.cmd.compressors.kubectl_compressor import (
    KubectlGetCompressor,
    parse_kubectl_get,
)
from redcon.cmd.compressors.lint_compressor import LintCompressor, parse_lint
from redcon.cmd.compressors.pkg_install_compressor import (
    PackageInstallCompressor,
    parse_pkg_install,
)
from redcon.cmd.pipeline import (
    LOG_POINTER_THRESHOLD_BYTES,
    clear_default_cache,
    compress_command,
)
from redcon.cmd.registry import detect_compressor
from redcon.cmd.types import CompressionLevel


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_default_cache()
    yield
    clear_default_cache()


# --- lint ---


MYPY_OUTPUT = b"""\
src/foo.py:42: error: Argument 1 to "process" has incompatible type "str"; expected "int"  [arg-type]
src/foo.py:78: warning: Returning Any from function declared to return "int"  [no-any-return]
src/bar.py:12: error: Cannot determine type of "result"  [name-defined]
src/bar.py:99: error: Missing return statement  [return]
src/bar.py:200: note: Revealed type is "builtins.str"
Found 3 errors in 2 files (checked 5 source files)
"""

RUFF_OUTPUT = b"""\
src/foo.py:1:1: E402 module level import not at top of file
src/foo.py:42:8: F401 'os' imported but unused
src/foo.py:55:1: D100 Missing docstring in public module
src/bar.py:10:5: E501 line too long (102 > 100 characters)
src/bar.py:18:1: F841 local variable 'x' is assigned to but never used
Found 5 errors.
"""


def test_parse_mypy_extracts_severities_and_codes():
    result = parse_lint(MYPY_OUTPUT.decode(), tool="mypy")
    assert result.error_count == 3
    assert result.warning_count == 1
    assert result.note_count == 1
    assert result.file_count == 2
    codes = {issue.code for issue in result.issues if issue.code}
    assert "arg-type" in codes
    assert "name-defined" in codes


def test_parse_ruff_extracts_codes():
    result = parse_lint(RUFF_OUTPUT.decode(), tool="ruff")
    assert result.error_count == 5
    assert result.file_count == 2
    codes = {issue.code for issue in result.issues}
    assert "F401" in codes
    assert "E501" in codes


def test_lint_compressor_compact_keeps_paths_and_codes():
    comp = LintCompressor()
    ctx = _ctx(("mypy",), CompressionLevel.COMPACT)
    out = comp.compress(MYPY_OUTPUT, b"", ctx)
    assert "src/foo.py" in out.text
    assert "src/bar.py" in out.text
    assert "arg-type" in out.text
    assert out.must_preserve_ok is True


def test_lint_compressor_ultra_summarises():
    comp = LintCompressor()
    ctx = _ctx(("ruff",), CompressionLevel.ULTRA, remaining=10, cap=2)
    out = comp.compress(RUFF_OUTPUT, b"", ctx)
    assert "5 errors" in out.text
    assert out.level == CompressionLevel.ULTRA


def test_lint_matches():
    comp = LintCompressor()
    assert comp.matches(("mypy", "src/"))
    assert comp.matches(("ruff", "check", "."))
    assert comp.matches(("python", "-m", "mypy"))
    assert not comp.matches(("git", "status"))


# --- docker ps ---


# Columns aligned by character offset to match docker ps default formatting.
# Header positions: 0, 15, 29, 50, 64, 79, 100.
DOCKER_PS_OUTPUT = b"""\
CONTAINER ID   IMAGE         COMMAND             CREATED       STATUS         PORTS                NAMES
abc1234567de   nginx:1.21    "nginx -g daemon"   2 hours ago   Up 2 hours     0.0.0.0:80->80/tcp   web
9876543210xy   postgres:14   "pg-entrypoint"     1 day ago     Up 1 day       5432/tcp             db
"""


def test_parse_docker_ps():
    result = parse_ps(DOCKER_PS_OUTPUT.decode())
    assert len(result.containers) == 2
    assert result.running_count == 2
    names = {c.name for c in result.containers}
    assert "web" in names
    assert "db" in names


# --- docker build ---


DOCKER_BUILD_LEGACY = b"""\
Sending build context to Docker daemon  2.048kB
Step 1/4 : FROM node:20
 ---> abc123def456
Step 2/4 : WORKDIR /app
 ---> Using cache
 ---> 4567abcdef01
Step 3/4 : COPY package.json ./
 ---> Using cache
 ---> 89abcdef0123
Step 4/4 : RUN npm install
 ---> npm WARN deprecated foo@1.0
 ---> 234567890abc
Successfully built 234567890abc
Successfully tagged myimage:latest
"""

DOCKER_BUILD_FAILED = b"""\
Step 1/3 : FROM node:20
 ---> abc123def456
Step 2/3 : RUN exit 1
 ---> Running in zzz
The command '/bin/sh -c exit 1' returned a non-zero code: 1
"""


def test_parse_docker_build_legacy_success():
    result = parse_build(DOCKER_BUILD_LEGACY.decode())
    assert result.success is True
    assert result.final_image_id == "234567890abc"
    assert "myimage:latest" in result.final_tags
    assert len(result.steps) == 4
    cached = [s for s in result.steps if s.cached]
    assert len(cached) == 2


def test_parse_docker_build_failed():
    result = parse_build(DOCKER_BUILD_FAILED.decode())
    assert result.success is False


def test_docker_compressor_dispatches_ps_and_build():
    comp = DockerCompressor()
    ps_ctx = _ctx(("docker", "ps"), CompressionLevel.COMPACT)
    out = comp.compress(DOCKER_PS_OUTPUT, b"", ps_ctx)
    assert out.schema == "docker_ps"
    assert "web" in out.text

    build_ctx = _ctx(("docker", "build"), CompressionLevel.COMPACT)
    out = comp.compress(DOCKER_BUILD_LEGACY, b"", build_ctx)
    assert out.schema == "docker_build"
    assert "myimage:latest" in out.text or "succeeded" in out.text


# --- pkg install ---


PIP_INSTALL_OUTPUT = b"""\
Collecting numpy
  Downloading numpy-1.26.4.tar.gz (15.6 MB)
Collecting pandas
  Downloading pandas-2.2.1-cp311-cp311-macosx_11_0_arm64.whl (11.4 MB)
Installing collected packages: numpy, pandas
Successfully installed numpy-1.26.4 pandas-2.2.1
DEPRECATION: ABC will be removed in pip 25
"""

NPM_INSTALL_OUTPUT = b"""\
npm warn deprecated request@2.88.2: request has been deprecated, see ...
npm warn deprecated har-validator@5.1.5: this library is no longer supported
added 142 packages, and audited 143 packages in 12s
14 packages are looking for funding
3 vulnerabilities (2 low, 1 moderate)
"""


def test_pip_install_extracts_packages():
    result = parse_pkg_install(PIP_INSTALL_OUTPUT.decode(), tool="pip")
    names = {op.name for op in result.operations if op.op == "added"}
    assert "numpy" in names
    assert "pandas" in names
    assert result.deprecated_count >= 1


def test_npm_install_extracts_counts_and_vulns():
    result = parse_pkg_install(NPM_INSTALL_OUTPUT.decode(), tool="npm")
    assert result.added == 142
    assert result.deprecated_count >= 2
    assert len(result.vulnerabilities) >= 1


def test_pkg_install_compressor_compact():
    comp = PackageInstallCompressor()
    ctx = _ctx(("pip", "install", "numpy", "pandas"), CompressionLevel.COMPACT)
    out = comp.compress(PIP_INSTALL_OUTPUT, b"", ctx)
    assert "numpy" in out.text
    assert "pandas" in out.text


def test_pkg_install_matches():
    comp = PackageInstallCompressor()
    assert comp.matches(("pip", "install", "numpy"))
    assert comp.matches(("npm", "install"))
    assert comp.matches(("pnpm", "i"))
    assert comp.matches(("yarn", "add", "react"))
    assert not comp.matches(("pip", "list"))


# --- kubectl get ---


KUBECTL_PODS = b"""\
NAME                                  READY   STATUS    RESTARTS   AGE
nginx-deployment-66b6c48dd5-abcde     1/1     Running   0          5d
nginx-deployment-66b6c48dd5-fghij     1/1     Running   0          5d
my-app-7d59c5f5b-zywxv                0/1     Pending   3          1h
worker-job-2x9p4                      0/1     Failed    0          10m
"""


def test_parse_kubectl_get_pods():
    result = parse_kubectl_get(KUBECTL_PODS.decode(), kind="pods")
    assert len(result.resources) == 4
    statuses = [r.status for r in result.resources]
    assert "Running" in statuses
    assert "Pending" in statuses
    assert "Failed" in statuses


def test_kubectl_compressor_compact_keeps_pod_names():
    comp = KubectlGetCompressor()
    ctx = _ctx(("kubectl", "get", "pods"), CompressionLevel.COMPACT)
    out = comp.compress(KUBECTL_PODS, b"", ctx)
    assert "my-app" in out.text
    assert "Running" in out.text or "Running:" in out.text


def test_kubectl_matches():
    comp = KubectlGetCompressor()
    assert comp.matches(("kubectl", "get", "pods"))
    assert comp.matches(("kubectl", "get", "deployments", "-A"))
    assert not comp.matches(("kubectl", "describe", "pod", "abc"))


# --- registry integration ---


def test_registry_detects_tier2_tools():
    assert detect_compressor(("mypy",)).schema == "lint"
    assert detect_compressor(("ruff",)).schema == "lint"
    assert detect_compressor(("docker", "ps")).schema == "docker"
    assert detect_compressor(("docker", "build", ".")).schema == "docker"
    assert detect_compressor(("pip", "install", "numpy")).schema == "pkg_install"
    assert detect_compressor(("npm", "install")).schema == "pkg_install"
    assert detect_compressor(("kubectl", "get", "pods")).schema == "kubectl_get"


# --- log-pointer tier ---


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Tiny git repo so we can run real commands inside it."""
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(tmp_path),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True
    )
    (tmp_path / "foo.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True
    )
    return tmp_path


def test_log_pointer_threshold_constant_is_sane():
    # Sanity: somebody could tune this to a useless value. Catch obvious
    # mistakes (e.g. zero, negative, more than 16 MiB).
    assert 64 * 1024 <= LOG_POINTER_THRESHOLD_BYTES <= 16 * 1024 * 1024


def test_log_pointer_engages_for_oversized_output(
    git_repo: Path, monkeypatch
):
    """When raw output exceeds LOG_POINTER_THRESHOLD_BYTES the compressor is
    bypassed and a log_pointer record is returned instead.

    We force the threshold low and use a tiny command (git --version) so we
    can exercise the path without producing actual megabytes of output.
    """
    import redcon.cmd.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "LOG_POINTER_THRESHOLD_BYTES", 5)

    report = compress_command("git --version", cwd=git_repo)
    assert report.output.schema == "log_pointer"
    assert "spilled to disk" in report.output.text
    assert "git --version" in report.output.text
    log_dir = git_repo / ".redcon" / "cmd_runs"
    assert log_dir.exists()
    assert any(p.suffix == ".log" for p in log_dir.iterdir())


def test_log_pointer_includes_tail(git_repo: Path, monkeypatch):
    """The summary should end with the last few lines of raw output."""
    import redcon.cmd.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "LOG_POINTER_THRESHOLD_BYTES", 5)

    report = compress_command("git --version", cwd=git_repo)
    assert "git version" in report.output.text


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
            remaining_tokens=remaining or 200,
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
