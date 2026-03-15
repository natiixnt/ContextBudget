from __future__ import annotations

"""CLI entrypoint for ContextBudget commands."""

import argparse
from pathlib import Path
import time

from contextbudget.config import ContextBudgetConfig, load_config
from contextbudget.core.policy import (
    default_strict_policy,
    load_policy,
)
from contextbudget.engine import ContextBudgetEngine
from contextbudget.core.render import (
    render_advise_markdown,
    render_agent_plan_markdown,
    render_agent_simulation_markdown,
    render_benchmark_markdown,
    render_diff_markdown,
    render_heatmap_markdown,
    render_pack_markdown,
    render_plan_markdown,
    render_policy_markdown,
    render_pr_audit_markdown,
    render_pr_comment_markdown,
    render_profile_markdown,
    render_report_markdown,
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
    engine = ContextBudgetEngine(config_path=args.config)
    data = engine.simulate_agent(
        task=args.task,
        repo=args.repo,
        workspace=args.workspace,
        top_files=args.top_files,
        prompt_overhead_per_step=args.prompt_overhead,
        output_tokens_per_step=args.output_tokens,
        context_mode=args.context_mode,
    )

    base = args.out_prefix or f"contextbudget-simulate-{_base_name(args.task)}"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")

    write_json(json_path, data)
    md_path.write_text(render_agent_simulation_markdown(data), encoding="utf-8")

    print(f"Wrote simulation JSON: {json_path}")
    print(f"Wrote simulation Markdown: {md_path}")
    print(f"Context mode: {data.get('context_mode', 'isolated')}")

    steps = data.get("steps", [])
    if isinstance(steps, list):
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            print(
                f"{idx}. {step.get('title', '')} "
                f"(context={step.get('context_tokens', 0)}, "
                f"total={step.get('step_total_tokens', 0)}, "
                f"cumulative_ctx={step.get('cumulative_context_tokens', 0)})"
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

    print(markdown)

    prefix = args.out_prefix or run_path.with_suffix("").name + "-profile"
    json_path = Path(f"{prefix}.json")
    md_path = Path(f"{prefix}.md")
    write_json(json_path, data)
    md_path.write_text(markdown, encoding="utf-8")
    print(f"Wrote profile JSON:     {json_path}")
    print(f"Wrote profile Markdown: {md_path}")
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

    graph_data = engine.visualize(
        repo=args.repo,
        history=history or None,
    )

    base = args.out_prefix or "contextbudget-graph"
    json_path = Path(f"{base}.json")
    write_json(json_path, graph_data)
    print(f"Wrote graph JSON: {json_path}")

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
        print(f"Wrote graph HTML: {html_path}")

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
        help="Simulate agent workflow token costs step by step before execution",
    )
    simulate_agent.add_argument("task", help="Task description")
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
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
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
        help="Analyse a repository and suggest architecture changes to reduce context size",
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
    advise.set_defaults(func=cmd_advise)

    visualize = sub.add_parser(
        "visualize",
        help="Build and export a repository dependency graph annotated with token usage",
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
        help="Output file prefix for graph JSON (and optional HTML).",
    )
    visualize.add_argument(
        "--config",
        help="Optional path to config TOML (default: <repo>/contextbudget.toml).",
    )
    visualize.set_defaults(func=cmd_visualize)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
