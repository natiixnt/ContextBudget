from __future__ import annotations

"""Top-level pipeline API wrappers preserving CLI compatibility."""

from pathlib import Path
from typing import Any, Sequence

from contextbudget.cache import RunHistoryEntry, append_run_history_entry, normalize_cache_report
from contextbudget.compressors.summarizers import normalize_summarizer_report
from contextbudget.config import ContextBudgetConfig, WorkspaceDefinition, load_config
from contextbudget.core.delta import build_delta_report, effective_pack_metrics, resolve_previous_run_label
from contextbudget.core.agent_planning import build_agent_workflow_plan
from contextbudget.core.model_profiles import normalize_model_profile_report, prepare_config_for_model_profile
from contextbudget.core.diffing import diff_run_artifacts
from contextbudget.core.heatmap import build_heatmap_report, heatmap_as_dict
from contextbudget.core.pr_audit import analyze_pull_request, pr_audit_as_dict
from contextbudget.core.render import read_json, render_pr_comment_markdown
from contextbudget.core.tokens import normalize_token_estimator_report
from contextbudget.plugins import ResolvedPlugins, resolve_plugins
from contextbudget.schemas.models import DEFAULT_TOP_FILES, RunReport
from contextbudget.telemetry import TelemetrySession, TelemetrySink, build_telemetry_sink
from contextbudget.stages.workflow import (
    as_json_dict as stage_as_json_dict,
    build_agent_plan_result,
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
    prepared_cfg, model_profile = prepare_config_for_model_profile(cfg)
    resolved_plugins = plugins if plugins is not None else resolve_plugins(prepared_cfg)
    effective_top_n = top_n if top_n is not None else (prepared_cfg.budget.top_files or DEFAULT_TOP_FILES)
    target_repo = workspace.root if workspace is not None else repo
    telemetry = _build_telemetry_session(
        repo=target_repo,
        config=prepared_cfg,
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
        files, scanned_repos = run_scan_workspace_stage(workspace, prepared_cfg)
    else:
        files = run_scan_stage(repo, prepared_cfg)
        scanned_repos = []
    telemetry.emit("scan_completed", scanned_files=len(files), scanned_repos=len(scanned_repos))
    ranked = run_score_stage(task, files, prepared_cfg, repo=target_repo, plugins=resolved_plugins)
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
        model_profile=model_profile,
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
    if plan.model_profile:
        data["model_profile"] = plan.model_profile
    return data


def run_plan_agent(
    task: str,
    repo: Path,
    top_n: int | None = None,
    config: ContextBudgetConfig | None = None,
    config_path: Path | None = None,
    telemetry_sink: TelemetrySink | None = None,
    workspace: WorkspaceDefinition | None = None,
    plugins: ResolvedPlugins | None = None,
) -> dict:
    """Run agent workflow planning and return a serializable artifact."""

    cfg = config if config is not None else (workspace.config if workspace is not None else load_config(repo, config_path=config_path))
    prepared_cfg, model_profile = prepare_config_for_model_profile(cfg)
    resolved_plugins = plugins if plugins is not None else resolve_plugins(prepared_cfg)
    effective_top_n = top_n if top_n is not None else (prepared_cfg.budget.top_files or DEFAULT_TOP_FILES)
    target_repo = workspace.root if workspace is not None else repo
    telemetry = _build_telemetry_session(
        repo=target_repo,
        config=prepared_cfg,
        command="plan_agent",
        telemetry_sink=telemetry_sink,
    )
    telemetry.emit(
        "run_started",
        top_files=effective_top_n,
        workspace=str(workspace.path) if workspace is not None else "",
        repo_count=len(workspace.repos) if workspace is not None else 1,
    )
    if workspace is not None:
        files, scanned_repos = run_scan_workspace_stage(workspace, prepared_cfg)
    else:
        files = run_scan_stage(repo, prepared_cfg)
        scanned_repos = []
    telemetry.emit("scan_completed", scanned_files=len(files), scanned_repos=len(scanned_repos))
    ranked = run_score_stage(task, files, prepared_cfg, repo=target_repo, plugins=resolved_plugins)
    telemetry.emit("scoring_completed", scanned_files=len(files), ranked_files=len(ranked), top_files=effective_top_n)
    workflow_plan = build_agent_workflow_plan(
        task=task,
        files=files,
        ranked=ranked,
        top_n=effective_top_n,
        estimate_tokens=resolved_plugins.estimate_tokens,
        score_task=lambda step_task: run_score_stage(
            step_task,
            files,
            prepared_cfg,
            repo=target_repo,
            plugins=resolved_plugins,
        ),
        workspace_mode=workspace is not None,
    )
    report = build_agent_plan_result(
        task,
        target_repo,
        scanned_files=len(files),
        ranked=ranked,
        workflow_plan=workflow_plan,
        top_n=effective_top_n,
        workspace_path=workspace.path if workspace is not None else None,
        scanned_repos=scanned_repos,
        implementations={
            **resolved_plugins.plan_implementations(),
            "agent_planner": "builtin.lifecycle",
        },
        token_estimator=resolved_plugins.token_estimator_report,
        model_profile=model_profile,
    )
    telemetry.emit(
        "plan_completed",
        scanned_files=len(files),
        scanned_repos=len(scanned_repos),
        ranked_files=len(ranked),
        top_files=effective_top_n,
        workflow_steps=len(report.steps),
        total_estimated_tokens=report.total_estimated_tokens,
        unique_context_tokens=report.unique_context_tokens,
        reused_context_tokens=report.reused_context_tokens,
    )
    return stage_as_json_dict(report)


def run_pack(
    task: str,
    repo: Path,
    max_tokens: int | None = None,
    top_files: int | None = None,
    delta_from: dict[str, Any] | str | Path | None = None,
    config: ContextBudgetConfig | None = None,
    config_path: Path | None = None,
    telemetry_sink: TelemetrySink | None = None,
    workspace: WorkspaceDefinition | None = None,
    plugins: ResolvedPlugins | None = None,
    record_history: bool = True,
) -> RunReport:
    """Run pack command pipeline and return typed run report."""

    cfg = config if config is not None else (workspace.config if workspace is not None else load_config(repo, config_path=config_path))
    prepared_cfg, model_profile = prepare_config_for_model_profile(cfg, requested_max_tokens=max_tokens)
    resolved_plugins = plugins if plugins is not None else resolve_plugins(prepared_cfg)
    effective_max_tokens = prepared_cfg.budget.max_tokens
    effective_top_files = top_files if top_files is not None else prepared_cfg.budget.top_files
    target_repo = workspace.root if workspace is not None else repo
    telemetry = _build_telemetry_session(
        repo=target_repo,
        config=prepared_cfg,
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
        files, scanned_repos = run_scan_workspace_stage(workspace, prepared_cfg)
    else:
        files = run_scan_stage(repo, prepared_cfg)
        scanned_repos = []
    telemetry.emit("scan_completed", scanned_files=len(files), scanned_repos=len(scanned_repos))
    ranked = run_score_stage(task, files, prepared_cfg, repo=target_repo, plugins=resolved_plugins)
    ranked_count = len(ranked)
    telemetry.emit("scoring_completed", scanned_files=len(files), ranked_files=ranked_count, top_files=telemetry_top_files)
    if effective_top_files is not None:
        ranked = ranked[:effective_top_files]
    cache = run_cache_stage(target_repo, prepared_cfg)
    compressed = run_pack_stage(
        task,
        target_repo,
        ranked,
        effective_max_tokens,
        cache,
        prepared_cfg,
        plugins=resolved_plugins,
    )
    cache.save()
    report = run_render_stage(
        task,
        target_repo,
        ranked,
        compressed,
        effective_max_tokens,
        prepared_cfg,
        top_files=effective_top_files,
        workspace_path=workspace.path if workspace is not None else None,
        scanned_repos=scanned_repos,
        implementations=resolved_plugins.pack_implementations(),
        token_estimator=resolved_plugins.token_estimator_report,
        model_profile=model_profile,
    )
    if record_history:
        selected_set = set(report.files_included)
        considered_files = [item.file.path for item in ranked]
        append_run_history_entry(
            target_repo,
            RunHistoryEntry(
                generated_at=report.generated_at,
                task=task,
                selected_files=list(report.files_included),
                ignored_files=[path for path in considered_files if path not in selected_set],
                candidate_files=considered_files,
                token_usage={
                    "max_tokens": effective_max_tokens,
                    "estimated_input_tokens": int(report.budget.get("estimated_input_tokens", 0) or 0),
                    "estimated_saved_tokens": int(report.budget.get("estimated_saved_tokens", 0) or 0),
                    "quality_risk_estimate": str(report.budget.get("quality_risk_estimate", "unknown")),
                },
                result_artifacts={
                    "run_json": "",
                    "run_markdown": "",
                },
                repo=str(target_repo),
                workspace=str(workspace.path) if workspace is not None else "",
            ),
            enabled=prepared_cfg.cache.run_history_enabled,
            history_file=prepared_cfg.cache.history_file,
            max_entries=prepared_cfg.cache.history_max_entries,
        )
    if delta_from is not None:
        if isinstance(delta_from, dict):
            previous_run = dict(delta_from)
        elif isinstance(delta_from, (str, Path)):
            previous_run = read_json(Path(delta_from))
        else:
            raise TypeError("delta_from must be a dict, path string, or Path")
        report.delta = build_delta_report(
            previous_run,
            as_json_dict(report),
            previous_label=resolve_previous_run_label(delta_from),
            token_estimator=resolved_plugins.estimate_tokens,
        )
    effective_metrics = effective_pack_metrics(as_json_dict(report))
    effective_files_included = effective_metrics.get("files_included", [])
    if not isinstance(effective_files_included, list):
        effective_files_included = []
    telemetry.emit(
        "pack_completed",
        max_tokens=effective_max_tokens,
        scanned_files=len(files),
        scanned_repos=len(scanned_repos),
        ranked_files=ranked_count,
        files_included=len(effective_files_included),
        files_skipped=len(report.files_skipped),
        top_files=telemetry_top_files,
        estimated_input_tokens=int(effective_metrics.get("estimated_input_tokens", 0) or 0),
        estimated_saved_tokens=int(effective_metrics.get("estimated_saved_tokens", 0) or 0),
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
    model_profile = normalize_model_profile_report(data)
    report = {
        "task": data.get("task", ""),
        "repo": data.get("repo", ""),
        "generated_at": data.get("generated_at", ""),
        "estimated_input_tokens": budget.get("estimated_input_tokens", 0),
        "estimated_saved_tokens": budget.get("estimated_saved_tokens", 0),
        "ranked_files": data.get("ranked_files", []),
        "files_included": data.get("files_included", []),
        "files_skipped": data.get("files_skipped", []),
        "duplicate_reads_prevented": budget.get("duplicate_reads_prevented", 0),
        "quality_risk_estimate": budget.get("quality_risk_estimate", "unknown"),
        "cache": cache,
        "summarizer": summarizer,
        "token_estimator": token_estimator,
        "model_profile": model_profile,
        "cache_hits": cache.get("hits", 0),
        "workspace": data.get("workspace", ""),
        "scanned_repos": data.get("scanned_repos", []),
        "selected_repos": data.get("selected_repos", []),
        "implementations": data.get("implementations", {}),
    }
    delta = data.get("delta", {})
    if isinstance(delta, dict) and delta:
        report["delta"] = delta
    return report


def run_diff_from_json(old_data: dict, new_data: dict, old_label: str = "old", new_label: str = "new") -> dict:
    """Build a run-to-run delta report from two run JSON payloads."""

    return diff_run_artifacts(old_data, new_data, old_label=old_label, new_label=new_label)


def run_pr_audit(
    repo: Path,
    *,
    base_ref: str | None = None,
    head_ref: str | None = None,
    config: ContextBudgetConfig | None = None,
    config_path: Path | None = None,
    plugins: ResolvedPlugins | None = None,
) -> dict:
    """Build a pull-request context audit from git diff state."""

    cfg = config if config is not None else load_config(repo, config_path=config_path)
    resolved_plugins = plugins if plugins is not None else resolve_plugins(cfg)
    report = analyze_pull_request(
        repo,
        base_ref=base_ref,
        head_ref=head_ref,
        config=cfg,
        plugins=resolved_plugins,
    )
    data = pr_audit_as_dict(report)
    data["comment_markdown"] = render_pr_comment_markdown(data)
    return data


def run_heatmap(history: Sequence[str | Path] | None = None, *, limit: int = 10) -> dict:
    """Aggregate historical pack artifacts into a heatmap report."""

    report = build_heatmap_report(history, limit=limit)
    return heatmap_as_dict(report)


def as_json_dict(report: RunReport) -> dict:
    """Convert run report to JSON dict."""

    return stage_as_json_dict(report)
