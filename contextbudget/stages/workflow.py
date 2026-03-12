from __future__ import annotations

"""Explicit pipeline stages for scan, score, pack, cache, and render boundaries."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from contextbudget.cache.summary_cache import SummaryCache
from contextbudget.compressors.context_compressor import CompressionResult, compress_ranked_files
from contextbudget.config import ContextBudgetConfig
from contextbudget.scanners.repository import scan_repository
from contextbudget.schemas.models import DEFAULT_TOP_FILES, FileRecord, RankedFile, RunReport
from contextbudget.scorers.relevance import score_files


@dataclass(slots=True)
class PlanStageResult:
    """Stage output for a plan command."""

    task: str
    repo: str
    scanned_files: int
    ranked_files: list[dict]


@dataclass(slots=True)
class PackStageResult:
    """Stage output for a pack command."""

    report: RunReport


def run_scan_stage(repo: Path, config: ContextBudgetConfig) -> list[FileRecord]:
    """Scan repository files according to scan settings."""

    return scan_repository(
        repo,
        max_file_size_bytes=config.scan.max_file_size_bytes,
        preview_chars=config.scan.preview_chars,
        include_globs=config.scan.include_globs,
        ignore_globs=config.scan.ignore_globs,
        ignore_dirs=config.scan.ignore_dirs,
        binary_extensions=config.scan.binary_extensions,
    )


def run_score_stage(task: str, files: list[FileRecord], config: ContextBudgetConfig) -> list[RankedFile]:
    """Rank scanned files by deterministic relevance score."""

    return score_files(task, files, settings=config.score)


def run_cache_stage(repo: Path, config: ContextBudgetConfig) -> SummaryCache:
    """Create cache adapter configured for the repository."""

    return SummaryCache(
        repo_path=repo,
        cache_file=config.cache.cache_file,
        enabled=config.cache.summary_cache_enabled,
    )


def run_pack_stage(
    task: str,
    repo: Path,
    ranked: list[RankedFile],
    max_tokens: int,
    cache: SummaryCache,
    config: ContextBudgetConfig,
) -> CompressionResult:
    """Compress ranked files under token budget."""

    return compress_ranked_files(
        task=task,
        repo=repo,
        ranked_files=ranked,
        max_tokens=max_tokens,
        cache=cache,
        settings=config.compression,
        duplicate_hash_cache_enabled=config.cache.duplicate_hash_cache_enabled,
    )


def run_render_stage(
    task: str,
    repo: Path,
    ranked: list[RankedFile],
    compressed: CompressionResult,
    max_tokens: int,
    config: ContextBudgetConfig,
    top_files: int | None = None,
) -> RunReport:
    """Render pipeline stage data into stable run report schema."""

    effective_top_files = top_files if top_files is not None else config.budget.top_files
    if effective_top_files is None:
        effective_top_files = DEFAULT_TOP_FILES
    return RunReport(
        command="pack",
        task=task,
        repo=str(repo),
        max_tokens=max_tokens,
        ranked_files=[
            {
                "path": item.file.path,
                "score": item.score,
                "reasons": item.reasons,
                "line_count": item.file.line_count,
            }
            for item in ranked[:effective_top_files]
        ],
        compressed_context=[
            {
                "path": item.path,
                "strategy": item.strategy,
                "original_tokens": item.original_tokens,
                "compressed_tokens": item.compressed_tokens,
                "text": item.text,
                "chunk_strategy": item.chunk_strategy,
                "chunk_reason": item.chunk_reason,
                "selected_ranges": item.selected_ranges,
            }
            for item in compressed.compressed_files
        ],
        files_included=compressed.files_included,
        files_skipped=compressed.files_skipped,
        budget={
            "estimated_input_tokens": compressed.estimated_input_tokens,
            "estimated_saved_tokens": compressed.estimated_saved_tokens,
            "duplicate_reads_prevented": compressed.duplicate_reads_prevented,
            "quality_risk_estimate": compressed.quality_risk_estimate,
        },
        cache_hits=compressed.cache_hits,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def build_plan_result(
    task: str,
    repo: Path,
    scanned_files: int,
    ranked: list[RankedFile],
    top_n: int,
) -> PlanStageResult:
    """Build serialized plan-stage payload."""

    return PlanStageResult(
        task=task,
        repo=str(repo),
        scanned_files=scanned_files,
        ranked_files=[
            {
                "path": item.file.path,
                "score": item.score,
                "reasons": item.reasons,
                "line_count": item.file.line_count,
            }
            for item in ranked[:top_n]
        ],
    )


def as_json_dict(report: RunReport) -> dict:
    """Convert typed run report into JSON-serializable dictionary."""

    return asdict(report)
