from __future__ import annotations

"""Top-level pipeline API wrappers preserving CLI compatibility."""

from pathlib import Path

from contextbudget.cache import normalize_cache_report
from contextbudget.compressors.summarizers import normalize_summarizer_report
from contextbudget.config import ContextBudgetConfig, WorkspaceDefinition, load_config
from contextbudget.core.diffing import diff_run_artifacts
from contextbudget.core.tokens import normalize_token_estimator_report
from contextbudget.plugins import ResolvedPlugins, resolve_plugins
from contextbudget.schemas.models import DEFAULT_TOP_FILES, RunReport
from contextbudget.telemetry import TelemetrySession, TelemetrySink, build_telemetry_sink
from contextbudget.stages.workflow import (
    as_json_dict as stage_as_json_dict,
    build_plan_result,
    run_cache_stage,
    run_pack_stage,
    run_render_stage,
    run_scan_stage,
    run_scan_workspace_stage,
    run_score_stage,
)


def _build_telemetry_session(
    *,
    repo: Path,
    config: ContextBudgetConfig,
    command: str,
    telemetry_sink: TelemetrySink | None = None,
) -> TelemetrySession:
    sink = telemetry_sink or build_telemetry_sink(
        repo=repo,
        enabled=config.telemetry.enabled,
        sink=config.telemetry.sink,
        file_path=config.telemetry.file_path,
    )
    return TelemetrySession(
        sink=sink,
        base_payload={
            "command": command,
            "repo": repo,
        },
    )


def run_plan(
    task: str,
    repo: Path,
    top_n: int | None = None,
    config: ContextBudgetConfig | None = None,
    config_path: Path | None = None,
    telemetry_sink: TelemetrySink | None = None,
    workspace: WorkspaceDefinition | None = None,
    plugins: ResolvedPlugins | None = None,
) -> dict:
    """Run plan command pipeline and return serializable payload."""

    cfg = config if config is not None else (workspace.config if workspace is not None else load_config(repo, config_path=config_path))
    resolved_plugins = plugins if plugins is not None else resolve_plugins(cfg)
    effective_top_n = top_n if top_n is not None else (cfg.budget.top_files or DEFAULT_TOP_FILES)
    target_repo = workspace.root if workspace is not None else repo
    telemetry = _build_telemetry_session(
        repo=target_repo,
        config=cfg,
        command="plan",
        telemetry_sink=telemetry_sink,
    )
    telemetry.emit(
        "run_started",
        top_files=effective_top_n,
        workspace=str(workspace.path) if workspace is not None else "",
        repo_count=len(workspace.repos) if workspace is not None else 1,
    )
    if workspace is not None:
        files, scanned_repos = run_scan_workspace_stage(workspace, cfg)
    else:
        files = run_scan_stage(repo, cfg)
        scanned_repos = []
    telemetry.emit("scan_completed", scanned_files=len(files), scanned_repos=len(scanned_repos))
    ranked = run_score_stage(task, files, cfg, plugins=resolved_plugins)
    telemetry.emit("scoring_completed", scanned_files=len(files), ranked_files=len(ranked), top_files=effective_top_n)
    plan = build_plan_result(
        task,
        target_repo,
        scanned_files=len(files),
        ranked=ranked,
        top_n=effective_top_n,
        workspace_path=workspace.path if workspace is not None else None,
        scanned_repos=scanned_repos,
        implementations=resolved_plugins.plan_implementations(),
        token_estimator=resolved_plugins.token_estimator_report,
    )
    data = {
        "task": plan.task,
        "repo": plan.repo,
        "scanned_files": plan.scanned_files,
        "ranked_files": plan.ranked_files,
    }
    if plan.workspace:
        data["workspace"] = plan.workspace
        data["scanned_repos"] = plan.scanned_repos
        data["selected_repos"] = plan.selected_repos
    if plan.implementations:
        data["implementations"] = plan.implementations
    if plan.token_estimator:
        data["token_estimator"] = plan.token_estimator
    return data


def run_pack(
    task: str,
    repo: Path,
    max_tokens: int | None = None,
    top_files: int | None = None,
    config: ContextBudgetConfig | None = None,
    config_path: Path | None = None,
    telemetry_sink: TelemetrySink | None = None,
    workspace: WorkspaceDefinition | None = None,
    plugins: ResolvedPlugins | None = None,
) -> RunReport:
    """Run pack command pipeline and return typed run report."""

    cfg = config if config is not None else (workspace.config if workspace is not None else load_config(repo, config_path=config_path))
    resolved_plugins = plugins if plugins is not None else resolve_plugins(cfg)
    effective_max_tokens = max_tokens if max_tokens is not None else cfg.budget.max_tokens
    effective_top_files = top_files if top_files is not None else cfg.budget.top_files
    target_repo = workspace.root if workspace is not None else repo
    telemetry = _build_telemetry_session(
        repo=target_repo,
        config=cfg,
        command="pack",
        telemetry_sink=telemetry_sink,
    )
    telemetry_top_files = effective_top_files if effective_top_files is not None else DEFAULT_TOP_FILES
    telemetry.emit(
        "run_started",
        max_tokens=effective_max_tokens,
        top_files=telemetry_top_files,
        workspace=str(workspace.path) if workspace is not None else "",
        repo_count=len(workspace.repos) if workspace is not None else 1,
    )

    if workspace is not None:
        files, scanned_repos = run_scan_workspace_stage(workspace, cfg)
    else:
        files = run_scan_stage(repo, cfg)
        scanned_repos = []
    telemetry.emit("scan_completed", scanned_files=len(files), scanned_repos=len(scanned_repos))
    ranked = run_score_stage(task, files, cfg, plugins=resolved_plugins)
    ranked_count = len(ranked)
    telemetry.emit("scoring_completed", scanned_files=len(files), ranked_files=ranked_count, top_files=telemetry_top_files)
    if effective_top_files is not None:
        ranked = ranked[:effective_top_files]
    cache = run_cache_stage(target_repo, cfg)
    compressed = run_pack_stage(task, target_repo, ranked, effective_max_tokens, cache, cfg, plugins=resolved_plugins)
    cache.save()
    report = run_render_stage(
        task,
        target_repo,
        ranked,
        compressed,
        effective_max_tokens,
        cfg,
        top_files=effective_top_files,
        workspace_path=workspace.path if workspace is not None else None,
        scanned_repos=scanned_repos,
        implementations=resolved_plugins.pack_implementations(),
        token_estimator=resolved_plugins.token_estimator_report,
    )
    telemetry.emit(
        "pack_completed",
        max_tokens=effective_max_tokens,
        scanned_files=len(files),
        scanned_repos=len(scanned_repos),
        ranked_files=ranked_count,
        files_included=len(report.files_included),
        files_skipped=len(report.files_skipped),
        top_files=telemetry_top_files,
        estimated_input_tokens=int(report.budget.get("estimated_input_tokens", 0) or 0),
        estimated_saved_tokens=int(report.budget.get("estimated_saved_tokens", 0) or 0),
        cache_hits=int(report.cache_hits or 0),
        duplicate_reads_prevented=int(report.budget.get("duplicate_reads_prevented", 0) or 0),
        quality_risk_estimate=str(report.budget.get("quality_risk_estimate", "unknown")),
    )
    return report

def run_report_from_json(data: dict) -> dict:
    """Extract report summary fields from a run JSON payload."""

    budget = data.get("budget", {})
    cache = normalize_cache_report(data)
    summarizer = normalize_summarizer_report(data)
    token_estimator = normalize_token_estimator_report(data)
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
        "cache": cache,
        "summarizer": summarizer,
        "token_estimator": token_estimator,
        "cache_hits": cache.get("hits", 0),
        "workspace": data.get("workspace", ""),
        "scanned_repos": data.get("scanned_repos", []),
        "selected_repos": data.get("selected_repos", []),
        "implementations": data.get("implementations", {}),
    }


def run_diff_from_json(old_data: dict, new_data: dict, old_label: str = "old", new_label: str = "new") -> dict:
    """Build a run-to-run delta report from two run JSON payloads."""

    return diff_run_artifacts(old_data, new_data, old_label=old_label, new_label=new_label)


def as_json_dict(report: RunReport) -> dict:
    """Convert run report to JSON dict."""

    return stage_as_json_dict(report)
