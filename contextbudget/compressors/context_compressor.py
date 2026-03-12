from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contextbudget.cache.summary_cache import SummaryCache
from contextbudget.core.text import task_keywords
from contextbudget.core.tokens import estimate_tokens
from contextbudget.schemas.models import CompressedFile, FileRecord, RankedFile


@dataclass(slots=True)
class CompressionResult:
    compressed_files: list[CompressedFile]
    files_included: list[str]
    files_skipped: list[str]
    estimated_input_tokens: int
    estimated_saved_tokens: int
    duplicate_reads_prevented: int
    cache_hits: int
    quality_risk_estimate: str


def _summary_from_text(path: str, text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_lines = lines[:8]
    summary = "\n".join(first_lines)
    if len(lines) > 8:
        summary += "\n..."
    return f"# Summary: {path}\n{summary}" if summary else f"# Summary: {path}\n<empty file>"


def _snippet_from_text(path: str, text: str, keywords: list[str]) -> str:
    raw_lines = text.splitlines()
    hit_indexes: list[int] = []
    for idx, line in enumerate(raw_lines):
        lower = line.lower()
        if any(keyword in lower for keyword in keywords):
            hit_indexes.append(idx)
    if not hit_indexes:
        snippet = "\n".join(raw_lines[:60])
        return f"# Snippet: {path}\n{snippet}"

    selected: list[str] = []
    seen: set[int] = set()
    for idx in hit_indexes[:8]:
        start = max(0, idx - 2)
        end = min(len(raw_lines), idx + 3)
        for line_no in range(start, end):
            if line_no in seen:
                continue
            seen.add(line_no)
            selected.append(raw_lines[line_no])
    return f"# Snippet: {path}\n" + "\n".join(selected[:120])


def compress_ranked_files(
    task: str,
    repo: Path,
    ranked_files: list[RankedFile],
    max_tokens: int,
    cache: SummaryCache,
) -> CompressionResult:
    keywords = task_keywords(task)
    compressed_files: list[CompressedFile] = []
    files_included: list[str] = []
    files_skipped: list[str] = []

    total_raw = 0
    total_compressed = 0
    duplicate_reads_prevented = 0
    seen_hashes: set[str] = set()

    for ranked in ranked_files:
        file_record = ranked.file
        if file_record.content_hash in seen_hashes:
            duplicate_reads_prevented += 1
            files_skipped.append(file_record.path)
            continue
        seen_hashes.add(file_record.content_hash)

        path = repo / file_record.path
        try:
            full_text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            files_skipped.append(file_record.path)
            continue

        raw_tokens = estimate_tokens(full_text)
        total_raw += raw_tokens
        cache_key = f"{file_record.path}:{file_record.size_bytes}:{file_record.content_hash}"

        if raw_tokens <= 600:
            strategy = "full"
            compressed = f"# Full: {file_record.path}\n{full_text}"
        elif ranked.score >= 2.5:
            strategy = "snippet"
            compressed = _snippet_from_text(file_record.path, full_text, keywords)
        else:
            strategy = "summary"
            cached = cache.get_summary(cache_key)
            if cached is not None:
                compressed = cached
            else:
                compressed = _summary_from_text(file_record.path, full_text)
                cache.put_summary(cache_key, compressed)

        compressed_tokens = estimate_tokens(compressed)
        if total_compressed + compressed_tokens > max_tokens:
            files_skipped.append(file_record.path)
            continue

        total_compressed += compressed_tokens
        compressed_files.append(
            CompressedFile(
                path=file_record.path,
                strategy=strategy,
                original_tokens=raw_tokens,
                compressed_tokens=compressed_tokens,
                text=compressed,
            )
        )
        files_included.append(file_record.path)

    saved = max(0, total_raw - total_compressed)
    skipped_ratio = len(files_skipped) / max(1, len(ranked_files))
    compression_ratio = total_compressed / max(1, total_raw)
    bounded_ratio = min(1.0, max(0.0, compression_ratio))
    risk_score = 0.55 * skipped_ratio + 0.45 * bounded_ratio
    if risk_score < 0.25:
        risk = "low"
    elif risk_score < 0.5:
        risk = "medium"
    else:
        risk = "high"

    return CompressionResult(
        compressed_files=compressed_files,
        files_included=files_included,
        files_skipped=files_skipped,
        estimated_input_tokens=total_compressed,
        estimated_saved_tokens=saved,
        duplicate_reads_prevented=duplicate_reads_prevented,
        cache_hits=cache.stats.hits,
        quality_risk_estimate=risk,
    )
