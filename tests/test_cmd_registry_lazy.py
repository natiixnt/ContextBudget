"""Tests for the lazy compressor registry."""

from __future__ import annotations

import subprocess
import sys

import pytest

# We deliberately do NOT import any compressor at the top of this file -
# the whole point is to assert that they're loaded only on demand.


@pytest.mark.parametrize(
    "argv,expected_schema",
    [
        (("git", "diff"), "git_diff"),
        (("git", "status"), "git_status"),
        (("git", "log"), "git_log"),
        (("pytest",), "pytest"),
        (("python", "-m", "pytest"), "pytest"),
        (("cargo", "test"), "cargo_test"),
        (("npm", "test"), "npm_test"),
        (("yarn", "test"), "npm_test"),
        (("vitest",), "npm_test"),
        (("go", "test"), "go_test"),
        (("rg", "foo"), "grep"),
        (("grep", "-rn", "foo"), "grep"),
        (("ls", "-l"), "ls"),
        (("tree",), "tree"),
        (("find", ".", "-name", "*.py"), "find"),
        # Tier 2 + new classes shipped after the original 11.
        (("ruff", "check", "."), "lint"),
        (("mypy", "src"), "lint"),
        (("docker", "build", "."), "docker"),
        (("docker", "ps"), "docker"),
        (("pip", "install", "fastapi"), "pkg_install"),
        (("npm", "install"), "pkg_install"),
        (("yarn", "add", "react"), "pkg_install"),
        (("kubectl", "get", "pods"), "kubectl_get"),
        (("py-spy", "record"), "profiler"),
        (("perf", "script"), "profiler"),
        (("cat", "/var/log/app.log"), "json_log"),
        (("tail", "-f", "/var/log/api.ndjson"), "json_log"),
        (("journalctl", "-o", "json"), "json_log"),
        (("coverage", "report"), "coverage"),
        (("python", "-m", "coverage", "report"), "coverage"),
        (("psql", "-c", "EXPLAIN ANALYZE SELECT 1"), "sql_explain"),
        (("mysql", "-e", "EXPLAIN FORMAT=TREE SELECT 1"), "sql_explain"),
        (("webpack", "--json"), "bundle_stats"),
        (("esbuild", "build", "--metafile"), "bundle_stats"),
        (("cat", "dist/stats.json"), "bundle_stats"),
    ],
)
def test_predicates_match_argv(argv, expected_schema):
    """Every cheap argv predicate matches its expected compressor."""
    from redcon.cmd.registry import detect_compressor

    compressor = detect_compressor(argv)
    assert compressor is not None
    assert compressor.schema == expected_schema


def test_unknown_argv_returns_none():
    from redcon.cmd.registry import detect_compressor

    assert detect_compressor(("definitely-not-a-tool",)) is None


def test_load_caches_instance():
    """Two matches in the same process return the same compressor instance."""
    from redcon.cmd.registry import detect_compressor

    a = detect_compressor(("git", "diff"))
    b = detect_compressor(("git", "diff"))
    assert a is b


def test_importing_redcon_cmd_does_not_load_compressors():
    """`import redcon.cmd` must not eagerly load any compressor module.

    We run this in a subprocess with -X importtime and assert that none of
    the compressor modules show up in the import trace. detect_compressor
    will load them on demand later.
    """
    code = "import redcon.cmd"
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", code],
        capture_output=True,
        text=True,
    )
    trace = result.stderr
    # Modules that previously eager-loaded under registry._bootstrap.
    eager_loaded_compressors = [
        "redcon.cmd.compressors.git_diff",
        "redcon.cmd.compressors.git_status",
        "redcon.cmd.compressors.git_log",
        "redcon.cmd.compressors.pytest_compressor",
        "redcon.cmd.compressors.cargo_test_compressor",
        "redcon.cmd.compressors.npm_test_compressor",
        "redcon.cmd.compressors.go_test_compressor",
        "redcon.cmd.compressors.grep_compressor",
        "redcon.cmd.compressors.listing_compressor",
    ]
    for module in eager_loaded_compressors:
        assert module not in trace, (
            f"redcon.cmd should not eager-load {module}; found in import trace:\n{trace}"
        )


def test_compressor_module_loads_on_first_match():
    """Detect-then-import: the compressor module is in sys.modules only after a match."""
    code = (
        "import sys; import redcon.cmd; "
        "before = 'redcon.cmd.compressors.git_diff' in sys.modules; "
        "redcon.cmd.detect_compressor(('git', 'diff')); "
        "after = 'redcon.cmd.compressors.git_diff' in sys.modules; "
        "print(f'{before},{after}')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    before, after = result.stdout.strip().split(",")
    assert before == "False"
    assert after == "True"


def test_predicates_agree_with_compressor_matches_methods():
    """If a compressor's `matches` method drifts from the registry predicate,
    the lazy registry would silently dispatch to the wrong tool. This test
    cross-checks every (argv, compressor) pair from the table above against
    the compressor instance's own `matches` after it's loaded."""
    from redcon.cmd.registry import detect_compressor

    samples = [
        (("git", "diff"), "git_diff"),
        (("git", "status"), "git_status"),
        (("git", "log"), "git_log"),
        (("pytest",), "pytest"),
        (("cargo", "test"), "cargo_test"),
        (("go", "test"), "go_test"),
        (("npm", "test"), "npm_test"),
        (("rg", "foo"), "grep"),
        (("ls", "-l"), "ls"),
        (("tree",), "tree"),
        (("find", "."), "find"),
    ]
    for argv, expected in samples:
        compressor = detect_compressor(argv)
        assert compressor is not None
        assert compressor.schema == expected
        assert compressor.matches(argv) is True
