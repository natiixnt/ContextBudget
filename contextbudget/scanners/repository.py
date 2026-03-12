from __future__ import annotations

"""Repository scan stage primitives."""

import fnmatch
import hashlib
from pathlib import Path, PurePosixPath

from contextbudget.schemas.models import BINARY_EXTENSIONS, CACHE_FILE, DEFAULT_IGNORE_DIRS, FileRecord


def _is_text_file(path: Path, binary_extensions: set[str]) -> bool:
    if path.suffix.lower() in binary_extensions:
        return False
    try:
        with path.open("rb") as handle:
            chunk = handle.read(2048)
        if b"\0" in chunk:
            return False
        return True
    except OSError:
        return False


def _count_lines(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _matches_glob(path: str, pattern: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.match(pattern) or fnmatch.fnmatch(path, pattern)


def scan_repository(
    repo_path: Path,
    max_file_size_bytes: int = 2_000_000,
    preview_chars: int = 2_000,
    include_globs: list[str] | None = None,
    ignore_globs: list[str] | None = None,
    ignore_dirs: set[str] | None = None,
    binary_extensions: set[str] | None = None,
) -> list[FileRecord]:
    """Scan text files in a repository and return file metadata records."""

    include_patterns = include_globs if include_globs is not None else ["*"]
    ignore_patterns = ignore_globs if ignore_globs is not None else []
    ignore = ignore_dirs if ignore_dirs is not None else set(DEFAULT_IGNORE_DIRS)
    binaries = binary_extensions if binary_extensions is not None else set(BINARY_EXTENSIONS)
    results: list[FileRecord] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if path.name == CACHE_FILE:
            continue
        if any(part in ignore for part in path.parts):
            continue
        rel = path.relative_to(repo_path).as_posix()
        if include_patterns and not any(_matches_glob(rel, pattern) for pattern in include_patterns):
            continue
        if ignore_patterns and any(_matches_glob(rel, pattern) for pattern in ignore_patterns):
            continue
        file_size = path.stat().st_size
        if file_size > max_file_size_bytes:
            continue
        if not _is_text_file(path, binaries):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
        results.append(
            FileRecord(
                path=rel,
                absolute_path=str(path),
                extension=path.suffix.lower(),
                size_bytes=file_size,
                line_count=_count_lines(text),
                content_hash=digest,
                content_preview=text[:preview_chars],
            )
        )
    return sorted(results, key=lambda record: record.path)
