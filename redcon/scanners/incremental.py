from __future__ import annotations

"""Incremental repository scan index for reusing unchanged file metadata."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any
import hashlib

from redcon.schemas.models import (
    BINARY_EXTENSIONS,
    CACHE_FILE,
    DEFAULT_IGNORE_DIRS,
    FileRecord,
    RUN_HISTORY_FILE,
    SCAN_INDEX_FILE,
)

INDEX_FORMAT_VERSION = 1

_VENV_PREFIXES = (".venv", "venv-")


def _is_venv_dir(name: str) -> bool:
    """Return True for venv-style directory names not covered by exact matches."""
    return any(name.startswith(prefix) for prefix in _VENV_PREFIXES)


@dataclass(slots=True)
class FileClassification:
    """Stored classification metadata for a scanned file."""

    kind: str
    reason: str
    extension: str
    is_text: bool


@dataclass(slots=True)
class ScanIndexEntry:
    """Persisted scan metadata for a repository file."""

    path: str
    size_bytes: int
    mtime_ns: int
    content_hash: str
    classification: FileClassification
    record: FileRecord | None = None


@dataclass(slots=True)
class ScanIndexState:
    """On-disk index state for incremental scans."""

    settings_fingerprint: str
    entries: dict[str, ScanIndexEntry] = field(default_factory=dict)
    version: int = INDEX_FORMAT_VERSION


@dataclass(slots=True)
class ScanRefreshSummary:
    """Summary of a scan-index refresh operation."""

    tracked_files: int
    included_files: int
    skipped_files: int
    added_count: int
    updated_count: int
    removed_count: int
    reused_count: int
    added_paths: list[str] = field(default_factory=list)
    updated_paths: list[str] = field(default_factory=list)
    removed_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScanRefreshResult:
    """Incremental scan output and refresh summary."""

    records: list[FileRecord]
    summary: ScanRefreshSummary
    index_path: str


def _is_text_file(path: Path, binary_extensions: set[str]) -> bool:
    if path.suffix.lower() in binary_extensions:
        return False
    try:
        with path.open("rb") as handle:
            chunk = handle.read(2048)
        return b"\0" not in chunk
    except OSError:
        return False


def _count_lines(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _matches_glob(path: str, pattern: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.match(pattern) or fnmatch.fnmatch(path, pattern)


def _scoped_path(relative_path: str, repo_label: str | None = None) -> str:
    if repo_label:
        return f"{repo_label}:{relative_path}"
    return relative_path


def _default_internal_paths() -> set[str]:
    return {CACHE_FILE, RUN_HISTORY_FILE, SCAN_INDEX_FILE}


def _normalize_relative_path(path: Path, repo_path: Path) -> str | None:
    try:
        return path.relative_to(repo_path).as_posix()
    except ValueError:
        return None


def _resolve_index_path(repo_path: Path, scan_index_file: str) -> Path:
    candidate = Path(scan_index_file)
    if candidate.is_absolute():
        return candidate
    return repo_path / candidate


def _normalize_internal_paths(
    repo_path: Path,
    *,
    scan_index_file: str,
    internal_paths: set[str] | None,
) -> set[str]:
    normalized: set[str] = set()
    for raw in _default_internal_paths().union(internal_paths or set()).union({scan_index_file}):
        candidate = Path(raw)
        if candidate.is_absolute():
            rel = _normalize_relative_path(candidate.resolve(), repo_path)
            if rel is not None:
                normalized.add(rel)
            continue
        normalized.add(candidate.as_posix())
    index_path = _resolve_index_path(repo_path, scan_index_file)
    rel_index = _normalize_relative_path(index_path.resolve(), repo_path)
    if rel_index is not None:
        normalized.add(rel_index)
    return normalized


def _fingerprint_settings(
    *,
    include_globs: list[str],
    ignore_globs: list[str],
    max_file_size_bytes: int,
    preview_chars: int,
    ignore_dirs: set[str],
    binary_extensions: set[str],
    internal_paths: set[str],
) -> tuple[str, dict[str, Any]]:
    payload = {
        "include_globs": list(include_globs),
        "ignore_globs": list(ignore_globs),
        "max_file_size_bytes": int(max_file_size_bytes),
        "preview_chars": int(preview_chars),
        "ignore_dirs": sorted(ignore_dirs),
        "binary_extensions": sorted(binary_extensions),
        "internal_paths": sorted(internal_paths),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest(), payload


def _load_scan_index(path: Path, *, settings_fingerprint: str) -> ScanIndexState:
    if not path.exists():
        return ScanIndexState(settings_fingerprint=settings_fingerprint)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ScanIndexState(settings_fingerprint=settings_fingerprint)
    if not isinstance(raw, dict):
        return ScanIndexState(settings_fingerprint=settings_fingerprint)
    if int(raw.get("version", 0) or 0) != INDEX_FORMAT_VERSION:
        return ScanIndexState(settings_fingerprint=settings_fingerprint)
    if str(raw.get("settings_fingerprint", "")) != settings_fingerprint:
        return ScanIndexState(settings_fingerprint=settings_fingerprint)

    entries: dict[str, ScanIndexEntry] = {}
    for item in raw.get("entries", []):
        if not isinstance(item, dict):
            continue
        classification_raw = item.get("classification", {})
        if not isinstance(classification_raw, dict):
            classification_raw = {}
        record_raw = item.get("record")
        if isinstance(record_raw, dict):
            try:
                record = FileRecord(
                    path=str(record_raw.get("path", "")),
                    absolute_path=str(record_raw.get("absolute_path", "")),
                    extension=str(record_raw.get("extension", "")),
                    size_bytes=int(record_raw.get("size_bytes", 0) or 0),
                    line_count=int(record_raw.get("line_count", 0) or 0),
                    content_hash=str(record_raw.get("content_hash", "")),
                    content_preview=str(record_raw.get("content_preview", "")),
                    relative_path=str(record_raw.get("relative_path", "")),
                    repo_label=str(record_raw.get("repo_label", "")),
                    repo_root=str(record_raw.get("repo_root", "")),
                )
            except (TypeError, ValueError):
                record = None
        else:
            record = None
        try:
            entry = ScanIndexEntry(
                path=str(item.get("path", "")),
                size_bytes=int(item.get("size_bytes", 0) or 0),
                mtime_ns=int(item.get("mtime_ns", 0) or 0),
                content_hash=str(item.get("content_hash", "")),
                classification=FileClassification(
                    kind=str(classification_raw.get("kind", "unknown")),
                    reason=str(classification_raw.get("reason", "")),
                    extension=str(classification_raw.get("extension", "")),
                    is_text=bool(classification_raw.get("is_text", False)),
                ),
                record=record,
            )
        except (TypeError, ValueError):
            continue
        if entry.path:
            entries[entry.path] = entry
    return ScanIndexState(settings_fingerprint=settings_fingerprint, entries=entries)


def load_scan_index(path: Path) -> dict[str, Any]:
    """Load the raw on-disk scan index for inspection or tests."""

    return json.loads(path.read_text(encoding="utf-8"))


def _save_scan_index(path: Path, state: ScanIndexState, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": state.version,
        "settings_fingerprint": state.settings_fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "entries": [asdict(state.entries[key]) for key in sorted(state.entries)],
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _build_file_record(
    path: Path,
    rel: str,
    *,
    file_size: int,
    preview_chars: int,
    repo_path: Path,
    repo_label: str | None = None,
) -> FileRecord | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
    return FileRecord(
        path=_scoped_path(rel, repo_label),
        absolute_path=str(path),
        extension=path.suffix.lower(),
        size_bytes=file_size,
        line_count=_count_lines(text),
        content_hash=digest,
        content_preview=text[:preview_chars],
        relative_path=rel,
        repo_label=repo_label or "",
        repo_root=str(repo_path),
    )


def _classify_file(
    path: Path,
    rel: str,
    *,
    file_size: int,
    mtime_ns: int,
    preview_chars: int,
    include_globs: list[str],
    ignore_globs: list[str],
    binary_extensions: set[str],
    max_file_size_bytes: int,
    repo_path: Path,
    repo_label: str | None = None,
) -> ScanIndexEntry:
    extension = path.suffix.lower()
    if include_globs and not any(_matches_glob(rel, pattern) for pattern in include_globs):
        return ScanIndexEntry(
            path=rel,
            size_bytes=file_size,
            mtime_ns=mtime_ns,
            content_hash="",
            classification=FileClassification(
                kind="excluded",
                reason="include_glob_miss",
                extension=extension,
                is_text=False,
            ),
        )
    if ignore_globs and any(_matches_glob(rel, pattern) for pattern in ignore_globs):
        return ScanIndexEntry(
            path=rel,
            size_bytes=file_size,
            mtime_ns=mtime_ns,
            content_hash="",
            classification=FileClassification(
                kind="ignored",
                reason="ignore_glob_match",
                extension=extension,
                is_text=False,
            ),
        )
    if file_size > max_file_size_bytes:
        return ScanIndexEntry(
            path=rel,
            size_bytes=file_size,
            mtime_ns=mtime_ns,
            content_hash="",
            classification=FileClassification(
                kind="too_large",
                reason="max_file_size_bytes",
                extension=extension,
                is_text=False,
            ),
        )
    if not _is_text_file(path, binary_extensions):
        return ScanIndexEntry(
            path=rel,
            size_bytes=file_size,
            mtime_ns=mtime_ns,
            content_hash="",
            classification=FileClassification(
                kind="binary",
                reason="binary_or_null_bytes",
                extension=extension,
                is_text=False,
            ),
        )
    record = _build_file_record(
        path,
        rel,
        file_size=file_size,
        preview_chars=preview_chars,
        repo_path=repo_path,
        repo_label=repo_label,
    )
    if record is None:
        return ScanIndexEntry(
            path=rel,
            size_bytes=file_size,
            mtime_ns=mtime_ns,
            content_hash="",
            classification=FileClassification(
                kind="unreadable",
                reason="read_error",
                extension=extension,
                is_text=False,
            ),
        )
    return ScanIndexEntry(
        path=rel,
        size_bytes=file_size,
        mtime_ns=mtime_ns,
        content_hash=record.content_hash,
        classification=FileClassification(
            kind="included",
            reason="matched_scan_rules",
            extension=extension,
            is_text=True,
        ),
        record=record,
    )


def refresh_scan_index(
    repo_path: Path,
    *,
    max_file_size_bytes: int = 2_000_000,
    preview_chars: int = 2_000,
    include_globs: list[str] | None = None,
    ignore_globs: list[str] | None = None,
    ignore_dirs: set[str] | None = None,
    binary_extensions: set[str] | None = None,
    scan_index_file: str = SCAN_INDEX_FILE,
    internal_paths: set[str] | None = None,
    repo_label: str | None = None,
) -> ScanRefreshResult:
    """Refresh the on-disk scan index and reuse unchanged file metadata."""

    include_patterns = include_globs if include_globs is not None else ["*"]
    ignore_patterns = ignore_globs if ignore_globs is not None else []
    ignored_directories = ignore_dirs if ignore_dirs is not None else set(DEFAULT_IGNORE_DIRS)
    binaries = binary_extensions if binary_extensions is not None else set(BINARY_EXTENSIONS)
    normalized_internal_paths = _normalize_internal_paths(
        repo_path,
        scan_index_file=scan_index_file,
        internal_paths=internal_paths,
    )
    settings_fingerprint, settings_payload = _fingerprint_settings(
        include_globs=include_patterns,
        ignore_globs=ignore_patterns,
        max_file_size_bytes=max_file_size_bytes,
        preview_chars=preview_chars,
        ignore_dirs=ignored_directories,
        binary_extensions=binaries,
        internal_paths=normalized_internal_paths,
    )
    index_path = _resolve_index_path(repo_path, scan_index_file)
    previous = _load_scan_index(index_path, settings_fingerprint=settings_fingerprint)
    current_entries: dict[str, ScanIndexEntry] = {}
    records: list[FileRecord] = []
    reused_paths: list[str] = []
    added_paths: list[str] = []
    updated_paths: list[str] = []
    seen_paths: set[str] = set()

    for root, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in ignored_directories and not _is_venv_dir(name)
        )
        for name in sorted(filenames):
            path = Path(root) / name
            if not path.is_file():
                continue
            rel = path.relative_to(repo_path).as_posix()
            if rel in normalized_internal_paths:
                continue
            try:
                stat_result = path.stat()
            except OSError:
                continue
            seen_paths.add(rel)
            file_size = int(stat_result.st_size)
            previous_entry = previous.entries.get(rel)
            if (
                previous_entry is not None
                and previous_entry.size_bytes == file_size
                and previous_entry.mtime_ns == int(stat_result.st_mtime_ns)
            ):
                current_entries[rel] = previous_entry
                reused_paths.append(rel)
                if previous_entry.record is not None:
                    records.append(previous_entry.record)
                continue
            entry = _classify_file(
                path,
                rel,
                file_size=file_size,
                mtime_ns=int(stat_result.st_mtime_ns),
                preview_chars=preview_chars,
                include_globs=include_patterns,
                ignore_globs=ignore_patterns,
                binary_extensions=binaries,
                max_file_size_bytes=max_file_size_bytes,
                repo_path=repo_path,
                repo_label=repo_label,
            )
            current_entries[rel] = entry
            if entry.record is not None:
                records.append(entry.record)
            if previous_entry is None:
                added_paths.append(rel)
            else:
                updated_paths.append(rel)

    removed_paths = sorted(set(previous.entries) - seen_paths)
    state = ScanIndexState(settings_fingerprint=settings_fingerprint, entries=current_entries)
    _save_scan_index(index_path, state, settings_payload)

    records.sort(key=lambda record: record.path)
    summary = ScanRefreshSummary(
        tracked_files=len(current_entries),
        included_files=len(records),
        skipped_files=max(0, len(current_entries) - len(records)),
        added_count=len(added_paths),
        updated_count=len(updated_paths),
        removed_count=len(removed_paths),
        reused_count=len(reused_paths),
        added_paths=sorted(added_paths),
        updated_paths=sorted(updated_paths),
        removed_paths=removed_paths,
    )
    return ScanRefreshResult(records=records, summary=summary, index_path=str(index_path))
