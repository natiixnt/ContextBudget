from __future__ import annotations

"""Example plugins for custom scoring and compression strategies."""

from pathlib import Path, PurePosixPath
from typing import Any, Mapping
import fnmatch

from contextbudget.compressors.context_compressor import CompressionResult
from contextbudget.compressors.summarizers import SummarizationService
from contextbudget.plugins.api import CompressorPlugin, ScorerPlugin
from contextbudget.scorers.relevance import score_files
from contextbudget.schemas.models import CompressedFile, RankedFile


def _matches_glob(path: str, pattern: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.match(pattern) or fnmatch.fnmatch(path, pattern)


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _path_glob_bonus_score(
    *,
    task: str,
    files,
    settings,
    options: Mapping[str, Any],
    estimate_tokens,
):
    del estimate_tokens
    patterns_raw = options.get("path_patterns", ["docs/**"])
    if isinstance(patterns_raw, (list, tuple, set)):
        patterns = [str(item) for item in patterns_raw if str(item).strip()]
    else:
        patterns = [str(patterns_raw)] if str(patterns_raw).strip() else ["docs/**"]
    try:
        bonus = float(options.get("bonus", 4.0) or 4.0)
    except (TypeError, ValueError):
        bonus = 4.0

    ranked = score_files(task, files, settings=settings)
    by_path = {item.file.path: item for item in ranked}

    for record in files:
        matched = next((pattern for pattern in patterns if _matches_glob(record.path, pattern)), None)
        if matched is None:
            continue
        reason = f"plugin path match '{matched}'"
        item = by_path.get(record.path)
        if item is None:
            by_path[record.path] = RankedFile(
                file=record,
                score=round(bonus, 3),
                reasons=[reason],
            )
            continue
        item.score = round(item.score + bonus, 3)
        _add_reason(item.reasons, reason)

    output = list(by_path.values())
    output.sort(key=lambda item: (-item.score, item.file.path))
    return output


def _leading_summary_text(path: str, text: str, preview_lines: int) -> str:
    kept_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        kept_lines.append(stripped)
        if len(kept_lines) >= preview_lines:
            break
    summary = "\n".join(kept_lines)
    if not summary:
        summary = "<empty file>"
    return f"# Plugin Summary: {path}\n{summary}"


def _risk_from_inclusion(included: int, total: int) -> str:
    if total <= 0:
        return "low"
    ratio = included / total
    if ratio >= 0.8:
        return "low"
    if ratio >= 0.4:
        return "medium"
    return "high"


def _leading_summary_compress(
    *,
    task: str,
    repo,
    ranked_files,
    max_tokens: int,
    cache,
    settings,
    summarization_settings,
    options: Mapping[str, Any],
    estimate_tokens,
    duplicate_hash_cache_enabled: bool,
):
    del task, repo, duplicate_hash_cache_enabled
    try:
        preview_lines = int(options.get("preview_lines", settings.summary_preview_lines) or settings.summary_preview_lines)
    except (TypeError, ValueError):
        preview_lines = settings.summary_preview_lines
    preview_lines = max(1, preview_lines)

    compressed_files: list[CompressedFile] = []
    files_included: list[str] = []
    files_skipped: list[str] = []
    total_raw = 0
    total_compressed = 0
    summarizer = SummarizationService(
        backend=(summarization_settings.backend if summarization_settings is not None else "deterministic"),
        adapter_name=(summarization_settings.adapter if summarization_settings is not None else ""),
    )

    for ranked in ranked_files:
        file_record = ranked.file
        try:
            file_text = Path(file_record.absolute_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            files_skipped.append(file_record.path)
            continue

        raw_tokens = estimate_tokens(file_text)
        total_raw += raw_tokens
        cache_key = (
            f"example.leading_summary:{file_record.path}:"
            f"{file_record.size_bytes}:{file_record.content_hash}:{preview_lines}"
        )
        cached = cache.get_summary(cache_key)
        if cached is not None:
            compressed = cached
            chunk_reason = "cached example leading-summary preview"
        else:
            compressed = _leading_summary_text(file_record.path, file_text, preview_lines)
            cache.put_summary(cache_key, compressed)
            chunk_reason = f"example plugin leading {preview_lines} non-empty lines"

        compressed_tokens = estimate_tokens(compressed)
        if total_compressed + compressed_tokens > max_tokens:
            files_skipped.append(file_record.path)
            continue

        total_compressed += compressed_tokens
        line_count = len(file_text.splitlines())
        compressed_files.append(
            CompressedFile(
                path=file_record.path,
                strategy="plugin-summary",
                original_tokens=raw_tokens,
                compressed_tokens=compressed_tokens,
                text=compressed,
                chunk_strategy="plugin-leading-summary",
                chunk_reason=chunk_reason,
                selected_ranges=(
                    [{"start_line": 1, "end_line": min(preview_lines, line_count), "kind": "plugin-summary"}]
                    if line_count > 0
                    else []
                ),
                relative_path=file_record.relative_path,
                repo_label=file_record.repo_label,
            )
        )
        files_included.append(file_record.path)

    cache_snapshot = cache.snapshot()
    return CompressionResult(
        compressed_files=compressed_files,
        files_included=files_included,
        files_skipped=files_skipped,
        estimated_input_tokens=total_compressed,
        estimated_saved_tokens=max(0, total_raw - total_compressed),
        duplicate_reads_prevented=0,
        cache=cache_snapshot,
        cache_hits=cache_snapshot.hits,
        quality_risk_estimate=_risk_from_inclusion(len(files_included), len(ranked_files)),
        summarizer=summarizer.snapshot(),
    )


path_glob_bonus_scorer = ScorerPlugin(
    name="example.path_glob_bonus",
    score=_path_glob_bonus_score,
    description="Adds a deterministic score bonus to paths matching configured glob patterns.",
)

leading_summary_compressor = CompressorPlugin(
    name="example.leading_summary",
    compress=_leading_summary_compress,
    description="Replaces built-in snippet/summary selection with a simple leading-lines summary.",
)
