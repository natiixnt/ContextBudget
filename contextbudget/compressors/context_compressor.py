from __future__ import annotations

"""Pack/compression stage for budgeted context generation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from contextbudget.cache.summary_cache import SummaryCacheBackend
from contextbudget.config import CompressionSettings, SummarizationSettings
from contextbudget.compressors.language_chunks import select_language_aware_chunks
from contextbudget.compressors.summarizers import SummaryRequest, SummarizationService
from contextbudget.core.text import task_keywords
from contextbudget.core.tokens import estimate_tokens
from contextbudget.schemas.models import CacheReport, CompressedFile, RankedFile, SummarizerReport


@dataclass(slots=True)
class CompressionResult:
    """Result bundle produced by the compression stage."""

    compressed_files: list[CompressedFile]
    files_included: list[str]
    files_skipped: list[str]
    estimated_input_tokens: int
    estimated_saved_tokens: int
    duplicate_reads_prevented: int
    cache: CacheReport
    cache_hits: int
    quality_risk_estimate: str
    summarizer: SummarizerReport


def _snippet_from_text(path: str, text: str, keywords: list[str], settings: CompressionSettings) -> str:
    raw_lines = text.splitlines()
    hit_indexes: list[int] = []
    for idx, line in enumerate(raw_lines):
        lower = line.lower()
        if any(keyword in lower for keyword in keywords):
            hit_indexes.append(idx)
    if not hit_indexes:
        snippet = "\n".join(raw_lines[: settings.snippet_fallback_lines])
        return f"# Snippet: {path}\n{snippet}"

    selected: list[str] = []
    seen: set[int] = set()
    for idx in hit_indexes[: settings.snippet_hit_limit]:
        start = max(0, idx - settings.snippet_context_lines)
        end = min(len(raw_lines), idx + settings.snippet_context_lines + 1)
        for line_no in range(start, end):
            if line_no in seen:
                continue
            seen.add(line_no)
            selected.append(raw_lines[line_no])
    return f"# Snippet: {path}\n" + "\n".join(selected[: settings.snippet_total_line_limit])


def compress_ranked_files(
    task: str,
    repo: Path,
    ranked_files: list[RankedFile],
    max_tokens: int,
    cache: SummaryCacheBackend,
    settings: CompressionSettings | None = None,
    summarization_settings: SummarizationSettings | None = None,
    duplicate_hash_cache_enabled: bool = True,
    token_estimator: Callable[[str], int] = estimate_tokens,
) -> CompressionResult:
    """Compress ranked files under a token budget."""

    cfg = settings if settings is not None else CompressionSettings()
    keywords = task_keywords(task)
    compressed_files: list[CompressedFile] = []
    files_included: list[str] = []
    files_skipped: list[str] = []

    total_raw = 0
    total_compressed = 0
    duplicate_reads_prevented = 0
    seen_hashes: set[str] = set()
    summarizer = SummarizationService(
        backend=(summarization_settings.backend if summarization_settings is not None else "deterministic"),
        adapter_name=(summarization_settings.adapter if summarization_settings is not None else ""),
    )

    for ranked in ranked_files:
        file_record = ranked.file
        if duplicate_hash_cache_enabled and file_record.content_hash in seen_hashes:
            duplicate_reads_prevented += 1
            files_skipped.append(file_record.path)
            continue
        if duplicate_hash_cache_enabled:
            seen_hashes.add(file_record.content_hash)

        try:
            full_text = Path(file_record.absolute_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            files_skipped.append(file_record.path)
            continue

        raw_tokens = token_estimator(full_text)
        total_raw += raw_tokens
        cache_key = f"{file_record.path}:{file_record.size_bytes}:{file_record.content_hash}"

        if raw_tokens <= cfg.full_file_threshold_tokens:
            strategy = "full"
            compressed = f"# Full: {file_record.path}\n{full_text}"
            chunk_strategy = "full-file"
            chunk_reason = "file fits within full-file threshold"
            line_count = len(full_text.splitlines())
            selected_ranges = (
                [
                    {
                        "start_line": 1,
                        "end_line": line_count,
                        "kind": "full",
                    }
                ]
                if line_count > 0
                else []
            )
        elif ranked.score >= cfg.snippet_score_threshold:
            strategy = "snippet"
            chunk = select_language_aware_chunks(
                file_path=file_record.path,
                text=full_text,
                keywords=keywords,
                line_budget=cfg.snippet_total_line_limit,
            )
            if chunk is not None:
                compressed = f"# Snippet: {file_record.path}\n{chunk.text}"
                chunk_strategy = chunk.chunk_strategy
                chunk_reason = chunk.chunk_reason
                selected_ranges = chunk.selected_ranges
            else:
                compressed = _snippet_from_text(file_record.path, full_text, keywords, cfg)
                chunk_strategy = "keyword-window"
                chunk_reason = "fallback keyword-window snippet selection"
                selected_ranges = []
        else:
            strategy = "summary"
            summary = summarizer.summarize(
                cache=cache,
                cache_key_prefix=cache_key,
                request=SummaryRequest(
                    task=task,
                    path=file_record.path,
                    text=full_text,
                    line_limit=cfg.summary_preview_lines,
                    score=ranked.score,
                    keywords=keywords,
                ),
            )
            compressed = summary.text
            chunk_reason = summary.chunk_reason
            chunk_strategy = summary.chunk_strategy
            line_count = len(full_text.splitlines())
            if chunk_strategy == "summary-external":
                selected_ranges = (
                    [
                        {
                            "start_line": 1,
                            "end_line": line_count,
                            "kind": "summary",
                        }
                    ]
                    if line_count > 0
                    else []
                )
            else:
                preview_end = min(cfg.summary_preview_lines, line_count)
                selected_ranges = (
                    [
                        {
                            "start_line": 1,
                            "end_line": preview_end,
                            "kind": "summary",
                        }
                    ]
                    if preview_end > 0
                    else []
                )

        compressed_tokens = token_estimator(compressed)
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
                chunk_strategy=chunk_strategy,
                chunk_reason=chunk_reason,
                selected_ranges=selected_ranges,
                relative_path=file_record.relative_path,
                repo_label=file_record.repo_label,
            )
        )
        files_included.append(file_record.path)

    saved = max(0, total_raw - total_compressed)
    skipped_ratio = len(files_skipped) / max(1, len(ranked_files))
    compression_ratio = total_compressed / max(1, total_raw)
    bounded_ratio = min(1.0, max(0.0, compression_ratio))
    total_weight = cfg.risk_skip_weight + cfg.risk_compression_weight
    if total_weight <= 0:
        total_weight = 1.0
    skip_weight = cfg.risk_skip_weight / total_weight
    compression_weight = cfg.risk_compression_weight / total_weight
    risk_score = skip_weight * skipped_ratio + compression_weight * bounded_ratio
    if risk_score < 0.25:
        risk = "low"
    elif risk_score < 0.5:
        risk = "medium"
    else:
        risk = "high"

    cache_snapshot = cache.snapshot()
    return CompressionResult(
        compressed_files=compressed_files,
        files_included=files_included,
        files_skipped=files_skipped,
        estimated_input_tokens=total_compressed,
        estimated_saved_tokens=saved,
        duplicate_reads_prevented=duplicate_reads_prevented,
        cache=cache_snapshot,
        cache_hits=cache_snapshot.hits,
        quality_risk_estimate=risk,
        summarizer=summarizer.snapshot(),
    )
