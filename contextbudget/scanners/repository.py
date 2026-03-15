from __future__ import annotations

"""Repository scan stage primitives."""

from pathlib import Path

from contextbudget.scanners.incremental import refresh_scan_index
from contextbudget.schemas.models import BINARY_EXTENSIONS, DEFAULT_IGNORE_DIRS, FileRecord, SCAN_INDEX_FILE


def scan_repository(
    repo_path: Path,
    max_file_size_bytes: int = 2_000_000,
    preview_chars: int = 2_000,
    include_globs: list[str] | None = None,
    ignore_globs: list[str] | None = None,
    ignore_dirs: set[str] | None = None,
    binary_extensions: set[str] | None = None,
    scan_index_file: str = SCAN_INDEX_FILE,
    internal_paths: set[str] | None = None,
    repo_label: str | None = None,
) -> list[FileRecord]:
    """Scan text files in a repository and return file metadata records."""

    result = refresh_scan_index(
        repo_path,
        max_file_size_bytes=max_file_size_bytes,
        preview_chars=preview_chars,
        include_globs=include_globs,
        ignore_globs=ignore_globs,
        ignore_dirs=ignore_dirs if ignore_dirs is not None else set(DEFAULT_IGNORE_DIRS),
        binary_extensions=binary_extensions if binary_extensions is not None else set(BINARY_EXTENSIONS),
        scan_index_file=scan_index_file,
        internal_paths=internal_paths,
        repo_label=repo_label,
    )
    return result.records
