from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from contextbudget.cache.summary_cache import SummaryCache
from contextbudget.compressors.context_compressor import compress_ranked_files
from contextbudget.scanners.repository import scan_repository
from contextbudget.scorers.relevance import score_files
from contextbudget.schemas.models import DEFAULT_MAX_TOKENS, DEFAULT_TOP_FILES, RunReport


def run_plan(task: str, repo: Path, top_n: int = DEFAULT_TOP_FILES) -> dict:
    files = scan_repository(repo)
    ranked = score_files(task, files)
    top = ranked[:top_n]
    return {
        "task": task,
        "repo": str(repo),
        "scanned_files": len(files),
        "ranked_files": [
            {
                "path": item.file.path,
                "score": item.score,
                "reasons": item.reasons,
                "line_count": item.file.line_count,
            }
            for item in top
        ],
    }


def run_pack(task: str, repo: Path, max_tokens: int = DEFAULT_MAX_TOKENS) -> RunReport:
    files = scan_repository(repo)
    ranked = score_files(task, files)
    cache = SummaryCache(repo)
    compressed = compress_ranked_files(task, repo, ranked, max_tokens, cache)
    cache.save()

    report = RunReport(
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
            for item in ranked[:DEFAULT_TOP_FILES]
        ],
        compressed_context=[
            {
                "path": item.path,
                "strategy": item.strategy,
                "original_tokens": item.original_tokens,
                "compressed_tokens": item.compressed_tokens,
                "text": item.text,
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
    return report


def run_report_from_json(data: dict) -> dict:
    budget = data.get("budget", {})
    return {
        "task": data.get("task", ""),
        "repo": data.get("repo", ""),
        "generated_at": data.get("generated_at", ""),
        "estimated_input_tokens": budget.get("estimated_input_tokens", 0),
        "estimated_saved_tokens": budget.get("estimated_saved_tokens", 0),
        "files_included": data.get("files_included", []),
        "files_skipped": data.get("files_skipped", []),
        "duplicate_reads_prevented": budget.get("duplicate_reads_prevented", 0),
        "quality_risk_estimate": budget.get("quality_risk_estimate", "unknown"),
    }


def as_json_dict(report: RunReport) -> dict:
    return asdict(report)
