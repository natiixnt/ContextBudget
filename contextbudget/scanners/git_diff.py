from __future__ import annotations

"""Git-backed pull-request diff scanning helpers."""

from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import subprocess


@dataclass(slots=True)
class ResolvedGitRefs:
    """Resolved refs and commits used for pull-request analysis."""

    base_ref: str
    head_ref: str
    base_commit: str
    head_commit: str
    merge_base: str


@dataclass(slots=True)
class GitDiffFile:
    """A changed file with before/after content snapshots."""

    path: str
    change_type: str
    previous_path: str = ""
    before_text: str = ""
    after_text: str = ""
    before_binary: bool = False
    after_binary: bool = False
    analyzed: bool = True
    skipped_reason: str = ""


def _run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git_text(repo: Path, args: list[str]) -> str:
    completed = _run_git(repo, args)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="ignore").strip() or "unknown git error"
        raise ValueError(f"git {' '.join(args)} failed: {message}")
    return completed.stdout.decode("utf-8", errors="ignore").strip()


def _git_ref_exists(repo: Path, ref: str) -> bool:
    completed = _run_git(repo, ["rev-parse", "--verify", "--quiet", ref])
    return completed.returncode == 0


def _resolve_event_refs() -> tuple[str, str]:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        return "", ""
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    pull_request = payload.get("pull_request", {})
    if not isinstance(pull_request, dict):
        return "", ""
    base = pull_request.get("base", {})
    head = pull_request.get("head", {})
    if not isinstance(base, dict) or not isinstance(head, dict):
        return "", ""
    return str(base.get("sha", "") or ""), str(head.get("sha", "") or "")


def _resolve_default_base_ref(repo: Path) -> str:
    event_base, _ = _resolve_event_refs()
    if event_base and _git_ref_exists(repo, event_base):
        return event_base

    github_base_ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if github_base_ref:
        for candidate in (f"origin/{github_base_ref}", github_base_ref):
            if _git_ref_exists(repo, candidate):
                return candidate

    for candidate in ("HEAD~1", "HEAD^", "HEAD"):
        if _git_ref_exists(repo, candidate):
            return candidate
    return "HEAD"


def _resolve_default_head_ref(repo: Path) -> str:
    _, event_head = _resolve_event_refs()
    if event_head and _git_ref_exists(repo, event_head):
        return event_head

    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    if github_sha and _git_ref_exists(repo, github_sha):
        return github_sha

    if _git_ref_exists(repo, "HEAD"):
        return "HEAD"
    return ""


def resolve_pull_request_refs(
    repo: Path,
    *,
    base_ref: str | None = None,
    head_ref: str | None = None,
) -> ResolvedGitRefs:
    """Resolve base/head refs and merge-base commit for PR analysis."""

    resolved_base_ref = str(base_ref or _resolve_default_base_ref(repo)).strip()
    resolved_head_ref = str(head_ref or _resolve_default_head_ref(repo)).strip()
    if not resolved_base_ref:
        raise ValueError("Could not resolve a base ref for pull-request analysis.")
    if not resolved_head_ref:
        raise ValueError("Could not resolve a head ref for pull-request analysis.")

    base_commit = _git_text(repo, ["rev-parse", resolved_base_ref])
    head_commit = _git_text(repo, ["rev-parse", resolved_head_ref])
    merge_base = _git_text(repo, ["merge-base", base_commit, head_commit])

    return ResolvedGitRefs(
        base_ref=resolved_base_ref,
        head_ref=resolved_head_ref,
        base_commit=base_commit,
        head_commit=head_commit,
        merge_base=merge_base,
    )


def _matches_glob(path: str, pattern: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.match(pattern) or fnmatch.fnmatch(path, pattern)


def _path_in_scope(
    path: str,
    *,
    include_globs: list[str],
    ignore_globs: list[str],
    ignore_dirs: set[str],
) -> tuple[bool, str]:
    pure = PurePosixPath(path)
    if any(part in ignore_dirs for part in pure.parts):
        return False, "ignored_directory"
    if include_globs and not any(_matches_glob(path, pattern) for pattern in include_globs):
        return False, "include_glob_miss"
    if ignore_globs and any(_matches_glob(path, pattern) for pattern in ignore_globs):
        return False, "ignore_glob_match"
    return True, ""


def _looks_binary(path: str, raw: bytes | None, binary_extensions: set[str]) -> bool:
    if PurePosixPath(path).suffix.lower() in binary_extensions:
        return True
    if raw is None:
        return False
    return b"\0" in raw


def _git_show_bytes(repo: Path, rev: str, path: str) -> bytes | None:
    completed = _run_git(repo, ["show", f"{rev}:{path}"])
    if completed.returncode != 0:
        return None
    return completed.stdout


def _decode_text(raw: bytes | None) -> str:
    if raw is None:
        return ""
    return raw.decode("utf-8", errors="ignore")


def _parse_name_status_line(line: str) -> tuple[str, str, str]:
    parts = line.split("\t")
    if not parts:
        return "", "", ""
    status = parts[0].strip()
    code = status[:1]
    if code in {"R", "C"}:
        if len(parts) < 3:
            return "", "", ""
        return status, parts[1].strip(), parts[2].strip()
    if len(parts) < 2:
        return "", "", ""
    return status, "", parts[1].strip()


def _change_type(status: str) -> str:
    code = status[:1]
    mapping = {
        "A": "added",
        "C": "copied",
        "D": "deleted",
        "M": "modified",
        "R": "renamed",
        "T": "type_changed",
    }
    return mapping.get(code, "modified")


def collect_pull_request_diff(
    repo: Path,
    *,
    base_ref: str | None = None,
    head_ref: str | None = None,
    include_globs: list[str] | None = None,
    ignore_globs: list[str] | None = None,
    ignore_dirs: set[str] | None = None,
    binary_extensions: set[str] | None = None,
) -> tuple[ResolvedGitRefs, list[GitDiffFile]]:
    """Collect changed files plus before/after file content from git refs."""

    resolved = resolve_pull_request_refs(repo, base_ref=base_ref, head_ref=head_ref)
    include_patterns = list(include_globs or ["*"])
    ignore_patterns = list(ignore_globs or [])
    ignored_dirs = set(ignore_dirs or set())
    binaries = set(binary_extensions or set())

    raw_diff = _git_text(
        repo,
        ["diff", "--name-status", "--find-renames", resolved.merge_base, resolved.head_commit],
    )
    if not raw_diff:
        return resolved, []

    files: list[GitDiffFile] = []
    for line in raw_diff.splitlines():
        status, previous_path, path = _parse_name_status_line(line)
        if not status or not path:
            continue

        scope_path = path or previous_path
        in_scope, skip_reason = _path_in_scope(
            scope_path,
            include_globs=include_patterns,
            ignore_globs=ignore_patterns,
            ignore_dirs=ignored_dirs,
        )
        if not in_scope:
            files.append(
                GitDiffFile(
                    path=path,
                    previous_path=previous_path,
                    change_type=_change_type(status),
                    analyzed=False,
                    skipped_reason=skip_reason,
                )
            )
            continue

        before_raw = None
        after_raw = None
        change_type = _change_type(status)
        if change_type not in {"added"}:
            before_target = previous_path or path
            before_raw = _git_show_bytes(repo, resolved.merge_base, before_target)
        if change_type not in {"deleted"}:
            after_raw = _git_show_bytes(repo, resolved.head_commit, path)

        before_binary = _looks_binary(previous_path or path, before_raw, binaries)
        after_binary = _looks_binary(path, after_raw, binaries)
        is_binary = before_binary or after_binary

        files.append(
            GitDiffFile(
                path=path,
                previous_path=previous_path,
                change_type=change_type,
                before_text="" if before_binary else _decode_text(before_raw),
                after_text="" if after_binary else _decode_text(after_raw),
                before_binary=before_binary,
                after_binary=after_binary,
                analyzed=not is_binary,
                skipped_reason="binary_file" if is_binary else "",
            )
        )

    return resolved, files
