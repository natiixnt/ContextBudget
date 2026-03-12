from __future__ import annotations

"""JSON and Markdown render/output helpers."""

import json
from pathlib import Path


def write_json(path: Path, data: dict) -> None:
    """Write JSON file with stable formatting."""

    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> dict:
    """Read JSON file into a dictionary."""

    return json.loads(path.read_text(encoding="utf-8"))


def render_plan_markdown(data: dict) -> str:
    """Render plan-stage payload to Markdown."""

    lines = [
        "# ContextBudget Plan",
        "",
        f"Task: {data['task']}",
        f"Repository: {data['repo']}",
        f"Scanned files: {data['scanned_files']}",
        "",
        "## Ranked Relevant Files",
    ]
    for item in data["ranked_files"]:
        reasons = ", ".join(item["reasons"]) if item["reasons"] else "no specific reason"
        lines.append(f"- `{item['path']}` (score: {item['score']}) - {reasons}")
    if not data["ranked_files"]:
        lines.append("- No files matched current heuristic signals.")
    lines.append("")
    return "\n".join(lines)


def render_pack_markdown(data: dict) -> str:
    """Render pack run payload to Markdown."""

    budget = data.get("budget", {})
    lines = [
        "# ContextBudget Pack Report",
        "",
        f"Task: {data.get('task', '')}",
        f"Repository: {data.get('repo', '')}",
        f"Max tokens: {data.get('max_tokens', 0)}",
        "",
        "## Budget",
        f"- Estimated input tokens: {budget.get('estimated_input_tokens', 0)}",
        f"- Estimated saved tokens: {budget.get('estimated_saved_tokens', 0)}",
        f"- Duplicate reads prevented: {budget.get('duplicate_reads_prevented', 0)}",
        f"- Quality risk estimate: {budget.get('quality_risk_estimate', 'unknown')}",
        f"- Cache hits: {data.get('cache_hits', 0)}",
        "",
        "## Files Included",
    ]
    included = data.get("files_included", [])
    if included:
        for path in included:
            lines.append(f"- `{path}`")
    else:
        lines.append("- None")

    lines.extend(["", "## Files Skipped"])
    skipped = data.get("files_skipped", [])
    if skipped:
        for path in skipped:
            lines.append(f"- `{path}`")
    else:
        lines.append("- None")

    lines.extend(["", "## Ranked Relevant Files"])
    for item in data.get("ranked_files", []):
        lines.append(f"- `{item['path']}` (score: {item['score']})")

    lines.extend(["", "## Chunk Selection"])
    for item in data.get("compressed_context", []):
        ranges = item.get("selected_ranges", [])
        if ranges:
            first = ranges[0]
            range_preview = f"{first.get('start_line', '?')}-{first.get('end_line', '?')}"
            if len(ranges) > 1:
                range_preview += f", +{len(ranges) - 1} more"
        else:
            range_preview = "n/a"
        lines.append(
            f"- `{item.get('path', '')}`: {item.get('chunk_strategy', 'none')} - "
            f"{item.get('chunk_reason', '')} (ranges: {range_preview})"
        )

    lines.append("")
    return "\n".join(lines)


def render_report_markdown(data: dict) -> str:
    """Render summary report payload to Markdown."""

    lines = [
        "# ContextBudget Summary Report",
        "",
        f"Task: {data.get('task', '')}",
        f"Repository: {data.get('repo', '')}",
        f"Generated at: {data.get('generated_at', '')}",
        "",
        f"- Estimated input tokens: {data.get('estimated_input_tokens', 0)}",
        f"- Estimated saved tokens: {data.get('estimated_saved_tokens', 0)}",
        f"- Duplicate reads prevented: {data.get('duplicate_reads_prevented', 0)}",
        f"- Quality risk estimate: {data.get('quality_risk_estimate', 'unknown')}",
        "",
        "## Files Included",
    ]

    included = data.get("files_included", [])
    if included:
        for item in included:
            lines.append(f"- `{item}`")
    else:
        lines.append("- None")

    lines.extend(["", "## Files Skipped"])
    skipped = data.get("files_skipped", [])
    if skipped:
        for item in skipped:
            lines.append(f"- `{item}`")
    else:
        lines.append("- None")

    lines.append("")
    return "\n".join(lines)


def render_policy_markdown(policy_data: dict) -> str:
    """Render strict policy evaluation block to Markdown."""

    lines = [
        "## Policy",
        f"- Passed: {policy_data.get('passed', False)}",
    ]
    violations = policy_data.get("violations", [])
    if violations:
        for violation in violations:
            lines.append(f"- Violation: {violation}")
    else:
        lines.append("- No violations")
    return "\n".join(lines)


def render_diff_markdown(data: dict) -> str:
    """Render run-to-run diff payload to Markdown."""

    task = data.get("task_diff", {})
    context = data.get("context_diff", {})
    budget = data.get("budget_delta", {})
    scores = data.get("ranked_score_changes", [])

    lines = [
        "# ContextBudget Diff Report",
        "",
        f"Old run: {data.get('old_run', '')}",
        f"New run: {data.get('new_run', '')}",
        "",
        "## Task Difference",
        f"- Changed: {task.get('changed', False)}",
        f"- Old task: {task.get('old_task', '')}",
        f"- New task: {task.get('new_task', '')}",
        "",
        "## Context File Changes",
        f"- Files added: {context.get('added_count', 0)}",
        f"- Files removed: {context.get('removed_count', 0)}",
    ]

    added = context.get("files_added", [])
    removed = context.get("files_removed", [])
    if added:
        for path in added:
            lines.append(f"- Added: `{path}`")
    if removed:
        for path in removed:
            lines.append(f"- Removed: `{path}`")
    if not added and not removed:
        lines.append("- No context file changes")

    lines.extend(["", "## Ranked Score Changes"])
    if scores:
        for item in scores[:25]:
            old_score = item.get("old_score")
            new_score = item.get("new_score")
            delta = item.get("delta", 0)
            lines.append(
                f"- `{item.get('path', '')}`: {old_score} -> {new_score} "
                f"(delta: {delta}, {item.get('change_type', 'changed')})"
            )
        if len(scores) > 25:
            lines.append(f"- ... {len(scores) - 25} more changes")
    else:
        lines.append("- No ranked score changes")

    input_delta = budget.get("estimated_input_tokens", {})
    saved_delta = budget.get("estimated_saved_tokens", {})
    risk_delta = budget.get("quality_risk", {})
    cache_delta = budget.get("cache_hits", {})

    lines.extend(
        [
            "",
            "## Budget Deltas",
            (
                "- Estimated input tokens: "
                f"{input_delta.get('old', 0)} -> {input_delta.get('new', 0)} "
                f"(delta: {input_delta.get('delta', 0)})"
            ),
            (
                "- Estimated saved tokens: "
                f"{saved_delta.get('old', 0)} -> {saved_delta.get('new', 0)} "
                f"(delta: {saved_delta.get('delta', 0)})"
            ),
            (
                "- Quality risk: "
                f"{risk_delta.get('old', 'unknown')} -> {risk_delta.get('new', 'unknown')} "
                f"(delta level: {risk_delta.get('delta_level', 0)})"
            ),
            (
                "- Cache hits: "
                f"{cache_delta.get('old', 0)} -> {cache_delta.get('new', 0)} "
                f"(delta: {cache_delta.get('delta', 0)})"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def render_benchmark_markdown(data: dict) -> str:
    """Render benchmark artifact to Markdown."""

    strategies = data.get("strategies", [])
    lines = [
        "# ContextBudget Benchmark Report",
        "",
        f"Task: {data.get('task', '')}",
        f"Repository: {data.get('repo', '')}",
        f"Baseline full-context tokens: {data.get('baseline_full_context_tokens', 0)}",
        f"Token budget: {data.get('max_tokens', 0)}",
        f"Top files: {data.get('top_files', 0)}",
        "",
        "## Strategy Comparison",
        "",
        "| Strategy | Input Tokens | Saved Tokens | Files Included | Duplicate Reads Prevented | Quality Risk | Cache Hits | Runtime (ms) |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]

    for strategy in strategies:
        lines.append(
            "| "
            f"{strategy.get('strategy', '')} | "
            f"{strategy.get('estimated_input_tokens', 0)} | "
            f"{strategy.get('estimated_saved_tokens', 0)} | "
            f"{len(strategy.get('files_included', []))} | "
            f"{strategy.get('duplicate_reads_prevented', 0)} | "
            f"{strategy.get('quality_risk_estimate', 'unknown')} | "
            f"{strategy.get('cache_hits', 0)} | "
            f"{strategy.get('runtime_ms', 0)} |"
        )

    lines.extend(["", "## Strategy Details"])
    for strategy in strategies:
        lines.append(f"- `{strategy.get('strategy', '')}`: {strategy.get('description', '')}")
        if strategy.get("notes"):
            lines.append(f"- Notes: {strategy.get('notes')}")
        files_included = strategy.get("files_included", [])
        files_skipped = strategy.get("files_skipped", [])
        lines.append(f"- Files included ({len(files_included)}): {', '.join(files_included) if files_included else 'none'}")
        lines.append(f"- Files skipped ({len(files_skipped)}): {', '.join(files_skipped) if files_skipped else 'none'}")

    lines.append("")
    return "\n".join(lines)
