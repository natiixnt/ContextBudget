from __future__ import annotations

"""Top-level pipeline API wrappers preserving CLI compatibility."""

from pathlib import Path

from contextbudget.config import ContextBudgetConfig, load_config
from contextbudget.core.diffing import diff_run_artifacts
from contextbudget.schemas.models import DEFAULT_TOP_FILES, RunReport
from contextbudget.telemetry import TelemetrySession, TelemetrySink, build_telemetry_sink
from contextbudget.stages.workflow import (
    as_json_dict as stage_as_json_dict,
    build_plan_result,
    run_cache_stage,
    run_pack_stage,
    run_render_stage,
    run_scan_stage,
    run_score_stage,
)


def _build_telemetry_session(
    *,
    repo: Path,
    config: ContextBudgetConfig,
    command: str,
    task: str,
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
            "task": task,
            "repo": str(repo),
        },
    )


def run_plan(
    task: str,
    repo: Path,
    top_n: int | None = None,
    config: ContextBudgetConfig | None = None,
    config_path: Path | None = None,
    telemetry_sink: TelemetrySink | None = None,
) -> dict:
    """Run plan command pipeline and return serializable payload."""

    cfg = config if config is not None else load_config(repo, config_path=config_path)
    effective_top_n = top_n if top_n is not None else (cfg.budget.top_files or DEFAULT_TOP_FILES)
    telemetry = _build_telemetry_session(
        repo=repo,
        config=cfg,
        command="plan",
        task=task,
        telemetry_sink=telemetry_sink,
    )
    telemetry.emit("run_started", top_files=effective_top_n)
    files = run_scan_stage(repo, cfg)
    telemetry.emit("scan_completed", scanned_files=len(files))
    ranked = run_score_stage(task, files, cfg)
    telemetry.emit("scoring_completed", ranked_files=len(ranked))
    plan = build_plan_result(task, repo, scanned_files=len(files), ranked=ranked, top_n=effective_top_n)
    return {
        "task": plan.task,
        "repo": plan.repo,
        "scanned_files": plan.scanned_files,
        "ranked_files": plan.ranked_files,
    }


def run_pack(
    task: str,
    repo: Path,
    max_tokens: int | None = None,
    top_files: int | None = None,
    config: ContextBudgetConfig | None = None,
    config_path: Path | None = None,
    telemetry_sink: TelemetrySink | None = None,
) -> RunReport:
    """Run pack command pipeline and return typed run report."""

    cfg = config if config is not None else load_config(repo, config_path=config_path)
    effective_max_tokens = max_tokens if max_tokens is not None else cfg.budget.max_tokens
    effective_top_files = top_files if top_files is not None else cfg.budget.top_files
    telemetry = _build_telemetry_session(
        repo=repo,
        config=cfg,
        command="pack",
        task=task,
        telemetry_sink=telemetry_sink,
    )
    telemetry.emit(
        "run_started",
        max_tokens=effective_max_tokens,
        top_files=effective_top_files if effective_top_files is not None else DEFAULT_TOP_FILES,
    )

    files = run_scan_stage(repo, cfg)
    telemetry.emit("scan_completed", scanned_files=len(files))
    ranked = run_score_stage(task, files, cfg)
    telemetry.emit("scoring_completed", ranked_files=len(ranked))
    if effective_top_files is not None:
        ranked = ranked[:effective_top_files]
    cache = run_cache_stage(repo, cfg)
    compressed = run_pack_stage(task, repo, ranked, effective_max_tokens, cache, cfg)
    cache.save()
    report = run_render_stage(
        task,
        repo,
        ranked,
        compressed,
        effective_max_tokens,
        cfg,
        top_files=effective_top_files,
    )
    telemetry.emit(
        "pack_completed",
        files_included=len(report.files_included),
        files_skipped=len(report.files_skipped),
        estimated_input_tokens=int(report.budget.get("estimated_input_tokens", 0) or 0),
        estimated_saved_tokens=int(report.budget.get("estimated_saved_tokens", 0) or 0),
        quality_risk_estimate=str(report.budget.get("quality_risk_estimate", "unknown")),
    )
    return report

def run_report_from_json(data: dict) -> dict:
    """Extract report summary fields from a run JSON payload."""

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


def run_diff_from_json(old_data: dict, new_data: dict, old_label: str = "old", new_label: str = "new") -> dict:
    """Build a run-to-run delta report from two run JSON payloads."""

    return diff_run_artifacts(old_data, new_data, old_label=old_label, new_label=new_label)


def as_json_dict(report: RunReport) -> dict:
    """Convert run report to JSON dict."""

    return stage_as_json_dict(report)
