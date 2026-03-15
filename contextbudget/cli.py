from __future__ import annotations

"""CLI entrypoint for ContextBudget commands."""

import argparse
import json as _json_mod
from pathlib import Path
import time

from contextbudget.config import ContextBudgetConfig, load_config
from contextbudget.core.policy import (
    default_strict_policy,
    load_policy,
)
from contextbudget.agents.middleware import ContextBudgetMiddleware
from contextbudget.engine import BudgetPolicyViolationError, ContextBudgetEngine
from contextbudget.core.render import (
    render_advise_markdown,
    render_agent_plan_markdown,
    render_agent_simulation_markdown,
    render_benchmark_markdown,
    render_context_dataset_markdown,
    render_dataset_markdown,
    render_diff_markdown,
    render_drift_markdown,
    render_heatmap_markdown,
    render_observe_markdown,
    render_pack_markdown,
    render_pipeline_markdown,
    render_plan_markdown,
    render_policy_markdown,
    render_pr_audit_markdown,
    render_pr_comment_markdown,
    render_prepare_context_markdown,
    render_profile_markdown,
    render_read_profile_markdown,
    render_report_markdown,
    render_visualize_markdown,
    write_json,
)
from contextbudget.schemas.models import normalize_repo
from contextbudget.scanners.incremental import ScanRefreshResult, ScanRefreshSummary
from contextbudget.stages.workflow import run_scan_refresh_stage


def _base_name(task: str) -> str:
    sanitized = "-".join(task.lower().strip().split())
    return sanitized[:40] if sanitized else "run"


def _resolve_config_path(path: str | None) -> Path | None:
    if not path:
        return None
    return Path(path).resolve()


def _render_scan_summary(prefix: str, tracked_repo: Path, summary: ScanRefreshSummary) -> str:
    return (
        f"{prefix}: repo={tracked_repo} "
        f"tracked={summary.tracked_files} included={summary.included_files} "
        f"reused={summary.reused_count} added={summary.added_count} "
        f"updated={summary.updated_count} removed={summary.removed_count}"
    )


def _render_scan_change_paths(summary: ScanRefreshSummary, limit: int = 5) -> str:
    changes: list[str] = []
    if summary.added_paths:
        joined = ", ".join(summary.added_paths[:limit])
        if len(summary.added_paths) > limit:
            joined = f"{joined}, +{len(summary.added_paths) - limit} more"
        changes.append(f"added[{joined}]")
    if summary.updated_paths:
        joined = ", ".join(summary.updated_paths[:limit])
        if len(summary.updated_paths) > limit:
            joined = f"{joined}, +{len(summary.updated_paths) - limit} more"
        changes.append(f"updated[{joined}]")
    if summary.removed_paths:
        joined = ", ".join(summary.removed_paths[:limit])
        if len(summary.removed_paths) > limit:
            joined = f"{joined}, +{len(summary.removed_paths) - limit} more"
        changes.append(f"removed[{joined}]")
    return " ".join(changes)


def cmd_plan(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    data = engine.plan(
        task=args.task,
        repo=args.repo,
        workspace=args.workspace,
        top_files=args.top_files,
    )

    base = args.out_prefix or f"contextbudget-plan-{_base_name(args.task)}"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")

    write_json(json_path, data)
    md_path.write_text(render_plan_markdown(data), encoding="utf-8")

    print(f"Wrote plan JSON: {json_path}")
    print(f"Wrote plan Markdown: {md_path}")
    for idx, item in enumerate(data["ranked_files"][:10], start=1):
        print(f"{idx}. {item['path']} (score={item['score']})")
    return 0


def cmd_plan_agent(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    data = engine.plan_agent(
        task=args.task,
        repo=args.repo,
        workspace=args.workspace,
        top_files=args.top_files,
    )

    base = args.out_prefix or f"contextbudget-agent-plan-{_base_name(args.task)}"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")

    write_json(json_path, data)
    md_path.write_text(render_agent_plan_markdown(data), encoding="utf-8")

    print(f"Wrote agent plan JSON: {json_path}")
    print(f"Wrote agent plan Markdown: {md_path}")
    shared_context = data.get("shared_context", [])
    if isinstance(shared_context, list) and shared_context:
        preview = ", ".join(
            f"{item.get('path', '')} ({item.get('estimated_tokens', 0)})"
            for item in shared_context[:5]
            if isinstance(item, dict)
        )
        if len(shared_context) > 5:
            preview = f"{preview}, +{len(shared_context) - 5} more"
        print(f"Shared context: {preview}")

    steps = data.get("steps", [])
    if isinstance(steps, list):
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            print(f"{idx}. {step.get('title', '')} (tokens={step.get('estimated_tokens', 0)})")
            context = step.get("context", [])
            if isinstance(context, list) and context:
                preview = ", ".join(
                    item.get("path", "")
                    for item in context[:5]
                    if isinstance(item, dict)
                )
                if len(context) > 5:
                    preview = f"{preview}, +{len(context) - 5} more"
                print(f"   context: {preview}")
            else:
                print("   context: none")

    print(
        "Total estimated tokens: "
        f"{data.get('total_estimated_tokens', 0)} "
        f"(unique={data.get('unique_context_tokens', 0)}, reused={data.get('reused_context_tokens', 0)})"
    )
    return 0


def cmd_simulate_agent(args: argparse.Namespace) -> int:
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "human")
    if getattr(args, "list_models", False):
        from contextbudget.core.agent_cost import list_known_models
        rows = list_known_models()
        if fmt == "json":
            print(_json_mod.dumps(rows, indent=2, default=str))
        else:
            print(f"{'Model':<32} {'Provider':<12} {'Input $/MTok':>14} {'Output $/MTok':>14}")
            print("-" * 76)
            for row in rows:
                print(
                    f"{row['model']:<32} {row['provider']:<12} "
                    f"{row['input_per_1m_usd']:>14.4f} {row['output_per_1m_usd']:>14.4f}"
                )
        return 0

    run_artifact_path = getattr(args, "run_artifact", None)
    task = args.task or ""
    repo = args.repo

    if run_artifact_path:
        run_artifact_file = Path(run_artifact_path)
        if not run_artifact_file.exists():
            print(f"Error: run artifact not found: {run_artifact_file}")
            return 2
        from contextbudget.core.render import read_json as _read_json
        _run_data = _read_json(run_artifact_file)
        if not task:
            task = str(_run_data.get("task", "") or "")
        if repo == ".":
            _raw_repo = str(_run_data.get("repo", "") or "")
            if _raw_repo:
                repo = _raw_repo

    if not task:
        print("error: task is required (provide as argument or via --run-artifact)")
        return 2

    engine = ContextBudgetEngine(config_path=args.config)
    data = engine.simulate_agent(
        task=task,
        repo=repo,
        workspace=args.workspace,
        top_files=args.top_files,
        prompt_overhead_per_step=args.prompt_overhead,
        output_tokens_per_step=args.output_tokens,
        context_mode=args.context_mode,
        model=args.model,
        price_per_1m_input=args.price_input,
        price_per_1m_output=args.price_output,
    )

    base = args.out_prefix or f"contextbudget-simulate-{_base_name(task)}"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")

    write_json(json_path, data)
    md_path.write_text(render_agent_simulation_markdown(data), encoding="utf-8")

    if fmt == "json":
        print(_json_mod.dumps(data, indent=2, default=str))
        return 0

    print(f"Wrote simulation JSON: {json_path}")
    print(f"Wrote simulation Markdown: {md_path}")
    print(f"Context mode: {data.get('context_mode', 'isolated')}")

    # Cost summary
    cost = data.get("cost_estimate", {})
    if isinstance(cost, dict) and cost:
        model_name = cost.get("model", data.get("model", ""))
        provider = cost.get("provider", "")
        provider_str = f" ({provider})" if provider else ""
        print(
            f"Model: {model_name}{provider_str} "
            f"| input ${cost.get('input_per_1m_usd', 0):.2f}/MTok "
            f"| output ${cost.get('output_per_1m_usd', 0):.2f}/MTok"
        )
        print(
            f"Estimated cost: ${cost.get('total_cost_usd', 0.0):.4f} USD "
            f"(input ${cost.get('total_input_cost_usd', 0.0):.4f} + "
            f"output ${cost.get('total_output_cost_usd', 0.0):.4f})"
        )
        notes = cost.get("notes", [])
        for note in notes:
            print(f"Cost note: {note}")

    steps = data.get("steps", [])
    if isinstance(steps, list):
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            step_cost_str = ""
            if isinstance(cost, dict) and cost.get("steps_cost"):
                step_costs = cost["steps_cost"]
                if idx - 1 < len(step_costs):
                    sc = step_costs[idx - 1]
                    step_cost_str = f", cost=${sc.get('step_cost_usd', 0.0):.5f}"
            print(
                f"{idx}. {step.get('title', '')} "
                f"(context={step.get('context_tokens', 0)}, "
                f"total={step.get('step_total_tokens', 0)}, "
                f"cumulative_ctx={step.get('cumulative_context_tokens', 0)}"
                f"{step_cost_str})"
            )

    print(
        f"Total tokens: {data.get('total_tokens', 0)} "
        f"| variance: {data.get('token_variance', 0.0)} "
        f"| std_dev: {data.get('token_std_dev', 0.0)}"
    )
    print(
        f"Min/avg/max per step: "
        f"{data.get('min_step_tokens', 0)} / "
        f"{data.get('avg_step_tokens', 0.0)} / "
        f"{data.get('max_step_tokens', 0)}"
    )
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    data = engine.pack(
        task=args.task,
        repo=args.repo,
        workspace=args.workspace,
        max_tokens=args.max_tokens,
        top_files=args.top_files,
        delta_from=args.delta,
    )

    base = args.out_prefix or "run"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")

    write_json(json_path, data)
    markdown = render_pack_markdown(data)

    policy_result: dict | None = None
    if args.strict:
        if args.policy:
            policy = load_policy(Path(args.policy))
        else:
            policy = default_strict_policy(max_estimated_input_tokens=int(data.get("max_tokens", 0) or 0))
        policy_result = engine.evaluate_policy(data, policy=policy)
        data["policy"] = policy_result
        write_json(json_path, data)
        markdown = f"{markdown}\n{render_policy_markdown(policy_result)}\n"
    md_path.write_text(markdown, encoding="utf-8")
    engine.record_history_artifacts(
        data,
        artifacts={
            "run_json": str(json_path.resolve()),
            "run_markdown": str(md_path.resolve()),
        },
    )

    budget = data["budget"]
    print(f"Wrote run JSON: {json_path}")
    print(f"Wrote run Markdown: {md_path}")
    print(
        "Budget: "
        f"input={budget['estimated_input_tokens']} tokens, "
        f"saved={budget['estimated_saved_tokens']} tokens, "
        f"risk={budget['quality_risk_estimate']}"
    )
    model_profile = data.get("model_profile", {})
    if isinstance(model_profile, dict) and model_profile:
        print(
            "Model profile: "
            f"selected={model_profile.get('selected_profile', '')} "
            f"resolved={model_profile.get('resolved_profile', '')} "
            f"context={model_profile.get('context_window', 0)} "
            f"compression={model_profile.get('recommended_compression_strategy', '')} "
            f"max_tokens={model_profile.get('effective_max_tokens', data.get('max_tokens', 0))}"
        )
        if model_profile.get("budget_clamped", False):
            print("Model profile note: max_tokens was clamped to fit the configured context window")
    delta = data.get("delta", {})
    if isinstance(delta, dict) and delta:
        delta_budget = delta.get("budget", {})
        if isinstance(delta_budget, dict):
            print(
                "Delta: "
                f"original={delta_budget.get('original_tokens', 0)} tokens, "
                f"delta={delta_budget.get('delta_tokens', 0)} tokens, "
                f"saved={delta_budget.get('tokens_saved', 0)} tokens"
            )
    estimator = data.get("token_estimator", {})
    if isinstance(estimator, dict):
        print(
            "Token estimator: "
            f"selected={estimator.get('selected_backend', 'heuristic')} "
            f"effective={estimator.get('effective_backend', 'heuristic')} "
            f"fallback={estimator.get('fallback_used', False)}"
        )
        reason = str(estimator.get("fallback_reason", "") or "")
        if reason:
            print(f"Token estimator note: {reason}")
    summarizer = data.get("summarizer", {})
    if isinstance(summarizer, dict):
        print(
            "Summarizer: "
            f"selected={summarizer.get('selected_backend', 'deterministic')} "
            f"effective={summarizer.get('effective_backend', 'deterministic')} "
            f"fallback={summarizer.get('fallback_used', False)}"
        )
        adapter = str(summarizer.get("external_adapter", "") or "")
        if adapter:
            print(f"Summarizer adapter: {adapter}")
        logs = summarizer.get("logs", [])
        if isinstance(logs, list):
            for item in logs:
                print(f"Summarizer log: {item}")
    if policy_result is not None:
        if bool(policy_result.get("passed", False)):
            print("Policy check: PASS")
        else:
            print("Policy check: FAIL")
            for violation in policy_result.get("violations", []):
                print(f"- {violation}")
            return 2
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine()
    run_path = Path(args.run_json)
    data = engine.profile(run_path)
    markdown = render_profile_markdown(data)

    tokens_before = int(data.get("tokens_before") or 0)
    tokens_saved = int(data.get("tokens_saved") or 0)
    savings_pct = float(data.get("savings_pct") or 0.0)

    _STAGE_LABELS = {
        "cache_reuse": "cache reuse",
        "symbol_extraction": "symbol extraction",
        "slicing": "slicing",
        "compression": "compression",
        "snippet": "snippet",
        "delta": "delta mode",
        "full": "full",
    }

    print("Token Savings Breakdown")
    print()
    by_stage = data.get("by_stage", {})
    if isinstance(by_stage, dict) and tokens_before > 0:
        for stage_key, stage_data in by_stage.items():
            if not isinstance(stage_data, dict):
                continue
            stage_saved = int(stage_data.get("tokens_saved") or 0)
            if stage_saved == 0:
                continue
            pct = round((stage_saved / tokens_before) * 100.0, 0)
            label = _STAGE_LABELS.get(stage_key, stage_key.replace("_", " "))
            print(f"{label}: -{int(pct)}%")
    print()
    print(f"total savings: -{savings_pct:.0f}%")

    prefix = args.out_prefix or run_path.with_suffix("").name + "-profile"
    json_path = Path(f"{prefix}.json")
    md_path = Path(f"{prefix}.md")
    write_json(json_path, data)
    md_path.write_text(markdown, encoding="utf-8")
    print(f"\nWrote profile JSON:     {json_path}")
    print(f"Wrote profile Markdown: {md_path}")
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine()
    run_path = Path(args.run_json)
    data = engine.pipeline_trace(run_path)

    prefix = args.out_prefix or run_path.with_suffix("").name + "-pipeline"
    json_path = Path(f"{prefix}.json")
    md_path = Path(f"{prefix}.md")

    write_json(json_path, data)
    markdown = render_pipeline_markdown(data)
    md_path.write_text(markdown, encoding="utf-8")

    final_tokens = int(data.get("final_tokens") or 0)
    total_saved = int(data.get("total_tokens_saved") or 0)
    total_pct = float(data.get("total_reduction_pct") or 0.0)

    print(f"Pipeline trace: {run_path}")
    print(f"Task: {data.get('task', '')}")
    print(f"Repo: {data.get('repo', '')}")
    print()
    col_w = (30, 7, 12, 12, 10, 10)
    header = (
        f"{'Stage':<{col_w[0]}} {'Files':>{col_w[1]}} "
        f"{'Tokens In':>{col_w[2]}} {'Tokens Out':>{col_w[3]}} "
        f"{'Saved':>{col_w[4]}} {'Reduction':>{col_w[5]}}"
    )
    print(header)
    print("-" * (sum(col_w) + 5))
    for stage in data.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        is_opt = bool(stage.get("is_optimisation", False))
        label = ("  \u00bb " if is_opt else "") + stage.get("label", stage.get("name", ""))
        files = int(stage.get("files_in") or 0)
        t_in = int(stage.get("tokens_in") or 0)
        t_out = int(stage.get("tokens_out") or 0)
        t_saved = int(stage.get("tokens_saved") or 0)
        pct = float(stage.get("reduction_pct") or 0.0)
        pct_str = f"{pct:.1f}%" if pct > 0 else "\u2014"
        saved_str = f"{t_saved:,}" if t_saved > 0 else "\u2014"
        print(
            f"{label:<{col_w[0]}} {files:>{col_w[1]},} "
            f"{t_in:>{col_w[2]},} {t_out:>{col_w[3]},} "
            f"{saved_str:>{col_w[4]}} {pct_str:>{col_w[5]}}"
        )
    print("-" * (sum(col_w) + 5))
    print(
        f"{'Final context':<{col_w[0]}} {'':{col_w[1]}} "
        f"{'':{col_w[2]}} {final_tokens:>{col_w[3]},} "
        f"{total_saved:>{col_w[4]},} {total_pct:>{col_w[5]-1}.1f}%"
    )
    print()
    print(f"Wrote pipeline JSON:     {json_path}")
    print(f"Wrote pipeline Markdown: {md_path}")
    return 0


def cmd_observe(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine()
    run_path = Path(args.run_json)
    if not run_path.exists():
        print(f"Error: run artifact not found: {run_path}")
        return 2

    base_dir = Path(args.base_dir) if getattr(args, "base_dir", None) else run_path.parent
    no_store = getattr(args, "no_store", False)
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "human")

    data = engine.observe(run_path, store=not no_store, base_dir=base_dir)
    markdown = render_observe_markdown(data)

    prefix = args.out_prefix or run_path.with_suffix("").name + "-observe"
    json_path = Path(f"{prefix}.json")
    md_path = Path(f"{prefix}.md")

    write_json(json_path, data)
    md_path.write_text(markdown, encoding="utf-8")

    if fmt == "json":
        print(_json_mod.dumps(data, indent=2, default=str))
    else:
        print(markdown)
        print(f"Wrote observe JSON:     {json_path}")
        print(f"Wrote observe Markdown: {md_path}")
        if not no_store:
            store_path = base_dir / ".contextbudget" / "observe-history.json"
            print(f"Metrics stored in:      {store_path}")

    if getattr(args, "export_history", False):
        from contextbudget.telemetry.store import export_observe_history_json
        hist = export_observe_history_json(base_dir=base_dir)
        hist_path = Path(f"{prefix}-history.json")
        write_json(hist_path, hist)
        if fmt != "json":
            print(f"Exported history JSON:  {hist_path}")

    return 0


def cmd_read_profiler(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine()
    run_path = Path(args.run_json)
    fmt = getattr(args, "format", "human")
    data = engine.read_profile(run_path)
    markdown = render_read_profile_markdown(data)

    prefix = args.out_prefix or run_path.with_suffix("").name + "-read-profile"
    json_path = Path(f"{prefix}.json")
    md_path = Path(f"{prefix}.md")
    write_json(json_path, data)
    md_path.write_text(markdown, encoding="utf-8")

    if fmt == "json":
        print(_json_mod.dumps(data, indent=2, default=str))
    else:
        print(markdown)
        print(f"Unique files read:    {data.get('unique_files_read', 0)}")
        print(f"Duplicate reads:      {data.get('duplicate_reads', 0)}")
        print(f"Unnecessary reads:    {data.get('unnecessary_reads', 0)}")
        print(f"High-cost reads:      {data.get('high_cost_reads', 0)}")
        print(f"Tokens wasted total:  {data.get('tokens_wasted_total', 0)}")
        print(f"Wrote read-profile JSON:     {json_path}")
        print(f"Wrote read-profile Markdown: {md_path}")
    return 0

def cmd_report(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine()
    run_path = Path(args.run_json)
    summary = engine.report(run_path)
    markdown = render_report_markdown(summary)

    if args.policy:
        policy = load_policy(Path(args.policy))
        policy_result = engine.evaluate_policy(run_path, policy=policy)
        summary["policy"] = policy_result
        markdown = f"{markdown}\n{render_policy_markdown(policy_result)}\n"
    else:
        policy_result = None

    print(markdown)

    out_path = Path(args.out) if args.out else run_path.with_suffix(".report.md")
    out_path.write_text(markdown, encoding="utf-8")
    print(f"Wrote summary Markdown: {out_path}")
    if policy_result is not None and not bool(policy_result.get("passed", False)):
        print("Policy check: FAIL")
        for violation in policy_result.get("violations", []):
            print(f"- {violation}")
        return 2
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine()
    old_path = Path(args.old_run_json)
    new_path = Path(args.new_run_json)
    diff_data = engine.diff(
        old_path,
        new_path,
        old_label=str(old_path),
        new_label=str(new_path),
    )
    markdown = render_diff_markdown(diff_data)
    print(markdown)

    base = args.out_prefix or f"{old_path.stem}-vs-{new_path.stem}.diff"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    write_json(json_path, diff_data)
    md_path.write_text(markdown, encoding="utf-8")

    print(f"Wrote diff JSON: {json_path}")
    print(f"Wrote diff Markdown: {md_path}")
    return 0


def cmd_pr_audit(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    audit_data = engine.pr_audit(
        repo=args.repo,
        base_ref=args.base,
        head_ref=args.head,
        config_path=args.config,
    )
    comment_markdown = render_pr_comment_markdown(audit_data)
    audit_data["comment_markdown"] = comment_markdown
    markdown = render_pr_audit_markdown(audit_data)

    base = args.out_prefix or "contextbudget-pr-audit"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    comment_path = Path(f"{base}.comment.md")
    write_json(json_path, audit_data)
    md_path.write_text(markdown, encoding="utf-8")
    comment_path.write_text(comment_markdown, encoding="utf-8")

    summary = audit_data.get("summary", {})
    print(f"Wrote PR audit JSON: {json_path}")
    print(f"Wrote PR audit Markdown: {md_path}")
    print(f"Wrote PR comment Markdown: {comment_path}")
    print(
        "Estimated token impact: "
        f"{float(summary.get('estimated_token_delta_pct', 0.0) or 0.0):+.1f}% "
        f"({int(summary.get('estimated_tokens_before', 0) or 0)} -> "
        f"{int(summary.get('estimated_tokens_after', 0) or 0)})"
    )
    causing_increase = audit_data.get("files_causing_increase", [])
    if isinstance(causing_increase, list) and causing_increase:
        print("Files causing increase:")
        for path in causing_increase[:10]:
            print(f"- {path}")

    token_delta = int(summary.get("estimated_token_delta", 0) or 0)
    token_delta_pct = float(summary.get("estimated_token_delta_pct", 0.0) or 0.0)
    if args.max_token_increase is not None and token_delta > args.max_token_increase:
        print(
            "PR audit gate: FAIL "
            f"(token delta {token_delta} exceeds limit {args.max_token_increase})"
        )
        return 2
    if args.max_token_increase_pct is not None and token_delta_pct > args.max_token_increase_pct:
        print(
            "PR audit gate: FAIL "
            f"(token impact {token_delta_pct:.1f}% exceeds limit {args.max_token_increase_pct:.1f}%)"
        )
        return 2
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    benchmark_data = engine.benchmark(
        task=args.task,
        repo=args.repo,
        workspace=args.workspace,
        max_tokens=args.max_tokens,
        top_files=args.top_files,
    )
    markdown = render_benchmark_markdown(benchmark_data)

    base = args.out_prefix or f"contextbudget-benchmark-{_base_name(args.task)}"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    write_json(json_path, benchmark_data)
    md_path.write_text(markdown, encoding="utf-8")

    print("Benchmark summary:")
    model_profile = benchmark_data.get("model_profile", {})
    if isinstance(model_profile, dict) and model_profile:
        print(
            "Model profile: "
            f"selected={model_profile.get('selected_profile', '')} "
            f"resolved={model_profile.get('resolved_profile', '')} "
            f"context={model_profile.get('context_window', 0)} "
            f"compression={model_profile.get('recommended_compression_strategy', '')} "
            f"max_tokens={model_profile.get('effective_max_tokens', benchmark_data.get('max_tokens', 0))}"
        )
    estimator = benchmark_data.get("token_estimator", {})
    if isinstance(estimator, dict):
        print(
            "Estimator backend: "
            f"selected={estimator.get('selected_backend', 'heuristic')} "
            f"effective={estimator.get('effective_backend', 'heuristic')} "
            f"fallback={estimator.get('fallback_used', False)}"
        )
        reason = str(estimator.get("fallback_reason", "") or "")
        if reason:
            print(f"Estimator note: {reason}")
    for strategy in benchmark_data.get("strategies", []):
        print(
            f"- {strategy.get('strategy')}: "
            f"input={strategy.get('estimated_input_tokens')} "
            f"saved={strategy.get('estimated_saved_tokens')} "
            f"files={len(strategy.get('files_included', []))} "
            f"risk={strategy.get('quality_risk_estimate')} "
            f"runtime_ms={strategy.get('runtime_ms')}"
        )
    print(f"Wrote benchmark JSON: {json_path}")
    print(f"Wrote benchmark Markdown: {md_path}")
    return 0


def cmd_dataset(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "human")
    runs = getattr(args, "runs", None) or []
    if runs:
        data = engine.dataset_from_runs(runs)
    else:
        tasks_toml = getattr(args, "tasks_toml", None) or ""
        if not tasks_toml:
            print("error: tasks_toml is required when --runs is not provided")
            return 2
        data = engine.dataset(
            tasks_toml=tasks_toml,
            repo=args.repo,
            max_tokens=args.max_tokens,
            top_files=args.top_files,
        )

    base = args.out_prefix or "contextbudget-dataset"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    write_json(json_path, data)
    md_path.write_text(render_dataset_markdown(data), encoding="utf-8")

    if fmt == "json":
        print(_json_mod.dumps(data, indent=2, default=str))
    else:
        agg = data.get("aggregate", {})
        print(f"Tasks: {data.get('task_count', 0)}")
        print(
            f"Avg baseline tokens: {agg.get('avg_baseline_tokens', 0)}  "
            f"Avg optimized tokens: {agg.get('avg_optimized_tokens', 0)}  "
            f"Avg reduction: {agg.get('avg_reduction_pct', 0):.1f}%"
        )
        for idx, entry in enumerate(data.get("entries", []), start=1):
            label = entry.get("task_name") or entry.get("task", "")
            print(
                f"  {idx}. {label}  "
                f"baseline={entry.get('baseline_tokens', 0)}  "
                f"optimized={entry.get('optimized_tokens', 0)}  "
                f"reduction={entry.get('reduction_pct', 0):.1f}%"
            )
        print(f"Wrote dataset JSON: {json_path}")
        print(f"Wrote dataset Markdown: {md_path}")
    return 0


def cmd_build_dataset(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    data = engine.build_dataset(
        repo=args.repo,
        tasks_toml=args.tasks_toml or None,
        use_builtin=not args.no_builtin,
        max_tokens=args.max_tokens,
        top_files=args.top_files,
    )

    base = args.out_prefix or "contextbudget-context-dataset"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    write_json(json_path, data)
    md_path.write_text(render_context_dataset_markdown(data), encoding="utf-8")

    agg = data.get("aggregate", {})
    builtin_count = data.get("builtin_task_count", 0)
    extra_count = data.get("extra_task_count", 0)
    print(f"Tasks: {data.get('task_count', 0)} ({builtin_count} built-in, {extra_count} custom)")
    print(
        f"Avg baseline tokens: {agg.get('avg_baseline_tokens', 0)}  "
        f"Avg optimized tokens: {agg.get('avg_optimized_tokens', 0)}  "
        f"Avg reduction: {agg.get('avg_reduction_pct', 0):.1f}%"
    )
    for idx, entry in enumerate(data.get("entries", []), start=1):
        label = entry.get("task_name") or entry.get("task", "")
        print(
            f"  {idx}. {label}  "
            f"baseline={entry.get('baseline_tokens', 0)}  "
            f"optimized={entry.get('optimized_tokens', 0)}  "
            f"reduction={entry.get('reduction_pct', 0):.1f}%"
        )
    print(f"Wrote context dataset JSON: {json_path}")
    print(f"Wrote context dataset Markdown: {md_path}")
    return 0


def _print_heatmap_section(title: str, items: list[dict], *, runs_analyzed: int) -> None:
    print(title)
    if not items:
        print("- None")
        return
    for item in items:
        rate = float(item.get("inclusion_rate", 0.0) or 0.0) * 100.0
        print(
            "- "
            f"{item.get('path', '')}: "
            f"compressed={item.get('total_compressed_tokens', 0)} "
            f"original={item.get('total_original_tokens', 0)} "
            f"saved={item.get('total_saved_tokens', 0)} "
            f"included={item.get('inclusion_count', 0)}/{runs_analyzed} "
            f"rate={rate:.1f}%"
        )


def cmd_heatmap(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        print("--limit must be greater than 0")
        return 2

    engine = ContextBudgetEngine()
    try:
        heatmap_data = engine.heatmap(history=args.history, limit=args.limit)
    except ValueError as exc:
        print(str(exc))
        return 2

    markdown = render_heatmap_markdown(heatmap_data)
    base = args.out_prefix or "contextbudget-heatmap"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    write_json(json_path, heatmap_data)
    md_path.write_text(markdown, encoding="utf-8")

    runs_analyzed = int(heatmap_data.get("runs_analyzed", 0) or 0)
    print(f"Wrote heatmap JSON: {json_path}")
    print(f"Wrote heatmap Markdown: {md_path}")
    print(f"Runs analyzed: {runs_analyzed}")
    print(f"Unique files: {int(heatmap_data.get('unique_files', 0) or 0)}")
    print(f"Unique directories: {int(heatmap_data.get('unique_directories', 0) or 0)}")
    skipped = heatmap_data.get("skipped_artifacts", [])
    if isinstance(skipped, list) and skipped:
        print(f"Skipped artifacts: {len(skipped)}")
    _print_heatmap_section(
        "Top token-heavy files:",
        heatmap_data.get("top_token_heavy_files", []),
        runs_analyzed=runs_analyzed,
    )
    _print_heatmap_section(
        "Top token-heavy directories:",
        heatmap_data.get("top_token_heavy_directories", []),
        runs_analyzed=runs_analyzed,
    )
    _print_heatmap_section(
        "Most frequently included files:",
        heatmap_data.get("most_frequently_included_files", []),
        runs_analyzed=runs_analyzed,
    )
    _print_heatmap_section(
        "Largest token savings opportunities:",
        heatmap_data.get("largest_token_savings_opportunities", []),
        runs_analyzed=runs_analyzed,
    )
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    repo_path = normalize_repo(args.repo)
    engine = ContextBudgetEngine()
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "human")
    explicit_runs = getattr(args, "runs", None) or []
    try:
        drift_data = engine.drift(
            repo=repo_path,
            task=args.task or None,
            window=args.window,
            threshold_pct=args.threshold,
            runs=explicit_runs if explicit_runs else None,
        )
    except ValueError as exc:
        print(str(exc))
        return 2

    markdown = render_drift_markdown(drift_data)
    base = args.out_prefix or "contextbudget-drift"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    write_json(json_path, drift_data)
    md_path.write_text(markdown, encoding="utf-8")

    drift = drift_data.get("drift", {})
    alert = bool(drift.get("alert", False))

    if fmt == "json":
        print(_json_mod.dumps(drift_data, indent=2, default=str))
    else:
        verdict = str(drift.get("verdict", "none") or "none")
        token_drift_pct = float(drift.get("token_drift_pct", 0.0) or 0.0)
        file_drift_pct = float(drift.get("file_drift_pct", 0.0) or 0.0)
        dep_depth_drift_pct = float(drift.get("dep_depth_drift_pct", 0.0) or 0.0)
        entries_analyzed = int(drift_data.get("entries_analyzed", 0) or 0)

        if alert:
            print(f"context drift detected [{verdict.upper()}]")
        else:
            print("no significant context drift detected")
        print(f"  Entries analyzed  : {entries_analyzed}")
        direction = "increased" if token_drift_pct >= 0 else "decreased"
        print(f"  token usage {direction} by {abs(token_drift_pct):.1f}%")
        print(f"  File drift        : {file_drift_pct:+.1f}%")
        print(f"  Dependency depth  : {dep_depth_drift_pct:+.1f}%")
        contributors = drift_data.get("top_contributors", [])
        if isinstance(contributors, list) and contributors:
            print(f"  files contributing most to drift ({len(contributors)} file(s)):")
            for c in contributors[:5]:
                if isinstance(c, dict):
                    print(f"    {c.get('status', ''):10s} {c.get('file', '')}")
        print(f"Wrote drift JSON: {json_path}")
        print(f"Wrote drift Markdown: {md_path}")
    return 2 if alert else 0


def cmd_enforce(args: argparse.Namespace) -> int:
    policy_path = Path(args.policy_toml)
    run_path = Path(args.run_json)

    if not policy_path.exists():
        print(f"Policy file not found: {policy_path}")
        return 2
    if not run_path.exists():
        print(f"Run artifact not found: {run_path}")
        return 2

    policy = load_policy(policy_path)
    engine = ContextBudgetEngine()
    policy_result = engine.evaluate_policy(run_path, policy=policy)

    if bool(policy_result.get("passed", False)):
        print(f"Policy check: PASS ({run_path})")
        checks = policy_result.get("checks", {})
        for name, detail in checks.items():
            print(f"  {name}: actual={detail.get('actual')} limit={detail.get('limit')} pass={detail.get('passed')}")
        return 0
    else:
        print(f"Policy check: FAIL ({run_path})")
        for violation in policy_result.get("violations", []):
            print(f"  - {violation}")
        checks = policy_result.get("checks", {})
        for name, detail in checks.items():
            status = "pass" if detail.get("passed") else "FAIL"
            print(f"  {name}: actual={detail.get('actual')} limit={detail.get('limit')} [{status}]")
        return 2


def cmd_watch(args: argparse.Namespace) -> int:
    repo_path = normalize_repo(args.repo)
    config_path = _resolve_config_path(args.config)
    poll_interval = float(args.poll_interval)
    if poll_interval <= 0:
        print("--poll-interval must be greater than 0")
        return 2

    print(f"Watching repository: {repo_path}")
    print(f"Polling interval: {poll_interval:.2f}s")

    def refresh_once() -> tuple[ContextBudgetConfig, ScanRefreshResult]:
        cfg = load_config(repo_path, config_path=config_path)
        result = run_scan_refresh_stage(repo_path, cfg)
        return cfg, result

    _, initial = refresh_once()
    print(f"Scan index: {initial.index_path}")
    print(_render_scan_summary("Initial scan", repo_path, initial.summary))
    initial_changes = _render_scan_change_paths(initial.summary)
    if initial_changes:
        print(initial_changes)
    if args.once:
        return 0

    try:
        while True:
            time.sleep(poll_interval)
            _, result = refresh_once()
            summary = result.summary
            if summary.added_count or summary.updated_count or summary.removed_count:
                print(_render_scan_summary("Scan change", repo_path, summary))
                change_paths = _render_scan_change_paths(summary)
                if change_paths:
                    print(change_paths)
    except KeyboardInterrupt:
        print("Stopped watching.")
    return 0


def cmd_advise(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "human")
    data = engine.advise(
        repo=args.repo,
        history=args.history or None,
        large_file_tokens=args.large_file_tokens,
        high_fanin=args.high_fanin,
        high_fanout=args.high_fanout,
        high_frequency_rate=args.high_frequency_rate,
        top_suggestions=args.top,
    )

    base = args.out_prefix or "contextbudget-advise"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    write_json(json_path, data)
    md_path.write_text(render_advise_markdown(data), encoding="utf-8")

    if fmt == "json":
        print(_json_mod.dumps(data, indent=2, default=str))
    else:
        summary = data.get("summary", {})
        suggestions = data.get("suggestions", [])
        print(f"Wrote advise JSON: {json_path}")
        print(f"Wrote advise Markdown: {md_path}")
        print(
            f"Suggestions: {summary.get('total_suggestions', 0)} total, "
            f"{summary.get('split_file', 0)} split_file, "
            f"{summary.get('extract_module', 0)} extract_module, "
            f"{summary.get('reduce_dependencies', 0)} reduce_dependencies"
        )
        for idx, item in enumerate(suggestions[:10], start=1):
            print(
                f"{idx}. [{item.get('suggestion', '')}] {item.get('path', '')} "
                f"(impact={item.get('estimated_token_impact', 0)})"
            )
        if len(suggestions) > 10:
            print(f"... and {len(suggestions) - 10} more. See {md_path} for full report.")
    return 0


def cmd_visualize(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    history = args.history or []
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "human")

    graph_data = engine.visualize(
        repo=args.repo,
        history=history or None,
    )

    base = args.out_prefix or "contextbudget-graph"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")
    write_json(json_path, graph_data)
    md_path.write_text(render_visualize_markdown(graph_data), encoding="utf-8")

    if fmt == "json":
        print(_json_mod.dumps(graph_data, indent=2, default=str))
    else:
        print(f"Wrote graph JSON:     {json_path}")
        print(f"Wrote graph Markdown: {md_path}")
        stats = graph_data.get("stats", {})
        print(
            f"Nodes: {stats.get('total_nodes', 0)}  "
            f"Edges: {stats.get('total_edges', 0)}  "
            f"Total tokens: {stats.get('total_estimated_tokens', 0):,}"
        )
        top_token = stats.get("top_token_files", [])
        if top_token:
            print("Top token-heavy files:")
            for path in top_token:
                print(f"  {path}")
        most_imported = stats.get("most_imported_files", [])
        if most_imported:
            print("Most imported files:")
            for path in most_imported:
                print(f"  {path}")

    if args.html:
        html_str = engine.visualize_html(
            repo=args.repo,
            history=history or None,
        )
        html_path = Path(f"{base}.html")
        html_path.write_text(html_str, encoding="utf-8")
        if fmt != "json":
            print(f"Wrote graph HTML: {html_path}")

    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from contextbudget.core.dashboard import build_dashboard_data, serve_dashboard

    scan_paths = [Path(p) for p in args.paths] if args.paths else [Path(".")]
    fmt = getattr(args, "format", "human")
    export = getattr(args, "export", False)
    data = build_dashboard_data(scan_paths)

    if fmt == "json" or export:
        if export:
            export_path = Path(args.out_prefix or "contextbudget-dashboard") .with_suffix(".json")
            write_json(export_path, data)
            if fmt != "json":
                print(f"Wrote dashboard JSON: {export_path}")
        if fmt == "json":
            print(_json_mod.dumps(data, indent=2, default=str))
        return 0

    s = data["summary"]
    print(
        f"Runs: {s['total_runs']}  "
        f"Pack: {s['pack_runs']}  "
        f"Sim: {s['sim_runs']}  "
        f"Bench: {s['benchmark_runs']}"
    )
    print(
        f"Input tokens: {s['total_input_tokens']:,}  "
        f"Saved: {s['total_saved_tokens']:,}  "
        f"Rate: {s['savings_rate']:.1%}"
    )
    serve_dashboard(data, port=args.port, no_open=args.no_open)
    return 0


def cmd_control_plane(args: argparse.Namespace) -> int:
    from contextbudget.control_plane.server import make_server

    server = make_server(
        db_path=args.db,
        host=args.host,
        port=args.port,
    )
    server.serve()
    return 0


def cmd_gateway(args: argparse.Namespace) -> int:
    from contextbudget.gateway import GatewayConfig, GatewayServer

    config = GatewayConfig(
        host=args.host,
        port=args.port,
        max_tokens=args.max_tokens,
        max_files=args.max_files,
        config_path=args.config or None,
        telemetry_enabled=args.telemetry,
        log_requests=not args.no_log_requests,
        api_key=getattr(args, "api_key", None),
    )
    GatewayServer(config).start(block=True)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Auto-detect repository language(s) and generate config files."""
    import collections
    import re

    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        print(f"Repository path does not exist: {repo_path}")
        return 2

    config_path = repo_path / "contextbudget.toml"
    policy_path = repo_path / "policy.toml"

    if config_path.exists() and not args.force:
        print(f"contextbudget.toml already exists. Use --force to overwrite.")
        return 1

    # ── Detect languages ──────────────────────────────────────────────────────
    ext_counts: dict[str, int] = collections.Counter()
    code_extensions = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".cpp", ".c", ".cs"}
    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".contextbudget"}

    for item in repo_path.rglob("*"):
        if any(part in ignore_dirs for part in item.parts):
            continue
        if item.is_file() and item.suffix in code_extensions:
            ext_counts[item.suffix] += 1

    dominant_lang = ""
    lang_hints = {}
    if ext_counts:
        dominant_ext = max(ext_counts, key=lambda e: ext_counts[e])
        lang_map = {
            ".py": "python", ".ts": "typescript", ".tsx": "typescript",
            ".js": "javascript", ".jsx": "javascript", ".go": "go",
            ".rs": "rust", ".java": "java", ".rb": "ruby",
            ".cpp": "cpp", ".c": "c", ".cs": "csharp",
        }
        dominant_lang = lang_map.get(dominant_ext, "")
        lang_hints["dominant"] = dominant_lang
        lang_hints["file_counts"] = dict(sorted(ext_counts.items(), key=lambda x: -x[1])[:5])

    total_files = sum(ext_counts.values())

    # ── Estimate savings ──────────────────────────────────────────────────────
    # Rough heuristic: 40-60% token savings is typical for well-structured repos
    estimated_savings_pct = 55 if total_files > 50 else (40 if total_files > 10 else 25)
    default_max_tokens = 64000
    estimated_baseline = total_files * 300  # ~300 tokens avg per file
    estimated_saved = int(estimated_baseline * estimated_savings_pct / 100)

    # ── Generate contextbudget.toml ───────────────────────────────────────────
    lang_comment = f"# Detected dominant language: {dominant_lang}\n" if dominant_lang else ""
    config_content = f"""# ContextBudget configuration — generated by `contextbudget init`
# See: https://github.com/contextbudget/contextbudget/docs/configuration.md
{lang_comment}
[budget]
max_tokens = {default_max_tokens}
# top_files = 100

[scan]
# ignore_globs = ["tests/**", "docs/**"]

[score]
enable_import_graph_signals = true
history_selected_file_boost = 1.25

[compression]
full_file_threshold_tokens = 600
symbol_extraction_enabled = true

[cache]
backend = "local_file"
summary_cache_enabled = true
run_history_enabled = true
# Shared Redis cache (uncomment for team use):
# backend = "redis"
# redis_url = "redis://localhost:6379/0"
# redis_namespace = "{repo_path.name}"
# redis_ttl_seconds = 86400

[telemetry]
enabled = false
# sink = "jsonl"
"""

    policy_content = f"""# ContextBudget policy — generated by `contextbudget init`
# Enforced via: contextbudget policy run.json
# CI: contextbudget enforce --policy policy.toml run.json

[policy]
max_estimated_input_tokens = {default_max_tokens}
# max_files_included = 100
# max_quality_risk = "medium"
# min_savings_percentage = 10.0
"""

    config_path.write_text(config_content, encoding="utf-8")
    policy_path.write_text(policy_content, encoding="utf-8")

    # ── Print summary ─────────────────────────────────────────────────────────
    print(f"Initialized ContextBudget for: {repo_path}")
    print(f"  Created: contextbudget.toml")
    print(f"  Created: policy.toml")
    if dominant_lang:
        lang_breakdown = ", ".join(f"{ext}={n}" for ext, n in list(lang_hints['file_counts'].items())[:3])
        print(f"  Detected: {dominant_lang} ({lang_breakdown})")
    print(f"  Code files found: {total_files}")
    print(f"  Estimated savings: ~{estimated_savings_pct}%  (~{estimated_saved:,} tokens saved per run)")
    print()
    print("Next steps:")
    print("  contextbudget pack 'describe your task' --repo .")
    print("  contextbudget plan 'describe your task' --repo .")
    return 0


def cmd_prepare_context(args: argparse.Namespace) -> int:
    middleware = ContextBudgetMiddleware(config_path=_resolve_config_path(args.config))
    result = middleware.prepare_context(
        args.task,
        repo=args.repo,
        workspace=args.workspace if hasattr(args, "workspace") else None,
        max_tokens=args.max_tokens,
        top_files=args.top_files,
        delta_from=args.delta,
        config_path=_resolve_config_path(args.config),
    )

    policy_result: dict | None = None
    if args.strict:
        policy_path = Path(args.policy) if args.policy else None
        if policy_path:
            policy = load_policy(policy_path)
        else:
            max_tokens_effective = int(result.run_artifact.get("max_tokens", 0) or 0)
            policy = default_strict_policy(max_estimated_input_tokens=max_tokens_effective)
        try:
            result = middleware.enforce_budget(result, policy=policy, strict=args.strict)
        except BudgetPolicyViolationError as err:
            result.policy_result = err.policy_result
        policy_result = result.policy_result

    base = args.out_prefix or "prepare-context-run"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")

    record = result.as_record()
    write_json(json_path, record)
    md_path.write_text(render_prepare_context_markdown(record), encoding="utf-8")

    print(f"Wrote context JSON: {json_path}")
    print(f"Wrote context Markdown: {md_path}")

    meta = result.metadata
    print(
        f"Context: input={meta.get('estimated_input_tokens', 0)} tokens, "
        f"saved={meta.get('estimated_saved_tokens', 0)} tokens, "
        f"files={meta.get('files_included_count', 0)}, "
        f"risk={meta.get('quality_risk_estimate', 'unknown')}"
    )
    if meta.get("delta_enabled"):
        print(
            f"Delta: original={meta.get('original_input_tokens', 0)} tokens, "
            f"effective={meta.get('estimated_input_tokens', 0)} tokens"
        )

    if policy_result is not None:
        if bool(policy_result.get("passed", False)):
            print("Policy check: PASS")
        else:
            print("Policy check: FAIL")
            for violation in policy_result.get("violations", []):
                print(f"- {violation}")
            return 2

    return 0


def cmd_cost_analysis(args: argparse.Namespace) -> int:
    from contextbudget.core.cost_analysis import compute_cost_analysis, list_known_models, load_run_data

    fmt = getattr(args, "format", "human")

    if getattr(args, "list_models", False):
        rows = list_known_models()
        if fmt == "json":
            print(_json_mod.dumps(rows, indent=2, default=str))
        else:
            print(f"{'Model':<36} {'Provider':<12} {'Input $/MTok':>14}")
            print("-" * 66)
            for row in rows:
                print(
                    f"{row['model']:<36} {row['provider']:<12} "
                    f"{row['input_per_1m_usd']:>14.4f}"
                )
        return 0

    if not args.run_json:
        print("error: run_json is required (or use --list-models)")
        return 2

    run_path = Path(args.run_json)
    if not run_path.exists():
        print(f"Run artifact not found: {run_path}")
        return 2

    try:
        run_data = load_run_data(run_path)
    except Exception as exc:
        print(f"Failed to read run artifact: {exc}")
        return 2

    result = compute_cost_analysis(
        run_data,
        model=args.model,
        price_per_1m_input=args.price_input,
    )

    from contextbudget.core.render import render_cost_analysis_markdown, write_json

    # Derive output paths: default to <run_stem>-cost-analysis.{json,md}
    run_stem = run_path.with_suffix("").name
    json_out = Path(args.out) if args.out else Path(f"{run_stem}-cost-analysis.json")
    md_out = json_out.with_suffix(".md")

    write_json(json_out, result)
    markdown = render_cost_analysis_markdown(result)
    md_out.write_text(markdown, encoding="utf-8")

    if fmt == "json":
        print(_json_mod.dumps(result, indent=2, default=str))
        return 0

    model_label = result["model"]
    provider = result["provider"]
    provider_str = f" ({provider})" if provider else ""
    print(f"Model: {model_label}{provider_str}  |  input ${result['input_per_1m_usd']:.4f}/MTok")
    print()
    print(f"Baseline cost:  ${result['baseline_cost_usd']:.2f}  ({result['baseline_tokens']:,} tokens)")
    print(f"Optimized cost: ${result['optimized_cost_usd']:.2f}  ({result['optimized_tokens']:,} tokens)")
    print(f"Savings:        ${result['saved_cost_usd']:.2f}  ({result['saved_tokens']:,} tokens, {result['savings_pct']:.1f}%)")

    per_file = result.get("per_file", [])
    if per_file:
        print()
        print(f"{'File':<40} {'Saved tokens':>13} {'Saved $':>10}")
        print("-" * 65)
        for row in per_file:
            path_label = row["path"]
            if len(path_label) > 39:
                path_label = "..." + path_label[-36:]
            print(f"{path_label:<40} {row['saved_tokens']:>13,} {row['saved_cost_usd']:>10.6f}")

    for note in result.get("notes", []):
        print(f"Note: {note}")

    print()
    print(f"Wrote cost analysis JSON: {json_out}")
    print(f"Wrote cost analysis Markdown: {md_out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextbudget",
        description=(
            "Reduce token usage by planning and packing repository context. "
            "Supports contextbudget.toml sections: [scan], [budget], [score], [compression], "
            "[summarization], [plugins], [cache], [telemetry]."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Rank relevant files for a natural language task")
    plan.add_argument("task", help="Task description")
    plan.add_argument("--repo", default=".", help="Repository path")
    plan.add_argument("--workspace", help="Workspace TOML describing multiple local repositories/packages.")
    plan.add_argument("--out-prefix", help="Output file prefix for JSON/Markdown")
    plan.add_argument(
        "--top-files",
        type=int,
        default=None,
        help="Top ranked files to include in plan output (overrides [budget].top_files).",
    )
    plan.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    plan.set_defaults(func=cmd_plan)

    plan_agent = sub.add_parser("plan-agent", help="Plan context usage across a multi-step agent workflow")
    plan_agent.add_argument("task", help="Task description")
    plan_agent.add_argument("--repo", default=".", help="Repository path")
    plan_agent.add_argument("--workspace", help="Workspace TOML describing multiple local repositories/packages.")
    plan_agent.add_argument("--out-prefix", help="Output file prefix for JSON/Markdown")
    plan_agent.add_argument(
        "--top-files",
        type=int,
        default=None,
        help="Max files assigned per step from each ranking pass (overrides [budget].top_files).",
    )
    plan_agent.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    plan_agent.set_defaults(func=cmd_plan_agent)

    simulate_agent = sub.add_parser(
        "simulate-agent",
        help="Estimate token costs and USD spend for a multi-step agent workflow before execution",
    )
    simulate_agent.add_argument(
        "task",
        nargs="?",
        default="",
        help="Task description (may be omitted when --run-artifact is provided).",
    )
    simulate_agent.add_argument("--repo", default=".", help="Repository path")
    simulate_agent.add_argument("--workspace", help="Workspace TOML describing multiple local repositories/packages.")
    simulate_agent.add_argument("--out-prefix", help="Output file prefix for JSON/Markdown")
    simulate_agent.add_argument(
        "--top-files",
        type=int,
        default=None,
        help="Max files considered per workflow step (overrides [budget].top_files).",
    )
    simulate_agent.add_argument(
        "--prompt-overhead",
        type=int,
        default=800,
        help="Estimated prompt overhead tokens per step (system + user prompt, default: 800).",
    )
    simulate_agent.add_argument(
        "--output-tokens",
        type=int,
        default=600,
        help="Estimated model output tokens per step (default: 600).",
    )
    simulate_agent.add_argument(
        "--context-mode",
        default="isolated",
        choices=["isolated", "rolling", "full"],
        help=(
            "Context accumulation mode: "
            "isolated=each step is independent, "
            "rolling=two-step sliding window, "
            "full=context grows across all steps (default: isolated)."
        ),
    )
    simulate_agent.add_argument(
        "--model",
        default="gpt-4o",
        help=(
            "Model name used for cost estimation (default: gpt-4o). "
            "Supports Claude, GPT-4o, Gemini, Mistral, and others. "
            "Run `contextbudget simulate-agent --list-models` to see all known models."
        ),
    )
    simulate_agent.add_argument(
        "--price-input",
        dest="price_input",
        type=float,
        default=None,
        help="Custom input token price in USD per 1 000 000 tokens (overrides built-in model pricing).",
    )
    simulate_agent.add_argument(
        "--price-output",
        dest="price_output",
        type=float,
        default=None,
        help="Custom output token price in USD per 1 000 000 tokens (overrides built-in model pricing).",
    )
    simulate_agent.add_argument(
        "--list-models",
        action="store_true",
        default=False,
        help="Print all models in the built-in pricing table and exit.",
    )
    simulate_agent.add_argument(
        "--run-artifact",
        dest="run_artifact",
        default=None,
        help=(
            "Path to an existing pack or plan run artifact JSON. "
            "When provided, task and repo are read from the artifact if not given explicitly."
        ),
    )
    simulate_agent.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    simulate_agent.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) for readable summary, json to print raw JSON to stdout.",
    )
    simulate_agent.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print raw JSON to stdout (shorthand for --format json).",
    )
    simulate_agent.set_defaults(func=cmd_simulate_agent)

    pack = sub.add_parser("pack", help="Build compressed context under token budget")
    pack.add_argument("task", help="Task description")
    pack.add_argument("--repo", default=".", help="Repository path")
    pack.add_argument("--workspace", help="Workspace TOML describing multiple local repositories/packages.")
    pack.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Token budget override (takes precedence over [budget].max_tokens).",
    )
    pack.add_argument(
        "--top-files",
        type=int,
        default=None,
        help="Max files considered during packing (overrides [budget].top_files).",
    )
    pack.add_argument(
        "--delta",
        help="Optional previous run JSON used to emit an incremental delta context package.",
    )
    pack.add_argument("--out-prefix", help="Output file prefix for JSON/Markdown", default="run")
    pack.add_argument(
        "--strict",
        action="store_true",
        help="Enable strict policy enforcement (non-zero exit on violations).",
    )
    pack.add_argument(
        "--policy",
        help="Optional policy TOML for strict checks (default strict checks only max input tokens).",
    )
    pack.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    pack.set_defaults(func=cmd_pack)

    profile = sub.add_parser("profile", help="Show token savings breakdown for a pack run")
    profile.add_argument("run_json", help="Path to run JSON produced by pack")
    profile.add_argument("--out-prefix", help="Output file prefix for profile JSON/Markdown")
    profile.set_defaults(func=cmd_profile)

    pipeline_cmd = sub.add_parser(
        "pipeline",
        help="Show full context optimization pipeline trace for a pack run",
    )
    pipeline_cmd.add_argument("run_json", help="Path to run JSON produced by pack")
    pipeline_cmd.add_argument("--out-prefix", help="Output file prefix for pipeline JSON/Markdown")
    pipeline_cmd.set_defaults(func=cmd_pipeline)

    read_profiler = sub.add_parser(
        "read-profiler",
        help="Detect duplicate and unnecessary file reads in a pack run and quantify wasted tokens",
    )
    read_profiler.add_argument("run_json", help="Path to run JSON artifact produced by pack")
    read_profiler.add_argument(
        "--out-prefix", help="Output file prefix for read-profile JSON/Markdown"
    )
    read_profiler.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) for readable report, json to print raw JSON to stdout.",
    )
    read_profiler.set_defaults(func=cmd_read_profiler)

    report = sub.add_parser("report", help="Read a run JSON and produce a summary report")
    report.add_argument("run_json", help="Path to run JSON produced by pack")
    report.add_argument("--out", help="Path for markdown summary output")
    report.add_argument("--policy", help="Optional policy TOML to enforce strict budget checks.")
    report.set_defaults(func=cmd_report)

    diff = sub.add_parser("diff", help="Compare two run JSON artifacts")
    diff.add_argument("old_run_json", help="Path to older run JSON")
    diff.add_argument("new_run_json", help="Path to newer run JSON")
    diff.add_argument("--out-prefix", help="Output prefix for diff JSON/Markdown")
    diff.set_defaults(func=cmd_diff)

    pr_audit = sub.add_parser("pr-audit", help="Analyze pull-request diffs for context growth")
    pr_audit.add_argument("--repo", default=".", help="Repository path")
    pr_audit.add_argument("--base", help="Base git ref or commit SHA (defaults from CI env or HEAD~1).")
    pr_audit.add_argument("--head", help="Head git ref or commit SHA (default: HEAD or CI SHA).")
    pr_audit.add_argument("--config", help="Optional path to config TOML (default: <repo>/contextbudget.toml).")
    pr_audit.add_argument("--out-prefix", help="Output prefix for PR audit JSON/Markdown/comment files")
    pr_audit.add_argument(
        "--max-token-increase",
        type=int,
        default=None,
        help="Fail with non-zero exit if estimated token delta exceeds this absolute limit.",
    )
    pr_audit.add_argument(
        "--max-token-increase-pct",
        type=float,
        default=None,
        help="Fail with non-zero exit if estimated token impact exceeds this percentage.",
    )
    pr_audit.set_defaults(func=cmd_pr_audit)

    benchmark = sub.add_parser("benchmark", help="Compare context packing strategies")
    benchmark.add_argument("task", help="Task description")
    benchmark.add_argument("--repo", default=".", help="Repository path")
    benchmark.add_argument("--workspace", help="Workspace TOML describing multiple local repositories/packages.")
    benchmark.add_argument("--max-tokens", type=int, default=None, help="Token budget override for packed strategies.")
    benchmark.add_argument("--top-files", type=int, default=None, help="Top files override for ranking-based strategies.")
    benchmark.add_argument("--config", help="Optional path to config TOML (default: <repo>/contextbudget.toml).")
    benchmark.add_argument("--out-prefix", help="Output file prefix for benchmark JSON/Markdown")
    benchmark.set_defaults(func=cmd_benchmark)

    dataset = sub.add_parser(
        "dataset",
        help=(
            "Build a reproducible benchmark dataset from a TOML task list and export token reduction metrics. "
            "Accepts existing run artifacts via --runs to build a dataset without re-running benchmarks."
        ),
    )
    dataset.add_argument(
        "tasks_toml",
        nargs="?",
        default="",
        help="Path to TOML file containing [[tasks]] entries (omit when using --runs).",
    )
    dataset.add_argument("--repo", default=".", help="Repository path to benchmark against")
    dataset.add_argument("--max-tokens", type=int, default=None, help="Token budget forwarded to each benchmark run")
    dataset.add_argument("--top-files", type=int, default=None, help="Top-files limit forwarded to each benchmark run")
    dataset.add_argument("--config", help="Optional path to config TOML (default: <repo>/contextbudget.toml)")
    dataset.add_argument("--out-prefix", default="contextbudget-dataset", help="Output file prefix for dataset JSON/Markdown")
    dataset.add_argument(
        "--runs",
        nargs="+",
        default=None,
        metavar="RUN_JSON",
        help=(
            "One or more existing pack or benchmark run artifact JSON files. "
            "When provided, dataset entries are built from these artifacts without re-running benchmarks. "
            "Mutually exclusive with tasks_toml."
        ),
    )
    dataset.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) for readable summary, json to print raw JSON to stdout.",
    )
    dataset.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print raw JSON to stdout (shorthand for --format json).",
    )
    dataset.set_defaults(func=cmd_dataset)

    build_dataset = sub.add_parser(
        "build-dataset",
        help="Build a token-reduction benchmark dataset using built-in tasks (no TOML required)",
    )
    build_dataset.add_argument("--repo", default=".", help="Repository path to benchmark against")
    build_dataset.add_argument(
        "--tasks-toml",
        default=None,
        help="Optional TOML file with extra [[tasks]] entries to append to the built-in list",
    )
    build_dataset.add_argument(
        "--no-builtin",
        action="store_true",
        default=False,
        help="Skip built-in tasks and use only those from --tasks-toml",
    )
    build_dataset.add_argument("--max-tokens", type=int, default=None, help="Token budget forwarded to each benchmark run")
    build_dataset.add_argument("--top-files", type=int, default=None, help="Top-files limit forwarded to each benchmark run")
    build_dataset.add_argument("--config", help="Optional path to config TOML (default: <repo>/contextbudget.toml)")
    build_dataset.add_argument(
        "--out-prefix",
        default="contextbudget-context-dataset",
        help="Output file prefix for context dataset JSON/Markdown",
    )
    build_dataset.set_defaults(func=cmd_build_dataset)

    heatmap = sub.add_parser("heatmap", help="Aggregate historical pack runs into token heatmaps")
    heatmap.add_argument(
        "history",
        nargs="*",
        default=["."],
        help="Run JSON files or directories to scan recursively for pack artifacts.",
    )
    heatmap.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max rows to print in top heatmap sections.",
    )
    heatmap.add_argument(
        "--out-prefix",
        help="Output file prefix for heatmap JSON/Markdown",
        default="contextbudget-heatmap",
    )
    heatmap.set_defaults(func=cmd_heatmap)

    enforce = sub.add_parser(
        "enforce",
        help="Enforce a budget policy against a run artifact (exit non-zero on violations)",
    )
    enforce.add_argument("policy_toml", help="Path to policy TOML file")
    enforce.add_argument("run_json", help="Path to run JSON artifact produced by pack")
    enforce.set_defaults(func=cmd_enforce)

    watch = sub.add_parser("watch", help="Watch a repository and update scan state incrementally")
    watch.add_argument("--repo", default=".", help="Repository path")
    watch.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds for detecting local file changes.",
    )
    watch.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    watch.add_argument(
        "--once",
        action="store_true",
        help="Run a single incremental refresh and exit.",
    )
    watch.set_defaults(func=cmd_watch)


    advise = sub.add_parser(
        "advise",
        help=(
            "Scan a repository's import graph and suggest architecture improvements "
            "to reduce context bloat (split large files, extract modules, reduce dependencies)"
        ),
    )
    advise.add_argument("--repo", default=".", help="Repository path")
    advise.add_argument(
        "--history",
        nargs="*",
        default=[],
        help=(
            "Pack run JSON files or directories to use for inclusion-frequency signals. "
            "When omitted, frequency-based signals are skipped."
        ),
    )
    advise.add_argument(
        "--large-file-tokens",
        dest="large_file_tokens",
        type=int,
        default=None,
        help="Token threshold above which a file is considered large (default: 500).",
    )
    advise.add_argument(
        "--high-fanin",
        dest="high_fanin",
        type=int,
        default=None,
        help="Min importer count to flag a file as high-fan-in (default: 5).",
    )
    advise.add_argument(
        "--high-fanout",
        dest="high_fanout",
        type=int,
        default=None,
        help="Min outgoing import count to flag high-fan-out (default: 10).",
    )
    advise.add_argument(
        "--high-frequency-rate",
        dest="high_frequency_rate",
        type=float,
        default=None,
        help="Min pack-inclusion rate (0-1) to flag a frequently-included file (default: 0.5).",
    )
    advise.add_argument(
        "--top",
        type=int,
        default=25,
        help="Maximum number of suggestions to output (default: 25).",
    )
    advise.add_argument("--out-prefix", default="contextbudget-advise", help="Output file prefix")
    advise.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    advise.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) for readable suggestions, json to print raw JSON to stdout.",
    )
    advise.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print raw JSON to stdout (shorthand for --format json).",
    )
    advise.set_defaults(func=cmd_advise)

    visualize = sub.add_parser(
        "visualize",
        help=(
            "Build and export a repository dependency graph annotated with token counts "
            "and historical inclusion frequency"
        ),
    )
    visualize.add_argument("--repo", default=".", help="Repository path")
    visualize.add_argument(
        "--history",
        nargs="*",
        default=[],
        help=(
            "Pack run JSON files or directories to use for inclusion-frequency "
            "annotations.  When omitted, inclusion counts default to zero."
        ),
    )
    visualize.add_argument(
        "--html",
        action="store_true",
        help="Also write a self-contained interactive HTML visualization.",
    )
    visualize.add_argument(
        "--out-prefix",
        default="contextbudget-graph",
        help="Output file prefix for graph JSON, Markdown, and optional HTML.",
    )
    visualize.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    visualize.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) for readable summary, json to print raw JSON to stdout.",
    )
    visualize.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print raw JSON to stdout (shorthand for --format json).",
    )
    visualize.set_defaults(func=cmd_visualize)

    dashboard = sub.add_parser(
        "dashboard",
        help="Start a local web UI to browse and compare all run artifacts interactively",
    )
    dashboard.add_argument(
        "paths",
        nargs="*",
        default=[],
        help=(
            "Directories or JSON artifact files to scan for run history. "
            "Defaults to the current directory."
        ),
    )
    dashboard.add_argument(
        "--port",
        type=int,
        default=7842,
        help="Local port for the dashboard server (default: 7842).",
    )
    dashboard.add_argument(
        "--no-open",
        action="store_true",
        help="Do not automatically open the browser.",
    )
    dashboard.add_argument(
        "--export",
        action="store_true",
        help="Export dashboard data as JSON and exit without starting the server.",
    )
    dashboard.add_argument(
        "--out-prefix",
        default="contextbudget-dashboard",
        help="Output file prefix for --export mode (default: contextbudget-dashboard).",
    )
    dashboard.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) starts the server, json prints dashboard data to stdout.",
    )
    dashboard.set_defaults(func=cmd_dashboard)

    prepare_context_cmd = sub.add_parser(
        "prepare-context",
        help="Prepare optimized agent context using the middleware layer",
    )
    prepare_context_cmd.add_argument("task", help="Task description")
    prepare_context_cmd.add_argument("--repo", default=".", help="Repository path")
    prepare_context_cmd.add_argument(
        "--workspace",
        default=None,
        help="Workspace TOML describing multiple local repositories/packages.",
    )
    prepare_context_cmd.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        dest="max_tokens",
        help="Token budget override (takes precedence over [budget].max_tokens).",
    )
    prepare_context_cmd.add_argument(
        "--top-files",
        type=int,
        default=None,
        dest="top_files",
        help="Max files considered during packing (overrides [budget].top_files).",
    )
    prepare_context_cmd.add_argument(
        "--delta",
        default=None,
        help="Optional previous run JSON used to emit an incremental delta context package.",
    )
    prepare_context_cmd.add_argument(
        "--strict",
        action="store_true",
        help="Enable strict policy enforcement (non-zero exit on violations).",
    )
    prepare_context_cmd.add_argument(
        "--policy",
        default=None,
        help="Optional policy TOML for strict checks.",
    )
    prepare_context_cmd.add_argument(
        "--out-prefix",
        default="prepare-context-run",
        dest="out_prefix",
        help="Output file prefix for JSON/Markdown (default: prepare-context-run).",
    )
    prepare_context_cmd.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    prepare_context_cmd.set_defaults(func=cmd_prepare_context)

    drift = sub.add_parser(
        "drift",
        help="Detect and alert on token usage growth trends across historical pack runs",
    )
    drift.add_argument(
        "--repo",
        default=".",
        help="Repository path (history is read from <repo>/.contextbudget/history.json).",
    )
    drift.add_argument(
        "--task",
        default="",
        help="Optional task substring filter to restrict history entries analyzed.",
    )
    drift.add_argument(
        "--window",
        type=int,
        default=20,
        help="Number of recent history entries to include in the analysis (default: 20).",
    )
    drift.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        help="Token drift percentage that triggers an alert (default: 10.0).",
    )
    drift.add_argument(
        "--out-prefix",
        default="contextbudget-drift",
        help="Output file prefix for drift JSON/Markdown.",
    )
    drift.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) for readable alert summary, json to print raw JSON to stdout.",
    )
    drift.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print raw JSON to stdout (shorthand for --format json).",
    )
    drift.add_argument(
        "--runs",
        nargs="+",
        default=None,
        metavar="RUN_JSON",
        help=(
            "Explicit pack run artifact JSON files to analyze for drift "
            "(alternative to reading from <repo>/.contextbudget/history.json)."
        ),
    )
    drift.set_defaults(func=cmd_drift)

    observe = sub.add_parser(
        "observe",
        help="Extract and store observability metrics from a pack run artifact",
    )
    observe.add_argument(
        "run_json",
        help="Path to a run artifact JSON file produced by 'contextbudget pack'.",
    )
    observe.add_argument(
        "--out-prefix",
        default="",
        help="Output file prefix for observe JSON/Markdown (default: <run>-observe).",
    )
    observe.add_argument(
        "--base-dir",
        default="",
        help=(
            "Repository root used to resolve the metrics store path "
            "(default: directory containing run_json)."
        ),
    )
    observe.add_argument(
        "--no-store",
        action="store_true",
        help="Do not persist the report to the local metrics store.",
    )
    observe.add_argument(
        "--export-history",
        action="store_true",
        help="Export the full metrics store history to a JSON file.",
    )
    observe.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) for readable metrics report, json to print raw JSON to stdout.",
    )
    observe.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print raw JSON to stdout (shorthand for --format json).",
    )
    observe.set_defaults(func=cmd_observe)

    control_plane_cmd = sub.add_parser(
        "control-plane",
        help="Start the control plane HTTP API server for multi-team analytics",
    )
    control_plane_cmd.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the server to (default: 127.0.0.1).",
    )
    control_plane_cmd.add_argument(
        "--port",
        type=int,
        default=7700,
        help="Port for the control plane server (default: 7700).",
    )
    control_plane_cmd.add_argument(
        "--db",
        default=".contextbudget/control_plane.db",
        help="Path to SQLite database file (default: .contextbudget/control_plane.db).",
    )
    control_plane_cmd.set_defaults(func=cmd_control_plane)

    cost_analysis = sub.add_parser(
        "cost-analysis",
        help="Compute financial impact of context optimisation from a run artifact",
    )
    cost_analysis.add_argument(
        "run_json",
        nargs="?",
        default="",
        help="Path to a run artifact JSON file produced by 'contextbudget pack'.",
    )
    cost_analysis.add_argument(
        "--model",
        default="gpt-4o",
        help=(
            "Model name used for pricing lookup (default: gpt-4o). "
            "Supports Anthropic, OpenAI, Google, Mistral, Meta Llama, DeepSeek, Qwen, Cohere, and others. "
            "Run `contextbudget cost-analysis --list-models` to see all known models."
        ),
    )
    cost_analysis.add_argument(
        "--price-input",
        dest="price_input",
        type=float,
        default=None,
        help="Custom input token price in USD per 1 000 000 tokens (overrides built-in model pricing).",
    )
    cost_analysis.add_argument(
        "--out",
        default="",
        help="Write cost analysis metrics to this JSON file path.",
    )
    cost_analysis.add_argument(
        "--list-models",
        action="store_true",
        default=False,
        help="Print all models in the built-in pricing table and exit.",
    )
    cost_analysis.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format: human (default) for readable summary, json to print raw JSON to stdout.",
    )
    cost_analysis.set_defaults(func=cmd_cost_analysis)

    gateway_cmd = sub.add_parser(
        "gateway",
        help="Start the ContextBudget Runtime Gateway (agent → ContextBudget → LLM middleware)",
    )
    gateway_cmd.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the gateway server to (default: 127.0.0.1).",
    )
    gateway_cmd.add_argument(
        "--port",
        type=int,
        default=8787,
        help="Port for the gateway server (default: 8787).",
    )
    gateway_cmd.add_argument(
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=128_000,
        help="Default token budget applied when a request omits max_tokens (default: 128000).",
    )
    gateway_cmd.add_argument(
        "--max-files",
        dest="max_files",
        type=int,
        default=100,
        help="Default top-files cap applied when a request omits max_files (default: 100).",
    )
    gateway_cmd.add_argument(
        "--config",
        default="",
        help="Path to a contextbudget.toml shared by all gateway requests.",
    )
    gateway_cmd.add_argument(
        "--telemetry",
        action="store_true",
        default=False,
        help="Enable gateway telemetry event emission (default: off).",
    )
    gateway_cmd.add_argument(
        "--no-log-requests",
        dest="no_log_requests",
        action="store_true",
        default=False,
        help="Suppress per-request HTTP log lines.",
    )
    gateway_cmd.add_argument(
        "--api-key",
        dest="api_key",
        default=None,
        help="Bearer token required for all gateway requests (env: CB_GATEWAY_API_KEY).",
    )
    gateway_cmd.set_defaults(func=cmd_gateway)

    init_cmd = sub.add_parser(
        "init",
        help="Auto-detect repository language and generate contextbudget.toml and policy.toml",
    )
    init_cmd.add_argument(
        "--repo",
        default=".",
        help="Repository path to initialize (default: current directory).",
    )
    init_cmd.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing configuration files.",
    )
    init_cmd.set_defaults(func=cmd_init)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
