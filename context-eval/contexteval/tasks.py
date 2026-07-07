"""Task extraction from git history.

A benchmark task is a real commit: the commit subject is the task
description an agent would receive, and the set of source files the commit
modified is the ground truth a context-selection tool should surface.
Tools are evaluated against the repository state at the commit's parent,
so nothing about the change itself can leak into the selection.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# File types that count as selectable source context. Ground-truth filtering
# and the selection universe use the same set so coverage is well defined.
SOURCE_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".rb",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".toml",
    ".yml",
    ".yaml",
    ".sh",
    ".sql",
}

# Directories excluded from both the universe and ground truth: generated
# reports, prose, and the harness itself must not influence results.
EXCLUDED_PREFIXES = (
    "research/",
    "docs/",
    "examples/sample-outputs/",
    ".github/wiki/",
    "context-eval/",
)

_CONVENTIONAL_PREFIX = re.compile(r"^([a-z]+)(\([^)]*\))?!?:\s*")

# Commit types whose subject meaningfully describes the files they touch.
# docs/style/chore/ci subjects ("clear lint debt", "regen reports") name a
# process, not code, so coverage against them would measure noise.
_TASKLIKE_TYPES = {"feat", "fix", "perf", "refactor"}


@dataclass(frozen=True)
class Task:
    """One benchmark task derived from a single commit."""

    commit: str
    parent: str
    description: str
    ground_truth: tuple[str, ...]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def is_source_path(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    # Hidden directories (.github, .vscode, ...) are excluded on both sides:
    # most context tools skip them by convention, so counting them would
    # penalise convention-followers rather than measure selection quality.
    if any(part.startswith(".") for part in path.split("/")[:-1]):
        return False
    return Path(path).suffix.lower() in SOURCE_EXTS


def extract_tasks(
    repo: Path,
    *,
    rev: str = "HEAD",
    max_tasks: int = 20,
    min_files: int = 1,
    max_files: int = 8,
) -> list[Task]:
    """Walk the history of *rev* and turn suitable commits into tasks.

    A commit qualifies when its subject is descriptive enough to act as a
    task and it modified between *min_files* and *max_files* source files.
    Only modified/renamed/deleted paths count as ground truth: files the
    commit newly added did not exist at the parent state, so no selection
    tool could have picked them.
    """
    tasks: list[Task] = []
    log = _git(repo, "log", rev, "--no-merges", "--format=%H|%s")
    for line in log.splitlines():
        sha, _, subject = line.partition("|")
        match = _CONVENTIONAL_PREFIX.match(subject)
        if match and match.group(1) not in _TASKLIKE_TYPES:
            continue
        description = _CONVENTIONAL_PREFIX.sub("", subject).strip()
        if len(description) < 15:
            continue  # subjects like "wip" make meaningless tasks

        name_status = _git(repo, "show", "--name-status", "--format=", sha)
        ground_truth: set[str] = set()
        for row in name_status.splitlines():
            parts = row.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0]
            # Renames report old and new path; the old path existed at the
            # parent state, so that is the one a tool could have selected.
            if status.startswith(("M", "D", "R")):
                path = parts[1]
            else:
                continue  # A (added) and friends did not exist at parent
            if is_source_path(path):
                ground_truth.add(path)

        if not (min_files <= len(ground_truth) <= max_files):
            continue

        tasks.append(
            Task(
                commit=sha,
                parent=f"{sha}^",
                description=description,
                ground_truth=tuple(sorted(ground_truth)),
            )
        )
        if len(tasks) >= max_tasks:
            break
    return tasks
