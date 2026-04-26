"""
Argv rewriter that nudges known commands toward more token-efficient output
before they reach the subprocess runner.

Two classes of rewrites:

1. Lossless (always applied): replacing the default `git diff` algorithm
   with `--histogram --find-copies-harder -M -C` cuts phantom add+delete
   pairs on refactor diffs without losing any information.

2. Lossy / opt-in (only when ``prefer_compact=True``): pytest's ``--tb=line``,
   cargo's ``--quiet``, jest/vitest ``--reporter=basic`` strip detail the
   compressor would otherwise have to throw away anyway. A 60-80% upstream
   reduction lands before our parser even runs.

Every rewrite respects user intent: if the caller already passed a
conflicting flag (``--no-renames``, ``--tb=long``, ``--reporter=foo``) the
rewrite is skipped for that argv.
"""

from __future__ import annotations


def rewrite_argv(
    argv: tuple[str, ...],
    *,
    prefer_compact: bool = False,
) -> tuple[str, ...]:
    """Return a (possibly rewritten) argv plus the rewrite reason in argv."""
    rewritten = _rewrite_lossless(argv)
    if prefer_compact:
        rewritten = _rewrite_compact(rewritten)
    return rewritten


# --- lossless rewrites ---


_DIFF_BASE_FLAGS: tuple[str, ...] = (
    "--histogram",
    "--find-copies-harder",
    "-M",
    "-C",
)
_DIFF_OPT_OUT_FLAGS: frozenset[str] = frozenset(
    {
        "--patience",
        "--minimal",
        "--myers",
        "--no-renames",
    }
)


def _rewrite_lossless(argv: tuple[str, ...]) -> tuple[str, ...]:
    if len(argv) >= 2 and argv[0] == "git" and argv[1] == "diff":
        if any(a in _DIFF_OPT_OUT_FLAGS for a in argv[2:]):
            return argv
        # Skip if user already passed any of the flags we'd add.
        if any(a in argv[2:] for a in _DIFF_BASE_FLAGS):
            return argv
        return tuple(list(argv) + list(_DIFF_BASE_FLAGS))
    return argv


# --- opt-in compact rewrites ---


def _rewrite_compact(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv:
        return argv
    head = argv[0]
    rest = argv[1:]

    if head == "pytest":
        return _rewrite_pytest(argv)
    if head in {"python", "python3"} and "-m" in rest and "pytest" in rest:
        return _rewrite_pytest(argv)
    if head == "cargo" and len(rest) >= 1 and rest[0] == "test":
        return _rewrite_cargo_test(argv)
    if head in {"jest", "vitest"}:
        return _rewrite_node_runner(argv)
    if head == "npx" and len(rest) >= 1 and rest[0] in {"jest", "vitest"}:
        return _rewrite_node_runner(argv)
    return argv


_PYTEST_USER_TB = ("--tb",)
_PYTEST_QUIET_OPT_OUT = ("-v", "--verbose")


def _rewrite_pytest(argv: tuple[str, ...]) -> tuple[str, ...]:
    extras: list[str] = []
    if not any(a.startswith("--tb") for a in argv):
        extras.append("--tb=line")
    if not any(a in _PYTEST_QUIET_OPT_OUT for a in argv) and "-q" not in argv:
        extras.append("-q")
    if not extras:
        return argv
    return tuple(list(argv) + extras)


def _rewrite_cargo_test(argv: tuple[str, ...]) -> tuple[str, ...]:
    if "--quiet" in argv or "-q" in argv:
        return argv
    if "--verbose" in argv or "-v" in argv:
        return argv
    return tuple(list(argv) + ["--quiet"])


_NODE_REPORTER_OPT_OUT = ("--reporter", "--reporters")


def _rewrite_node_runner(argv: tuple[str, ...]) -> tuple[str, ...]:
    if any(a.split("=")[0] in _NODE_REPORTER_OPT_OUT for a in argv):
        return argv
    return tuple(list(argv) + ["--reporter=basic"])
