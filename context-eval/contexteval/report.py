"""Result aggregation and Markdown rendering."""

from __future__ import annotations

import statistics
from dataclasses import asdict

from contexteval.runner import TaskResult


def aggregate(results: list[TaskResult]) -> dict:
    tools = sorted({name for r in results for name in r.tools})
    summary: dict[str, dict] = {}
    for name in tools:
        coverages = [r.tools[name].coverage_pct for r in results if name in r.tools]
        per_point = [
            r.tools[name].tokens_per_point
            for r in results
            if name in r.tools and r.tools[name].tokens_per_point is not None
        ]
        tokens = [r.tools[name].tokens_used for r in results if name in r.tools]
        errors = sum(1 for r in results if name in r.tools and r.tools[name].error)
        summary[name] = {
            "tasks": len(coverages),
            "mean_coverage_pct": round(statistics.fmean(coverages), 1) if coverages else 0.0,
            "median_coverage_pct": round(statistics.median(coverages), 1) if coverages else 0.0,
            "mean_tokens_used": int(statistics.fmean(tokens)) if tokens else 0,
            "mean_tokens_per_coverage_point": (
                round(statistics.fmean(per_point), 1) if per_point else None
            ),
            "errors": errors,
        }
    return summary


def as_dict(results: list[TaskResult], *, budget: int, repo: str) -> dict:
    return {
        "budget_tokens": budget,
        "repo": repo,
        "task_count": len(results),
        "aggregate": aggregate(results),
        "tasks": [
            {
                "commit": r.task.commit[:10],
                "description": r.task.description,
                "ground_truth": list(r.ground_truth),
                "universe_size": r.universe_size,
                "tools": {name: asdict(tool) for name, tool in r.tools.items()},
            }
            for r in results
        ],
    }


def render_markdown(results: list[TaskResult], *, budget: int, repo: str, generated: str) -> str:
    summary = aggregate(results)
    ordered = sorted(summary.items(), key=lambda kv: -kv[1]["mean_coverage_pct"])

    lines = [
        "# context-eval results",
        "",
        f"Repository: `{repo}` | Token budget: {budget:,} | "
        f"Tasks: {len(results)} (from real git history) | Generated: {generated}",
        "",
        "## Aggregate",
        "",
        "| Tool | Mean coverage | Median coverage | Mean tokens used | Tokens / coverage point |",
        "|------|--------------:|----------------:|-----------------:|------------------------:|",
    ]
    for name, row in ordered:
        per_point = row["mean_tokens_per_coverage_point"]
        per_point_str = f"{per_point:,.1f}" if per_point is not None else "n/a"
        lines.append(
            f"| `{name}` | **{row['mean_coverage_pct']:.1f}%** "
            f"| {row['median_coverage_pct']:.1f}% "
            f"| {row['mean_tokens_used']:,} "
            f"| {per_point_str} |"
        )

    lines += [
        "",
        "Coverage: share of the files the task's real commit modified that "
        "the tool placed inside the budget. Tokens per coverage point: mean "
        "of per-task `tokens_used / coverage`; lower is cheaper evidence.",
        "",
        "## Per-task coverage",
        "",
    ]
    tools = [name for name, _ in ordered]
    header = "| Task | GT files | " + " | ".join(f"`{t}`" for t in tools) + " |"
    sep = "|------|---------:|" + "|".join(["---:"] * len(tools)) + "|"
    lines += [header, sep]
    for r in results:
        cells = []
        for t in tools:
            tool = r.tools.get(t)
            cells.append(f"{tool.coverage_pct:.0f}%" if tool else "-")
        desc = r.task.description
        if len(desc) > 60:
            desc = desc[:57] + "..."
        lines.append(f"| {desc} | {len(r.ground_truth)} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)
