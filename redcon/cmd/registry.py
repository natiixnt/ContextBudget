"""
Registry of available compressors with lazy module loading.

Each registered entry carries a cheap argv predicate plus an importer that
loads the compressor module + instantiates the class only when the predicate
matches. So `import redcon.cmd` no longer pulls in 11 compressor modules
(and their transitive `redcon.core.tokens` -> `redcon.schemas.models` chain) -
those imports happen on the first `detect_compressor(argv)` that needs them.

Once a compressor is loaded its instance is cached on the entry so repeat
matches in the same process are O(1) attribute lookup.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Callable

from redcon.cmd.compressors.base import Compressor


@dataclass(slots=True)
class _LazyEntry:
    name: str
    matcher: Callable[[tuple[str, ...]], bool]
    module_name: str
    class_name: str
    instance: Compressor | None = field(default=None)

    def load(self) -> Compressor:
        if self.instance is None:
            module = importlib.import_module(self.module_name)
            cls = getattr(module, self.class_name)
            self.instance = cls()
        return self.instance


_REGISTRY: list[_LazyEntry] = []


def register_compressor(compressor: Compressor) -> None:
    """Register a fully-instantiated compressor (test / plugin entry point).

    Keeps backwards compat for callers that already build a Compressor and
    want it in the registry. Wraps it in a _LazyEntry whose matcher uses
    the compressor's own ``matches`` method.
    """
    name = getattr(compressor, "schema", compressor.__class__.__name__)
    entry = _LazyEntry(
        name=name,
        matcher=compressor.matches,
        module_name="",
        class_name="",
    )
    entry.instance = compressor
    _REGISTRY.append(entry)


def register_lazy(
    name: str,
    matcher: Callable[[tuple[str, ...]], bool],
    module_name: str,
    class_name: str,
) -> None:
    """Register a compressor that is imported on first matching argv."""
    _REGISTRY.append(
        _LazyEntry(
            name=name,
            matcher=matcher,
            module_name=module_name,
            class_name=class_name,
        )
    )


def detect_compressor(argv: tuple[str, ...]) -> Compressor | None:
    """Return the first registered compressor whose matcher accepts argv."""
    for entry in _REGISTRY:
        if entry.matcher(argv):
            return entry.load()
    return None


def registered_schemas() -> tuple[str, ...]:
    """List schemas for registered compressors. Useful for diagnostics."""
    return tuple(entry.name for entry in _REGISTRY)


def reset_registry() -> None:
    """Drop registry to empty state. Test helper; not part of the public API."""
    _REGISTRY.clear()
    _bootstrap_lazy()


# --- argv predicates ---
#
# Kept here as plain functions so the matcher can run without importing the
# compressor module. They mirror the Compressor.matches methods inside each
# compressor class - if those drift the bootstrap test in
# tests/test_cmd_registry_lazy.py catches the divergence.


def _is_git_diff(argv: tuple[str, ...]) -> bool:
    return len(argv) >= 2 and argv[0] == "git" and argv[1] == "diff"


def _is_git_status(argv: tuple[str, ...]) -> bool:
    return len(argv) >= 2 and argv[0] == "git" and argv[1] == "status"


def _is_git_log(argv: tuple[str, ...]) -> bool:
    return len(argv) >= 2 and argv[0] == "git" and argv[1] == "log"


def _is_pytest(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    if argv[0] == "pytest":
        return True
    if argv[0] in {"python", "python3"} and "-m" in argv and "pytest" in argv:
        return True
    return False


def _is_cargo_test(argv: tuple[str, ...]) -> bool:
    return len(argv) >= 2 and argv[0] == "cargo" and argv[1] == "test"


def _is_npm_test(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    if argv[0] in {"npm", "pnpm", "yarn"} and "test" in argv:
        return True
    if argv[0] in {"vitest", "jest"}:
        return True
    if argv[0] == "npx" and len(argv) >= 2 and argv[1] in {"vitest", "jest"}:
        return True
    return False


def _is_go_test(argv: tuple[str, ...]) -> bool:
    return len(argv) >= 2 and argv[0] == "go" and argv[1] == "test"


def _is_grep(argv: tuple[str, ...]) -> bool:
    return bool(argv) and argv[0] in {"grep", "rg", "egrep", "fgrep"}


def _is_ls(argv: tuple[str, ...]) -> bool:
    return bool(argv) and argv[0] == "ls"


def _is_tree(argv: tuple[str, ...]) -> bool:
    return bool(argv) and argv[0] == "tree"


def _is_find(argv: tuple[str, ...]) -> bool:
    return bool(argv) and argv[0] == "find"


def _is_lint(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    if argv[0] in {"mypy", "ruff"}:
        return True
    if argv[0] in {"python", "python3"} and "-m" in argv and (
        "mypy" in argv or "ruff" in argv
    ):
        return True
    return False


def _is_docker(argv: tuple[str, ...]) -> bool:
    if len(argv) < 2:
        return False
    if argv[0] not in {"docker", "podman"}:
        return False
    return argv[1] in {"ps", "build", "image"}


def _is_pkg_install(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    if argv[0] == "pip" and len(argv) >= 2 and argv[1] in {"install", "uninstall"}:
        return True
    if argv[0] in {"python", "python3"} and "-m" in argv and "pip" in argv:
        if "install" in argv or "uninstall" in argv:
            return True
    if argv[0] in {"npm", "pnpm"} and len(argv) >= 2 and argv[1] in {
        "install",
        "i",
        "ci",
        "uninstall",
        "remove",
        "rm",
    }:
        return True
    if argv[0] == "yarn" and len(argv) >= 2 and argv[1] in {"add", "remove", "install"}:
        return True
    return False


def _is_kubectl_get(argv: tuple[str, ...]) -> bool:
    if len(argv) < 2:
        return False
    return argv[0] == "kubectl" and argv[1] == "get"


def _is_profiler(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    if argv[0] == "py-spy":
        return len(argv) >= 2 and argv[1] in {"record", "dump", "top"}
    if argv[0] == "perf":
        return len(argv) >= 2 and argv[1] in {"record", "script", "report"}
    if argv[0] == "flamegraph.pl":
        return True
    return False


def _bootstrap_lazy() -> None:
    """Register every built-in compressor as a lazy entry."""
    register_lazy(
        "git_diff",
        _is_git_diff,
        "redcon.cmd.compressors.git_diff",
        "GitDiffCompressor",
    )
    register_lazy(
        "git_status",
        _is_git_status,
        "redcon.cmd.compressors.git_status",
        "GitStatusCompressor",
    )
    register_lazy(
        "git_log",
        _is_git_log,
        "redcon.cmd.compressors.git_log",
        "GitLogCompressor",
    )
    register_lazy(
        "pytest",
        _is_pytest,
        "redcon.cmd.compressors.pytest_compressor",
        "PytestCompressor",
    )
    register_lazy(
        "cargo_test",
        _is_cargo_test,
        "redcon.cmd.compressors.cargo_test_compressor",
        "CargoTestCompressor",
    )
    register_lazy(
        "npm_test",
        _is_npm_test,
        "redcon.cmd.compressors.npm_test_compressor",
        "NpmTestCompressor",
    )
    register_lazy(
        "go_test",
        _is_go_test,
        "redcon.cmd.compressors.go_test_compressor",
        "GoTestCompressor",
    )
    register_lazy(
        "grep",
        _is_grep,
        "redcon.cmd.compressors.grep_compressor",
        "GrepCompressor",
    )
    register_lazy(
        "ls",
        _is_ls,
        "redcon.cmd.compressors.listing_compressor",
        "LsCompressor",
    )
    register_lazy(
        "tree",
        _is_tree,
        "redcon.cmd.compressors.listing_compressor",
        "TreeCompressor",
    )
    register_lazy(
        "find",
        _is_find,
        "redcon.cmd.compressors.listing_compressor",
        "FindCompressor",
    )
    register_lazy(
        "lint",
        _is_lint,
        "redcon.cmd.compressors.lint_compressor",
        "LintCompressor",
    )
    register_lazy(
        "docker",
        _is_docker,
        "redcon.cmd.compressors.docker_compressor",
        "DockerCompressor",
    )
    register_lazy(
        "pkg_install",
        _is_pkg_install,
        "redcon.cmd.compressors.pkg_install_compressor",
        "PackageInstallCompressor",
    )
    register_lazy(
        "kubectl_get",
        _is_kubectl_get,
        "redcon.cmd.compressors.kubectl_compressor",
        "KubectlGetCompressor",
    )
    register_lazy(
        "profiler",
        _is_profiler,
        "redcon.cmd.compressors.profiler_compressor",
        "ProfilerCompressor",
    )


_bootstrap_lazy()
