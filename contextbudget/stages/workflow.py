from __future__ import annotations

"""Explicit pipeline stages for scan, score, pack, cache, and render boundaries."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from contextbudget.cache.summary_cache import SummaryCacheBackend, create_summary_cache_backend
from contextbudget.compressors.context_compressor import CompressionResult
from contextbudget.config import ContextBudgetConfig, WorkspaceDefinition
from contextbudget.core.tokens import normalize_token_estimator_report
from contextbudget.plugins import ResolvedPlugins, resolve_plugins
from contextbudget.scanners.incremental import ScanRefreshResult, refresh_scan_index
from contextbudget.scanners.workspace import ScannedWorkspaceRepo, scan_workspace
from contextbudget.schemas.models import (
    CompressedFile,
    DEFAULT_TOP_FILES,
    FileRecord,
    RankedFile,
    RunReport,
    TokenEstimatorReport,
)


@dataclass(slots=True)
class PlanStageResult:
    """Stage output for a plan command."""

    task: str
    repo: str
    scanned_files: int
    ranked_files: list[dict]
    workspace: str = ""
    scanned_repos: list[dict] = field(default_factory=list)
    selected_repos: list[str] = field(default_factory=list)
    implementations: dict[str, str] = field(default_factory=dict)
    token_estimator: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class PackStageResult:
    """Stage output for a pack command."""

    report: RunReport


def _serialize_ranked_file(item: RankedFile) -> dict:
    data = {
        "path": item.file.path,
        "score": item.score,
        "reasons": item.reasons,
        "line_count": item.file.line_count,
    }
    if item.file.repo_label:
        data["repo"] = item.file.repo_label
        data["relative_path"] = item.file.relative_path
    return data


def _serialize_compressed_file(item: CompressedFile) -> dict:
    data = {
        "path": item.path,
        "strategy": item.strategy,
        "original_tokens": item.original_tokens,
        "compressed_tokens": item.compressed_tokens,
        "text": item.text,
        "chunk_strategy": item.chunk_strategy,
        "chunk_reason": item.chunk_reason,
        "selected_ranges": item.selected_ranges,
    }
    if item.repo_label:
        data["repo"] = item.repo_label
        data["relative_path"] = item.relative_path
    return data


def _serialize_scanned_repo(item: ScannedWorkspaceRepo) -> dict:
    return {
        "label": item.label,
        "path": item.path,
        "scanned_files": item.scanned_files,
    }


def _selected_repos_from_ranked(ranked: list[RankedFile]) -> list[str]:
    return sorted({item.file.repo_label for item in ranked if item.file.repo_label})


def _selected_repos_from_compressed(compressed: CompressionResult) -> list[str]:
    return sorted({item.repo_label for item in compressed.compressed_files if item.repo_label})


def _scan_internal_paths(config: ContextBudgetConfig) -> set[str]:
    paths = {config.cache.cache_file, config.telemetry.file_path}
    return {path for path in paths if path}


def run_scan_refresh_stage(repo: Path, config: ContextBudgetConfig) -> ScanRefreshResult:
    """Refresh repository scan state according to scan settings."""

    return refresh_scan_index(
        repo,
        max_file_size_bytes=config.scan.max_file_size_bytes,
        preview_chars=config.scan.preview_chars,
        include_globs=config.scan.include_globs,
        ignore_globs=config.scan.ignore_globs,
        ignore_dirs=config.scan.ignore_dirs,
        binary_extensions=config.scan.binary_extensions,
        internal_paths=_scan_internal_paths(config),
    )


def run_scan_stage(repo: Path, config: ContextBudgetConfig) -> list[FileRecord]:
    """Scan repository files according to scan settings."""

    return run_scan_refresh_stage(repo, config).records


def run_scan_workspace_stage(
    workspace: WorkspaceDefinition,
    config: ContextBudgetConfig,
) -> tuple[list[FileRecord], list[ScannedWorkspaceRepo]]:
    """Scan all repositories defined by a workspace."""

    return scan_workspace(workspace, config=config, internal_paths=_scan_internal_paths(config))


def run_score_stage(
    task: str,
    files: list[FileRecord],
    config: ContextBudgetConfig,
    plugins: ResolvedPlugins | None = None,
) -> list[RankedFile]:
    """Rank scanned files by deterministic relevance score."""

    resolved = plugins if plugins is not None else resolve_plugins(config)
    return resolved.scorer.score(
        task=task,
        files=files,
        settings=config.score,
        options=resolved.scorer_options,
        estimate_tokens=resolved.estimate_tokens,
    )


def run_cache_stage(repo: Path, config: ContextBudgetConfig) -> SummaryCacheBackend:
    """Create cache adapter configured for the repository."""

    return create_summary_cache_backend(
        repo_path=repo,
        backend=config.cache.backend,
        cache_file=config.cache.cache_file,
        enabled=config.cache.summary_cache_enabled,
    )


def run_pack_stage(
    task: str,
    repo: Path,
    ranked: list[RankedFile],
    max_tokens: int,
    cache: SummaryCacheBackend,
    config: ContextBudgetConfig,
    plugins: ResolvedPlugins | None = None,
) -> CompressionResult:
    """Compress ranked files under token budget."""

    resolved = plugins if plugins is not None else resolve_plugins(config)
    return resolved.compressor.compress(
        task=task,
        repo=repo,
        ranked_files=ranked,
        max_tokens=max_tokens,
        cache=cache,
        settings=config.compression,
        summarization_settings=config.summarization,
        options=resolved.compressor_options,
        estimate_tokens=resolved.estimate_tokens,
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
    workspace_path: Path | None = None,
    scanned_repos: list[ScannedWorkspaceRepo] | None = None,
    implementations: dict[str, str] | None = None,
    token_estimator: dict[str, object] | None = None,
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
        ranked_files=[_serialize_ranked_file(item) for item in ranked[:effective_top_files]],
        compressed_context=[_serialize_compressed_file(item) for item in compressed.compressed_files],
        files_included=compressed.files_included,
        files_skipped=compressed.files_skipped,
        budget={
            "estimated_input_tokens": compressed.estimated_input_tokens,
            "estimated_saved_tokens": compressed.estimated_saved_tokens,
            "duplicate_reads_prevented": compressed.duplicate_reads_prevented,
            "quality_risk_estimate": compressed.quality_risk_estimate,
        },
        cache=compressed.cache,
        summarizer=compressed.summarizer,
        token_estimator=TokenEstimatorReport(
            **normalize_token_estimator_report(
                {
                    "token_estimator": token_estimator or {},
                    "implementations": dict(implementations or {}),
                }
            )
        ),
        cache_hits=compressed.cache_hits,
        generated_at=datetime.now(timezone.utc).isoformat(),
        workspace=str(workspace_path) if workspace_path is not None else "",
        scanned_repos=[_serialize_scanned_repo(item) for item in (scanned_repos or [])],
        selected_repos=_selected_repos_from_compressed(compressed),
        implementations=dict(implementations or {}),
    )


def build_plan_result(
    task: str,
    repo: Path,
    scanned_files: int,
    ranked: list[RankedFile],
    top_n: int,
    workspace_path: Path | None = None,
    scanned_repos: list[ScannedWorkspaceRepo] | None = None,
    implementations: dict[str, str] | None = None,
    token_estimator: dict[str, object] | None = None,
) -> PlanStageResult:
    """Build serialized plan-stage payload."""

    return PlanStageResult(
        task=task,
        repo=str(repo),
        scanned_files=scanned_files,
        ranked_files=[_serialize_ranked_file(item) for item in ranked[:top_n]],
        workspace=str(workspace_path) if workspace_path is not None else "",
        scanned_repos=[_serialize_scanned_repo(item) for item in (scanned_repos or [])],
        selected_repos=_selected_repos_from_ranked(ranked[:top_n]),
        implementations=dict(implementations or {}),
        token_estimator=dict(token_estimator or {}),
    )


def as_json_dict(report: RunReport) -> dict:
    """Convert typed run report into JSON-serializable dictionary."""

    data = asdict(report)
    for key in ("workspace", "scanned_repos", "selected_repos", "implementations", "token_estimator"):
        if not data.get(key):
            data.pop(key, None)
    return data
