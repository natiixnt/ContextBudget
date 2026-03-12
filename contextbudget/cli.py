from __future__ import annotations

"""CLI entrypoint for ContextBudget commands."""

import argparse
from pathlib import Path

from contextbudget.core.policy import (
    default_strict_policy,
    load_policy,
)
from contextbudget.engine import ContextBudgetEngine
from contextbudget.core.render import (
    render_benchmark_markdown,
    render_diff_markdown,
    render_pack_markdown,
    render_plan_markdown,
    render_policy_markdown,
    render_report_markdown,
    write_json,
)


def _base_name(task: str) -> str:
    sanitized = "-".join(task.lower().strip().split())
    return sanitized[:40] if sanitized else "run"


def cmd_plan(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    data = engine.plan(
        task=args.task,
        repo=args.repo,
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


def cmd_pack(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    data = engine.pack(
        task=args.task,
        repo=args.repo,
        max_tokens=args.max_tokens,
        top_files=args.top_files,
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

    budget = data["budget"]
    print(f"Wrote run JSON: {json_path}")
    print(f"Wrote run Markdown: {md_path}")
    print(
        "Budget: "
        f"input={budget['estimated_input_tokens']} tokens, "
        f"saved={budget['estimated_saved_tokens']} tokens, "
        f"risk={budget['quality_risk_estimate']}"
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


def cmd_benchmark(args: argparse.Namespace) -> int:
    engine = ContextBudgetEngine(config_path=args.config)
    benchmark_data = engine.benchmark(
        task=args.task,
        repo=args.repo,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextbudget",
        description=(
            "Reduce token usage by planning and packing repository context. "
            "Supports contextbudget.toml sections: [scan], [budget], [score], [compression], [cache], [telemetry]."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Rank relevant files for a natural language task")
    plan.add_argument("task", help="Task description")
    plan.add_argument("--repo", default=".", help="Repository path")
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

    pack = sub.add_parser("pack", help="Build compressed context under token budget")
    pack.add_argument("task", help="Task description")
    pack.add_argument("--repo", default=".", help="Repository path")
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

    benchmark = sub.add_parser("benchmark", help="Compare context packing strategies")
    benchmark.add_argument("task", help="Task description")
    benchmark.add_argument("--repo", default=".", help="Repository path")
    benchmark.add_argument("--max-tokens", type=int, default=None, help="Token budget override for packed strategies.")
    benchmark.add_argument("--top-files", type=int, default=None, help="Top files override for ranking-based strategies.")
    benchmark.add_argument("--config", help="Optional path to config TOML (default: <repo>/contextbudget.toml).")
    benchmark.add_argument("--out-prefix", help="Output file prefix for benchmark JSON/Markdown")
    benchmark.set_defaults(func=cmd_benchmark)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
