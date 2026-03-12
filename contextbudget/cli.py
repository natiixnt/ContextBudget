from __future__ import annotations

import argparse
from pathlib import Path

from contextbudget.core.pipeline import as_json_dict, run_pack, run_plan, run_report_from_json
from contextbudget.core.render import (
    read_json,
    render_pack_markdown,
    render_plan_markdown,
    render_report_markdown,
    write_json,
)
from contextbudget.schemas.models import DEFAULT_MAX_TOKENS, normalize_repo


def _base_name(task: str) -> str:
    sanitized = "-".join(task.lower().strip().split())
    return sanitized[:40] if sanitized else "run"


def cmd_plan(args: argparse.Namespace) -> int:
    repo = normalize_repo(args.repo)
    data = run_plan(args.task, repo)

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
    repo = normalize_repo(args.repo)
    report = run_pack(args.task, repo=repo, max_tokens=args.max_tokens)
    data = as_json_dict(report)

    base = args.out_prefix or "run"
    json_path = Path(f"{base}.json")
    md_path = Path(f"{base}.md")

    write_json(json_path, data)
    md_path.write_text(render_pack_markdown(data), encoding="utf-8")

    budget = data["budget"]
    print(f"Wrote run JSON: {json_path}")
    print(f"Wrote run Markdown: {md_path}")
    print(
        "Budget: "
        f"input={budget['estimated_input_tokens']} tokens, "
        f"saved={budget['estimated_saved_tokens']} tokens, "
        f"risk={budget['quality_risk_estimate']}"
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_path = Path(args.run_json)
    data = read_json(run_path)
    summary = run_report_from_json(data)
    markdown = render_report_markdown(summary)
    print(markdown)

    out_path = Path(args.out) if args.out else run_path.with_suffix(".report.md")
    out_path.write_text(markdown, encoding="utf-8")
    print(f"Wrote summary Markdown: {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextbudget",
        description="Reduce token usage by planning and packing repository context.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Rank relevant files for a natural language task")
    plan.add_argument("task", help="Task description")
    plan.add_argument("--repo", default=".", help="Repository path")
    plan.add_argument("--out-prefix", help="Output file prefix for JSON/Markdown")
    plan.set_defaults(func=cmd_plan)

    pack = sub.add_parser("pack", help="Build compressed context under token budget")
    pack.add_argument("task", help="Task description")
    pack.add_argument("--repo", default=".", help="Repository path")
    pack.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    pack.add_argument("--out-prefix", help="Output file prefix for JSON/Markdown", default="run")
    pack.set_defaults(func=cmd_pack)

    report = sub.add_parser("report", help="Read a run JSON and produce a summary report")
    report.add_argument("run_json", help="Path to run JSON produced by pack")
    report.add_argument("--out", help="Path for markdown summary output")
    report.set_defaults(func=cmd_report)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
