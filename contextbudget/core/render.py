from __future__ import annotations

"""JSON and Markdown render/output helpers."""

import json
from pathlib import Path

from contextbudget.cache import normalize_cache_report
from contextbudget.compressors.summarizers import normalize_summarizer_report
from contextbudget.core.tokens import normalize_token_estimator_report


def write_json(path: Path, data: dict) -> None:
    """Write JSON file with stable formatting."""

    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> dict:
    """Read JSON file into a dictionary."""

    return json.loads(path.read_text(encoding="utf-8"))


def _append_workspace_lines(lines: list[str], data: dict) -> None:
    workspace = data.get("workspace")
    if isinstance(workspace, str) and workspace:
        lines.append(f"Workspace: {workspace}")

    scanned_repos = data.get("scanned_repos", [])
    if isinstance(scanned_repos, list) and scanned_repos:
        lines.append("Scanned repos:")
        for item in scanned_repos:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- "
                f"{item.get('label', '')}: {item.get('path', '')} "
                f"(files: {item.get('scanned_files', 0)})"
            )

    selected_repos = data.get("selected_repos", [])
    if isinstance(selected_repos, list) and selected_repos:
        lines.append(f"Selected repos: {', '.join(str(item) for item in selected_repos)}")


def _append_summarizer_lines(lines: list[str], data: dict) -> None:
    summarizer = normalize_summarizer_report(data)
    lines.extend(
        [
            f"- Summarizer selected: {summarizer.get('selected_backend', 'deterministic')}",
            f"- Summarizer effective: {summarizer.get('effective_backend', 'deterministic')}",
            f"- External summarizer configured: {summarizer.get('external_configured', False)}",
            f"- External summarizer resolved: {summarizer.get('external_resolved', False)}",
            f"- Summarizer fallback used: {summarizer.get('fallback_used', False)}",
            f"- Summarizer fallback count: {summarizer.get('fallback_count', 0)}",
            f"- Summary files processed: {summarizer.get('summary_count', 0)}",
        ]
    )
    adapter = str(summarizer.get("external_adapter", "") or "")
    if adapter:
        lines.append(f"- External summarizer adapter: {adapter}")
    logs = summarizer.get("logs", [])
    if isinstance(logs, list) and logs:
        lines.append("- Summarizer logs:")
        for item in logs:
            lines.append(f"  - {item}")


def _append_token_estimator_lines(lines: list[str], data: dict) -> None:
    estimator = normalize_token_estimator_report(data)
    lines.extend(
        [
            f"- Token estimator selected: {estimator.get('selected_backend', 'heuristic')}",
            f"- Token estimator effective: {estimator.get('effective_backend', 'heuristic')}",
            f"- Token estimator uncertainty: {estimator.get('uncertainty', 'approximate')}",
            f"- Token estimator available: {estimator.get('available', True)}",
            f"- Token estimator fallback used: {estimator.get('fallback_used', False)}",
        ]
    )
    model = str(estimator.get("model", "") or "")
    if model:
        lines.append(f"- Token estimator model: {model}")
    encoding = str(estimator.get("encoding", "") or "")
    if encoding:
        lines.append(f"- Token estimator encoding: {encoding}")
    fallback_reason = str(estimator.get("fallback_reason", "") or "")
    if fallback_reason:
        lines.append(f"- Token estimator fallback reason: {fallback_reason}")
    notes = estimator.get("notes", [])
    if isinstance(notes, list) and notes:
        lines.append("- Token estimator notes:")
        for item in notes:
            lines.append(f"  - {item}")


def _append_implementation_lines(lines: list[str], data: dict) -> None:
    implementations = data.get("implementations", {})
    if not isinstance(implementations, dict) or not implementations:
        return
    lines.append("Implementations:")
    for key in ("scorer", "compressor", "token_estimator"):
        value = implementations.get(key)
        if value:
            lines.append(f"- {key}: {value}")


def render_plan_markdown(data: dict) -> str:
    """Render plan-stage payload to Markdown."""

    lines = [
        "# ContextBudget Plan",
        "",
        f"Task: {data['task']}",
        f"Repository: {data['repo']}",
        f"Scanned files: {data['scanned_files']}",
    ]
    _append_workspace_lines(lines, data)
    _append_implementation_lines(lines, data)
    lines.extend(["", "## Token Estimator"])
    _append_token_estimator_lines(lines, data)
    lines.extend(["", "## Ranked Relevant Files"])
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
    cache = normalize_cache_report(data)
    lines = [
        "# ContextBudget Pack Report",
        "",
        f"Task: {data.get('task', '')}",
        f"Repository: {data.get('repo', '')}",
        f"Max tokens: {data.get('max_tokens', 0)}",
    ]
    _append_workspace_lines(lines, data)
    _append_implementation_lines(lines, data)
    lines.extend([
        "",
        "## Token Estimator",
    ])
    _append_token_estimator_lines(lines, data)
    lines.extend([
        "",
        "## Budget",
        f"- Estimated input tokens: {budget.get('estimated_input_tokens', 0)}",
        f"- Estimated saved tokens: {budget.get('estimated_saved_tokens', 0)}",
        f"- Duplicate reads prevented: {budget.get('duplicate_reads_prevented', 0)}",
        f"- Quality risk estimate: {budget.get('quality_risk_estimate', 'unknown')}",
        f"- Cache backend: {cache.get('backend', 'unknown')}",
        f"- Cache hits: {cache.get('hits', 0)}",
        f"- Cache misses: {cache.get('misses', 0)}",
        f"- Cache writes: {cache.get('writes', 0)}",
    ])
    _append_summarizer_lines(lines, data)
    lines.extend(["", "## Files Included"])
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

    cache = normalize_cache_report(data)
    lines = [
        "# ContextBudget Summary Report",
        "",
        f"Task: {data.get('task', '')}",
        f"Repository: {data.get('repo', '')}",
        f"Generated at: {data.get('generated_at', '')}",
    ]
    _append_workspace_lines(lines, data)
    _append_implementation_lines(lines, data)
    lines.extend([
        "",
        "## Token Estimator",
    ])
    _append_token_estimator_lines(lines, data)
    lines.extend([
        "",
        f"- Estimated input tokens: {data.get('estimated_input_tokens', 0)}",
        f"- Estimated saved tokens: {data.get('estimated_saved_tokens', 0)}",
        f"- Duplicate reads prevented: {data.get('duplicate_reads_prevented', 0)}",
        f"- Quality risk estimate: {data.get('quality_risk_estimate', 'unknown')}",
        f"- Cache backend: {cache.get('backend', 'unknown')}",
        f"- Cache hits: {cache.get('hits', 0)}",
        f"- Cache misses: {cache.get('misses', 0)}",
        f"- Cache writes: {cache.get('writes', 0)}",
    ])
    _append_summarizer_lines(lines, data)
    lines.extend(["", "## Files Included"])

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
    ]
    _append_workspace_lines(lines, data)
    _append_implementation_lines(lines, data)
    lines.extend(["", "## Token Estimator"])
    _append_token_estimator_lines(lines, data)
    lines.extend([
        "",
        "## Strategy Comparison",
        "",
        "| Strategy | Input Tokens | Saved Tokens | Files Included | Duplicate Reads Prevented | Quality Risk | Cache Hits | Runtime (ms) |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ])

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

    estimator_samples = data.get("estimator_samples", [])
    if isinstance(estimator_samples, list) and estimator_samples:
        lines.extend(["", "## Estimator Samples"])
        for sample in estimator_samples:
            if not isinstance(sample, dict):
                continue
            label = str(sample.get("name", "sample"))
            path = str(sample.get("path", "") or "")
            path_suffix = f" ({path})" if path else ""
            lines.append(f"- `{label}`{path_suffix}: chars={sample.get('chars', 0)}")
            estimators = sample.get("estimators", [])
            if not isinstance(estimators, list):
                continue
            for estimator in estimators:
                if not isinstance(estimator, dict):
                    continue
                detail = (
                    f"  - {estimator.get('backend', '')}: "
                    f"tokens={estimator.get('estimated_tokens', 0)} "
                    f"effective={estimator.get('effective_backend', '')} "
                    f"uncertainty={estimator.get('uncertainty', '')}"
                )
                if estimator.get("fallback_used"):
                    detail = f"{detail} fallback=true"
                reason = str(estimator.get("fallback_reason", "") or "")
                if reason:
                    detail = f"{detail} reason={reason}"
                lines.append(detail)

    lines.append("")
    return "\n".join(lines)
