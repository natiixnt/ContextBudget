from __future__ import annotations

import json
from pathlib import Path


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def render_plan_markdown(data: dict) -> str:
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

    lines.append("")
    return "\n".join(lines)


def render_report_markdown(data: dict) -> str:
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
