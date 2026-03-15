from __future__ import annotations

"""Workspace scan helpers for multi-repo local analysis."""

from dataclasses import dataclass

from redcon.config import RedconConfig, WorkspaceDefinition
from redcon.scanners.incremental import refresh_scan_index
from redcon.schemas.models import FileRecord


@dataclass(slots=True)
class ScannedWorkspaceRepo:
    """Summary of a workspace repository scan."""

    label: str
    path: str
    scanned_files: int


def scan_workspace(
    workspace: WorkspaceDefinition,
    config: RedconConfig,
    internal_paths: set[str] | None = None,
) -> tuple[list[FileRecord], list[ScannedWorkspaceRepo]]:
    """Scan all repositories defined in a workspace using shared scan settings."""

    files: list[FileRecord] = []
    scanned_repos: list[ScannedWorkspaceRepo] = []

    for repo in workspace.repos:
        include_globs = repo.include_globs or config.scan.include_globs
        ignore_globs = list(config.scan.ignore_globs)
        ignore_globs.extend(repo.ignore_globs)
        refresh = refresh_scan_index(
            repo.path,
            max_file_size_bytes=config.scan.max_file_size_bytes,
            preview_chars=config.scan.preview_chars,
            include_globs=include_globs,
            ignore_globs=ignore_globs,
            ignore_dirs=config.scan.ignore_dirs,
            binary_extensions=config.scan.binary_extensions,
            internal_paths=internal_paths,
            repo_label=repo.label,
        )
        repo_files = refresh.records
        files.extend(repo_files)
        scanned_repos.append(
            ScannedWorkspaceRepo(
                label=repo.label,
                path=str(repo.path),
                scanned_files=len(repo_files),
            )
        )

    files.sort(key=lambda record: record.path)
    scanned_repos.sort(key=lambda repo: repo.label)
    return files, scanned_repos
