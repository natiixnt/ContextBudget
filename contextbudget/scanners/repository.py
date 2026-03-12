from __future__ import annotations

import hashlib
from pathlib import Path

from contextbudget.schemas.models import BINARY_EXTENSIONS, DEFAULT_IGNORE_DIRS, FileRecord


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
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


def scan_repository(repo_path: Path, max_file_size_bytes: int = 2_000_000) -> list[FileRecord]:
    results: list[FileRecord] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in DEFAULT_IGNORE_DIRS for part in path.parts):
            continue
        if path.stat().st_size > max_file_size_bytes:
            continue
        if not _is_text_file(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(repo_path).as_posix()
        digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
        results.append(
            FileRecord(
                path=rel,
                absolute_path=str(path),
                extension=path.suffix.lower(),
                size_bytes=path.stat().st_size,
                line_count=_count_lines(text),
                content_hash=digest,
                content_preview=text[:2000],
            )
        )
    return sorted(results, key=lambda record: record.path)
