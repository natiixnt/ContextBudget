from __future__ import annotations

"""JSON and Markdown render/output helpers."""

import json
from pathlib import Path

from contextbudget.cache import normalize_cache_report
from contextbudget.compressors.summarizers import normalize_summarizer_report
from contextbudget.core.delta import normalize_delta_report
from contextbudget.core.model_profiles import normalize_model_profile_report
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


def _append_model_profile_lines(lines: list[str], data: dict) -> None:
    model_profile = normalize_model_profile_report(data)
    if not model_profile:
        return
    lines.extend(
        [
            f"- Model profile selected: {model_profile.get('selected_profile', '')}",
            f"- Model profile resolved: {model_profile.get('resolved_profile', '')}",
            f"- Model family: {model_profile.get('family', '')}",
            f"- Model tokenizer: {model_profile.get('tokenizer', '')}",
            f"- Model context window: {model_profile.get('context_window', 0)}",
            "- Recommended compression strategy: "
            f"{model_profile.get('recommended_compression_strategy', '')}",
            f"- Effective max tokens: {model_profile.get('effective_max_tokens', 0)}",
            f"- Reserved output tokens: {model_profile.get('reserved_output_tokens', 0)}",
            f"- Budget source: {model_profile.get('budget_source', '')}",
            f"- Budget clamped: {model_profile.get('budget_clamped', False)}",
        ]
    )
    notes = model_profile.get("notes", [])
    if isinstance(notes, list) and notes:
        lines.append("- Model profile notes:")
        for item in notes:
            lines.append(f"  - {item}")


def _has_model_profile(data: dict) -> bool:
    return bool(normalize_model_profile_report(data))


def _append_implementation_lines(lines: list[str], data: dict) -> None:
    implementations = data.get("implementations", {})
    if not isinstance(implementations, dict) or not implementations:
        return
    lines.append("Implementations:")
    for key in ("scorer", "compressor", "token_estimator"):
        value = implementations.get(key)
        if value:
            lines.append(f"- {key}: {value}")


def _format_ranked_file_scores(item: dict) -> str:
    score = item.get("score", 0)
    if "heuristic_score" not in item and "historical_score" not in item:
        return f"score: {score}"
    return (
        f"combined: {score}, "
        f"heuristic: {item.get('heuristic_score', 0)}, "
        f"history: {item.get('historical_score', 0)}"
    )


def _append_delta_lines(lines: list[str], data: dict) -> None:
    delta = normalize_delta_report(data)
    if not delta:
        return

    budget = delta.get("budget", {})
    if not isinstance(budget, dict):
        budget = {}
    files_added = delta.get("files_added", [])
    if not isinstance(files_added, list):
        files_added = []
    files_removed = delta.get("files_removed", [])
    if not isinstance(files_removed, list):
        files_removed = []
    changed_files = delta.get("changed_files", [])
    if not isinstance(changed_files, list):
        changed_files = []
    changed_slices = delta.get("changed_slices", [])
    if not isinstance(changed_slices, list):
        changed_slices = []
    changed_symbols = delta.get("changed_symbols", [])
    if not isinstance(changed_symbols, list):
        changed_symbols = []

    lines.extend(
        [
            "",
            "## Delta Context",
            f"- Previous run: {delta.get('previous_run', '')}",
            f"- Original tokens: {budget.get('original_tokens', 0)}",
            f"- Delta tokens: {budget.get('delta_tokens', 0)}",
            f"- Tokens saved: {budget.get('tokens_saved', 0)}",
            f"- Files added: {len(files_added)}",
            f"- Files removed: {len(files_removed)}",
            f"- Changed files: {len(changed_files)}",
            f"- Changed slices: {len(changed_slices)}",
            f"- Changed symbols: {len(changed_symbols)}",
        ]
    )
    for path in files_added:
        lines.append(f"- Added: `{path}`")
    for path in files_removed:
        lines.append(f"- Removed: `{path}`")
    for path in changed_files:
        lines.append(f"- Updated: `{path}`")
    for item in changed_symbols[:10]:
        if not isinstance(item, dict):
            continue
        added = item.get("added_symbols", [])
        removed = item.get("removed_symbols", [])
        changed = item.get("changed_symbols", [])
        lines.append(
            f"- Symbol delta `{item.get('path', '')}`: +{len(added) if isinstance(added, list) else 0} "
            f"-{len(removed) if isinstance(removed, list) else 0} "
            f"~{len(changed) if isinstance(changed, list) else 0}"
        )


def _fmt_usd(value: float) -> str:
    """Format a USD float for Markdown: use 4 decimals, or 2 for large values."""
    if value >= 1.0:
        return f"${value:.4f}"
    return f"${value:.6f}"


def render_agent_simulation_markdown(data: dict) -> str:
    """Render agent workflow simulation artifact to Markdown."""

    steps = data.get("steps", [])
    context_mode = data.get("context_mode", "isolated")
    cost = data.get("cost_estimate") or {}
    model_name = data.get("model", cost.get("model", ""))

    lines = [
        "# ContextBudget Agent Simulation",
        "",
        f"Task: {data.get('task', '')}",
        f"Repository: {data.get('repo', '')}",
        f"Scanned files: {data.get('scanned_files', 0)}",
        f"Context mode: {context_mode}",
        f"Prompt overhead per step: {data.get('prompt_overhead_per_step', 0)} tokens",
        f"Output tokens per step: {data.get('output_tokens_per_step', 0)} tokens",
        f"Workflow steps: {len(steps) if isinstance(steps, list) else 0}",
    ]
    if model_name:
        provider = cost.get("provider", "")
        provider_str = f" ({provider})" if provider else ""
        lines.append(f"Model: {model_name}{provider_str}")
    _append_workspace_lines(lines, data)
    _append_implementation_lines(lines, data)
    if _has_model_profile(data):
        lines.extend(["", "## Model Assumptions"])
        _append_model_profile_lines(lines, data)
    lines.extend(["", "## Token Estimator"])
    _append_token_estimator_lines(lines, data)

    # ------------------------------------------------------------------
    # Cost estimate section
    # ------------------------------------------------------------------
    if isinstance(cost, dict) and cost:
        lines.extend([
            "",
            "## Estimated API Cost",
            "",
            f"| | Value |",
            f"|---|---|",
            f"| Model | {cost.get('model', '')} |",
            f"| Provider | {cost.get('provider', '—')} |",
            f"| Input price | ${cost.get('input_per_1m_usd', 0):.2f} / MTok |",
            f"| Output price | ${cost.get('output_per_1m_usd', 0):.2f} / MTok |",
            f"| Total input tokens | {cost.get('total_input_tokens', 0):,} |",
            f"| Total output tokens | {cost.get('total_output_tokens', 0):,} |",
            f"| **Total cost** | **{_fmt_usd(cost.get('total_cost_usd', 0.0))} USD** |",
            f"| Input cost | {_fmt_usd(cost.get('total_input_cost_usd', 0.0))} USD |",
            f"| Output cost | {_fmt_usd(cost.get('total_output_cost_usd', 0.0))} USD |",
            f"| Min step cost | {_fmt_usd(cost.get('min_step_cost_usd', 0.0))} USD |",
            f"| Max step cost | {_fmt_usd(cost.get('max_step_cost_usd', 0.0))} USD |",
            f"| Avg step cost | {_fmt_usd(cost.get('avg_step_cost_usd', 0.0))} USD |",
        ])
        notes = cost.get("notes", [])
        if isinstance(notes, list) and notes:
            lines.append("")
            for note in notes:
                lines.append(f"> **Note:** {note}")

    # ------------------------------------------------------------------
    # Token summary
    # ------------------------------------------------------------------
    lines.extend([
        "",
        "## Token Summary",
        f"- Total tokens (all steps): {data.get('total_tokens', 0)}",
        f"- Unique context tokens: {data.get('unique_context_tokens', 0)}",
        f"- Total context tokens (with reuse): {data.get('total_context_tokens', 0)}",
        f"- Total prompt overhead tokens: {data.get('total_prompt_tokens', 0)}",
        f"- Total output tokens: {data.get('total_output_tokens', 0)}",
        "",
        "## Token Variance",
        f"- Variance: {data.get('token_variance', 0.0)}",
        f"- Std deviation: {data.get('token_std_dev', 0.0)}",
        f"- Min step tokens: {data.get('min_step_tokens', 0)}",
        f"- Max step tokens: {data.get('max_step_tokens', 0)}",
        f"- Avg step tokens: {data.get('avg_step_tokens', 0.0)}",
    ])

    # ------------------------------------------------------------------
    # Step breakdown table (now includes cost column)
    # ------------------------------------------------------------------
    steps_cost: list[dict] = cost.get("steps_cost", []) if isinstance(cost, dict) else []
    has_cost = bool(steps_cost)
    if has_cost:
        lines.extend([
            "",
            "## Step Breakdown",
            "",
            "| # | Step | Context | Prompt | Output | Step Total | Cumul. Context | Est. Cost |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
    else:
        lines.extend([
            "",
            "## Step Breakdown",
            "",
            "| # | Step | Context | Prompt | Output | Step Total | Cumulative Context |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ])

    if isinstance(steps, list) and steps:
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            cost_cell = ""
            if has_cost and idx - 1 < len(steps_cost):
                sc = steps_cost[idx - 1]
                cost_cell = f" | {_fmt_usd(sc.get('step_cost_usd', 0.0))}"
            lines.append(
                f"| {idx} | {step.get('title', '')} "
                f"| {step.get('context_tokens', 0)} "
                f"| {step.get('prompt_tokens', 0)} "
                f"| {step.get('output_tokens', 0)} "
                f"| {step.get('step_total_tokens', 0)} "
                f"| {step.get('cumulative_context_tokens', 0)}"
                f"{cost_cell} |"
            )
    else:
        if has_cost:
            lines.append("| - | No steps | 0 | 0 | 0 | 0 | 0 | — |")
        else:
            lines.append("| - | No steps | 0 | 0 | 0 | 0 | 0 |")

    # ------------------------------------------------------------------
    # Per-step detail blocks
    # ------------------------------------------------------------------
    lines.extend(["", "## Step Details"])
    if isinstance(steps, list) and steps:
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            step_lines = [
                f"### {idx}. {step.get('title', '')}",
                f"- Step id: {step.get('id', '')}",
                f"- Objective: {step.get('objective', '')}",
                f"- Files read: {step.get('file_count', 0)}",
                f"- Context tokens: {step.get('context_tokens', 0)}",
                f"- Prompt overhead: {step.get('prompt_tokens', 0)}",
                f"- Output tokens: {step.get('output_tokens', 0)}",
                f"- Step total tokens: {step.get('step_total_tokens', 0)}",
                f"- Cumulative context tokens: {step.get('cumulative_context_tokens', 0)}",
                f"- Cumulative total tokens: {step.get('cumulative_total_tokens', 0)}",
            ]
            if has_cost and idx - 1 < len(steps_cost):
                sc = steps_cost[idx - 1]
                step_lines += [
                    f"- Estimated cost: {_fmt_usd(sc.get('step_cost_usd', 0.0))} USD "
                    f"(input {_fmt_usd(sc.get('input_cost_usd', 0.0))} + "
                    f"output {_fmt_usd(sc.get('output_cost_usd', 0.0))})",
                ]
            step_lines.append("- Files read:")
            lines.extend(step_lines)
            files_read = step.get("files_read", [])
            if isinstance(files_read, list) and files_read:
                for f in files_read:
                    if not isinstance(f, dict):
                        continue
                    lines.append(
                        f"  - `{f.get('path', '')}` "
                        f"[{f.get('read_type', 'step')}] "
                        f"({f.get('tokens', 0)} tokens)"
                    )
            else:
                lines.append("  - None")
    else:
        lines.append("- No steps produced.")

    lines.append("")
    return "\n".join(lines)


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
    if _has_model_profile(data):
        lines.extend(["", "## Model Assumptions"])
        _append_model_profile_lines(lines, data)
    lines.extend(["", "## Token Estimator"])
    _append_token_estimator_lines(lines, data)
    lines.extend(["", "## Ranked Relevant Files"])
    for item in data["ranked_files"]:
        reasons = ", ".join(item["reasons"]) if item["reasons"] else "no specific reason"
        lines.append(f"- `{item['path']}` ({_format_ranked_file_scores(item)}) - {reasons}")
    if not data["ranked_files"]:
        lines.append("- No files matched current heuristic signals.")
    lines.append("")
    return "\n".join(lines)


def render_agent_plan_markdown(data: dict) -> str:
    """Render agent workflow planning payload to Markdown."""

    steps = data.get("steps", [])
    shared_context = data.get("shared_context", [])
    lines = [
        "# ContextBudget Agent Plan",
        "",
        f"Task: {data.get('task', '')}",
        f"Repository: {data.get('repo', '')}",
        f"Scanned files: {data.get('scanned_files', 0)}",
        f"Workflow steps: {len(steps) if isinstance(steps, list) else 0}",
        f"Total estimated tokens: {data.get('total_estimated_tokens', 0)}",
        f"Unique context tokens: {data.get('unique_context_tokens', 0)}",
        f"Reused context tokens: {data.get('reused_context_tokens', 0)}",
    ]
    _append_workspace_lines(lines, data)
    _append_implementation_lines(lines, data)
    if _has_model_profile(data):
        lines.extend(["", "## Model Assumptions"])
        _append_model_profile_lines(lines, data)
    lines.extend(["", "## Token Estimator"])
    _append_token_estimator_lines(lines, data)

    lines.extend(["", "## Shared Context"])
    if isinstance(shared_context, list) and shared_context:
        for item in shared_context:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('path', '')}` "
                f"({item.get('estimated_tokens', 0)} tokens, reused in {item.get('reuse_count', 0)} steps)"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Workflow Steps"])
    if isinstance(steps, list) and steps:
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            lines.extend(
                [
                    f"### {idx}. {step.get('title', '')}",
                    f"- Step id: {step.get('id', '')}",
                    f"- Objective: {step.get('objective', '')}",
                    f"- Estimated tokens: {step.get('estimated_tokens', 0)}",
                    f"- Shared context tokens: {step.get('shared_context_tokens', 0)}",
                    f"- Step-specific tokens: {step.get('step_context_tokens', 0)}",
                    f"- Planning prompt: `{step.get('planning_prompt', '')}`",
                    "- Context:",
                ]
            )
            context = step.get("context", [])
            if isinstance(context, list) and context:
                for item in context:
                    if not isinstance(item, dict):
                        continue
                    lines.append(
                        "  - "
                        f"`{item.get('path', '')}` "
                        f"[{item.get('source', 'step')}] "
                        f"({item.get('estimated_tokens', 0)} tokens, score: {item.get('score', 0)})"
                    )
            else:
                lines.append("  - None")
    else:
        lines.append("- No workflow steps produced.")

    lines.extend(["", "## Ranked Relevant Files"])
    ranked_files = data.get("ranked_files", [])
    if isinstance(ranked_files, list) and ranked_files:
        for item in ranked_files:
            if not isinstance(item, dict):
                continue
            reasons = ", ".join(item.get("reasons", [])) if item.get("reasons") else "no specific reason"
            lines.append(f"- `{item.get('path', '')}` (score: {item.get('score', 0)}) - {reasons}")
    else:
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
    if _has_model_profile(data):
        lines.extend(["", "## Model Assumptions"])
        _append_model_profile_lines(lines, data)
    lines.extend(["", "## Token Estimator"])
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
        f"- Cache tokens saved: {cache.get('tokens_saved', 0)}",
        f"- Fragment cache hits: {cache.get('fragment_hits', 0)}",
        f"- Fragment cache misses: {cache.get('fragment_misses', 0)}",
        f"- Fragment cache writes: {cache.get('fragment_writes', 0)}",
    ])
    _append_summarizer_lines(lines, data)
    _append_delta_lines(lines, data)
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
        lines.append(f"- `{item['path']}` ({_format_ranked_file_scores(item)})")

    lines.extend(["", "## Chunk Selection"])
    for item in data.get("compressed_context", []):
        ranges = item.get("selected_ranges", [])
        symbols = item.get("symbols", [])
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
        cache_status = str(item.get("cache_status", "") or "")
        cache_reference = str(item.get("cache_reference", "") or "")
        if cache_status:
            cache_line = f"  - cache: {cache_status}"
            if cache_reference:
                cache_line = f"{cache_line} ({cache_reference})"
            lines.append(cache_line)
        if isinstance(symbols, list) and symbols:
            preview = ", ".join(
                f"{symbol.get('symbol_type', 'symbol')} {symbol.get('name', '')}"
                for symbol in symbols[:3]
                if isinstance(symbol, dict)
            )
            if preview:
                if len(symbols) > 3:
                    preview = f"{preview}, +{len(symbols) - 3} more"
                lines.append(f"  - symbols: {preview}")

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
    if _has_model_profile(data):
        lines.extend(["", "## Model Assumptions"])
        _append_model_profile_lines(lines, data)
    lines.extend(["", "## Token Estimator"])
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
        f"- Cache tokens saved: {cache.get('tokens_saved', 0)}",
        f"- Fragment cache hits: {cache.get('fragment_hits', 0)}",
        f"- Fragment cache misses: {cache.get('fragment_misses', 0)}",
        f"- Fragment cache writes: {cache.get('fragment_writes', 0)}",
    ])
    _append_summarizer_lines(lines, data)
    _append_delta_lines(lines, data)
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

    lines.extend(["", "## Ranked Relevant Files"])
    ranked_files = data.get("ranked_files", [])
    if ranked_files:
        for item in ranked_files:
            lines.append(f"- `{item['path']}` ({_format_ranked_file_scores(item)})")
    else:
        lines.append("- None")

    lines.append("")
    return "\n".join(lines)


def _append_heatmap_rows(lines: list[str], items: list[dict], *, runs_analyzed: int) -> None:
    if not items:
        lines.append("- None")
        return
    for item in items:
        rate = float(item.get("inclusion_rate", 0.0) or 0.0) * 100.0
        lines.append(
            "- "
            f"`{item.get('path', '')}`: "
            f"compressed={item.get('total_compressed_tokens', 0)} "
            f"original={item.get('total_original_tokens', 0)} "
            f"saved={item.get('total_saved_tokens', 0)} "
            f"included={item.get('inclusion_count', 0)}/{runs_analyzed} "
            f"rate={rate:.1f}%"
        )


def render_heatmap_markdown(data: dict) -> str:
    """Render historical token heatmap analytics to Markdown."""

    runs_analyzed = int(data.get("runs_analyzed", 0) or 0)
    lines = [
        "# ContextBudget Heatmap Report",
        "",
        f"Generated at: {data.get('generated_at', '')}",
        f"Runs analyzed: {runs_analyzed}",
        f"Unique files: {data.get('unique_files', 0)}",
        f"Unique directories: {data.get('unique_directories', 0)}",
        f"Artifacts scanned: {len(data.get('artifacts_scanned', []))}",
        f"Skipped artifacts: {len(data.get('skipped_artifacts', []))}",
        "",
        "## Top Token-Heavy Files",
    ]
    _append_heatmap_rows(lines, data.get("top_token_heavy_files", []), runs_analyzed=runs_analyzed)
    lines.extend(["", "## Top Token-Heavy Directories"])
    _append_heatmap_rows(lines, data.get("top_token_heavy_directories", []), runs_analyzed=runs_analyzed)
    lines.extend(["", "## Most Frequently Included Files"])
    _append_heatmap_rows(lines, data.get("most_frequently_included_files", []), runs_analyzed=runs_analyzed)
    lines.extend(["", "## Largest Token Savings Opportunities"])
    _append_heatmap_rows(
        lines,
        data.get("largest_token_savings_opportunities", []),
        runs_analyzed=runs_analyzed,
    )

    skipped = data.get("skipped_artifacts", [])
    if isinstance(skipped, list) and skipped:
        lines.extend(["", "## Skipped Artifacts"])
        for item in skipped:
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{item.get('artifact_path', '')}`: {item.get('reason', '')}")

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


def _format_signed_int(value: int) -> str:
    return f"{int(value):+d}"


def _format_signed_float(value: float) -> str:
    numeric = float(value)
    rounded = round(numeric, 1)
    if rounded.is_integer():
        return f"{rounded:+.0f}"
    return f"{rounded:+.1f}"


def _format_signed_percent(value: float) -> str:
    numeric = float(value)
    rounded = round(numeric, 1)
    if rounded.is_integer():
        return f"{rounded:+.0f}%"
    return f"{rounded:+.1f}%"


def _pr_audit_file_map(data: dict) -> dict[str, dict]:
    files = data.get("files", [])
    if not isinstance(files, list):
        return {}
    mapping: dict[str, dict] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if path:
            mapping[path] = item
    return mapping


def render_pr_comment_markdown(data: dict) -> str:
    """Render the concise PR comment for context-growth auditing."""

    summary = data.get("summary", {})
    files_by_path = _pr_audit_file_map(data)
    causing_increase = data.get("files_causing_increase", [])
    lines = [
        "## ContextBudget Analysis",
        "",
        f"Estimated token impact: {_format_signed_percent(float(summary.get('estimated_token_delta_pct', 0.0) or 0.0))}",
        (
            "Estimated tokens: "
            f"{summary.get('estimated_tokens_before', 0)} -> {summary.get('estimated_tokens_after', 0)} "
            f"({_format_signed_int(int(summary.get('estimated_token_delta', 0) or 0))})"
        ),
        "",
        "Files causing increase:",
    ]

    if isinstance(causing_increase, list) and causing_increase:
        for path in causing_increase:
            entry = files_by_path.get(str(path), {})
            details: list[str] = []
            token_delta = int(entry.get("token_delta", 0) or 0)
            if token_delta > 0:
                details.append(f"{_format_signed_int(token_delta)} tokens")
            new_dependencies = entry.get("new_dependencies", [])
            if isinstance(new_dependencies, list) and new_dependencies:
                details.append(f"+{len(new_dependencies)} dependencies")
            complexity_delta = float(entry.get("complexity_delta", 0.0) or 0.0)
            if complexity_delta > 0:
                details.append(f"complexity {_format_signed_float(complexity_delta)}")
            if details:
                lines.append(f"- {path} ({', '.join(details)})")
            else:
                lines.append(f"- {path}")
    else:
        lines.append("- None")

    larger_files = data.get("larger_files", [])
    new_dependencies = data.get("new_dependencies", [])
    increased_complexity = data.get("increased_complexity", [])
    if any((larger_files, new_dependencies, increased_complexity)):
        lines.extend(["", "Signals:"])
        if isinstance(larger_files, list) and larger_files:
            lines.append(f"- Larger files: {', '.join(str(item) for item in larger_files[:5])}")
        if isinstance(new_dependencies, list) and new_dependencies:
            preview = ", ".join(str(item.get("name", "")) for item in new_dependencies[:5] if isinstance(item, dict))
            if preview:
                lines.append(f"- New dependencies: {preview}")
        if isinstance(increased_complexity, list) and increased_complexity:
            lines.append(f"- Increased context complexity: {', '.join(str(item) for item in increased_complexity[:5])}")

    suggestions = data.get("suggestions", [])
    lines.extend(["", "Suggestions:"])
    if isinstance(suggestions, list) and suggestions:
        for item in suggestions:
            lines.append(f"- {item}")
    else:
        lines.append("- None")

    lines.append("")
    return "\n".join(lines)


def render_pr_audit_markdown(data: dict) -> str:
    """Render the full PR context audit artifact to Markdown."""

    summary = data.get("summary", {})
    files = data.get("files", [])
    lines = [
        "# ContextBudget PR Audit",
        "",
        f"Repository: {data.get('repo', '')}",
        f"Base ref: {data.get('base_ref', '')}",
        f"Head ref: {data.get('head_ref', '')}",
        f"Merge base: {data.get('merge_base', '')}",
        f"Generated at: {data.get('generated_at', '')}",
        "",
        "## Token Estimator",
    ]
    _append_token_estimator_lines(lines, data)
    lines.extend(
        [
            "",
            "## Summary",
            f"- Changed files: {summary.get('changed_files', 0)}",
            f"- Analyzed files: {summary.get('analyzed_files', 0)}",
            f"- Skipped files: {summary.get('skipped_files', 0)}",
            f"- Estimated tokens before: {summary.get('estimated_tokens_before', 0)}",
            f"- Estimated tokens after: {summary.get('estimated_tokens_after', 0)}",
            f"- Estimated token delta: {_format_signed_int(int(summary.get('estimated_token_delta', 0) or 0))}",
            f"- Estimated token impact: {_format_signed_percent(float(summary.get('estimated_token_delta_pct', 0.0) or 0.0))}",
            f"- Larger files: {summary.get('larger_file_count', 0)}",
            f"- New dependencies: {summary.get('new_dependency_count', 0)}",
            f"- Increased complexity: {summary.get('increased_complexity_count', 0)}",
            "",
            "## Files Causing Increase",
        ]
    )

    causing_increase = data.get("files_causing_increase", [])
    files_by_path = _pr_audit_file_map(data)
    if isinstance(causing_increase, list) and causing_increase:
        for path in causing_increase:
            entry = files_by_path.get(str(path), {})
            lines.append(
                f"- `{path}`: "
                f"tokens {_format_signed_int(int(entry.get('token_delta', 0) or 0))}, "
                f"lines {_format_signed_int(int(entry.get('line_delta', 0) or 0))}, "
                f"complexity {_format_signed_float(float(entry.get('complexity_delta', 0.0) or 0.0))}"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## File Details"])
    if isinstance(files, list) and files:
        for item in files:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("analyzed", True)):
                lines.append(
                    f"- `{item.get('path', '')}`: skipped ({item.get('skipped_reason', 'unknown')})"
                )
                continue
            lines.append(
                f"- `{item.get('path', '')}` "
                f"[{item.get('change_type', 'modified')}]: "
                f"tokens {item.get('before', {}).get('token_count', 0)} -> {item.get('after', {}).get('token_count', 0)}, "
                f"complexity {item.get('before', {}).get('complexity_score', 0)} -> {item.get('after', {}).get('complexity_score', 0)}"
            )
            new_dependencies = item.get("new_dependencies", [])
            if isinstance(new_dependencies, list) and new_dependencies:
                lines.append(f"  - new dependencies: {', '.join(str(dep) for dep in new_dependencies)}")
            reasons = item.get("growth_reasons", [])
            if isinstance(reasons, list) and reasons:
                lines.append(f"  - growth reasons: {', '.join(str(reason) for reason in reasons)}")
    else:
        lines.append("- None")

    dependencies = data.get("new_dependencies", [])
    lines.extend(["", "## New Dependencies"])
    if isinstance(dependencies, list) and dependencies:
        for item in dependencies:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('name', '')}` via {item.get('source', '')} in `{item.get('file', '')}`"
            )
    else:
        lines.append("- None")

    suggestions = data.get("suggestions", [])
    lines.extend(["", "## Suggestions"])
    if isinstance(suggestions, list) and suggestions:
        for item in suggestions:
            lines.append(f"- {item}")
    else:
        lines.append("- None")

    lines.extend(["", "## PR Comment Preview", "", render_pr_comment_markdown(data)])
    lines.append("")
    return "\n".join(lines)


def render_advise_markdown(data: dict) -> str:
    """Render context architecture advice report to Markdown."""

    summary = data.get("summary", {})
    suggestions = data.get("suggestions", [])
    lines = [
        "# ContextBudget Architecture Advice",
        "",
        f"Repository: {data.get('repo', '')}",
        f"Generated at: {data.get('generated_at', '')}",
        f"Scanned files: {data.get('scanned_files', 0)}",
        f"Runs analyzed: {data.get('runs_analyzed', 0)}",
        f"Large-file token threshold: {data.get('large_file_token_threshold', 0)}",
        f"High fan-in threshold: {data.get('high_fanin_threshold', 0)} importers",
        f"High fan-out threshold: {data.get('high_fanout_threshold', 0)} dependencies",
        "",
        "## Summary",
        f"- Total suggestions: {summary.get('total_suggestions', 0)}",
        f"- Split file: {summary.get('split_file', 0)}",
        f"- Extract module: {summary.get('extract_module', 0)}",
        f"- Reduce dependencies: {summary.get('reduce_dependencies', 0)}",
        "",
        "## Suggestions (ranked by token impact)",
    ]

    if isinstance(suggestions, list) and suggestions:
        for idx, item in enumerate(suggestions, start=1):
            if not isinstance(item, dict):
                continue
            signals = item.get("signals", [])
            signals_str = ", ".join(signals) if signals else "none"
            lines.extend([
                f"### {idx}. `{item.get('path', '')}` — {item.get('suggestion', '')}",
                f"- **Estimated token impact:** {item.get('estimated_token_impact', 0)}",
                f"- **Signals:** {signals_str}",
                f"- {item.get('reason', '')}",
                "",
            ])
    else:
        lines.append("- No suggestions. Repository structure looks agent-friendly.")
        lines.append("")

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
    if _has_model_profile(data):
        lines.extend(["", "## Model Assumptions"])
        _append_model_profile_lines(lines, data)
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


def render_profile_markdown(data: dict) -> str:
    """Render a token savings profile artifact to Markdown."""

    tokens_before = int(data.get("tokens_before") or 0)
    tokens_after = int(data.get("tokens_after") or 0)
    tokens_saved = int(data.get("tokens_saved") or 0)
    savings_pct = float(data.get("savings_pct") or 0.0)
    run_json = str(data.get("run_json") or "")

    lines = ["# ContextBudget Token Savings Profile", ""]
    if run_json:
        lines.append(f"Run artifact: {run_json}")
    lines.extend(
        [
            f"Generated at: {data.get('generated_at', '')}",
            "",
            "## Summary",
            "",
            "| Metric | Tokens |",
            "|--------|--------|",
            f"| Tokens before optimization | {tokens_before} |",
            f"| Tokens after optimization  | {tokens_after} |",
            f"| Total tokens saved         | {tokens_saved} |",
            f"| Savings                    | {savings_pct:.1f}% |",
            "",
            "## Savings by Stage",
            "",
            "| Stage | Files | Tokens Saved | % of Total Savings |",
            "|-------|-------|-------------|---------------------|",
        ]
    )

    by_stage = data.get("by_stage", {})
    any_stage_data = False
    if isinstance(by_stage, dict):
        for stage_name, stage_data in by_stage.items():
            if not isinstance(stage_data, dict):
                continue
            stage_saved = int(stage_data.get("tokens_saved") or 0)
            file_count = int(stage_data.get("file_count") or 0)
            if stage_saved == 0 and file_count == 0:
                continue
            pct_of_total = round((stage_saved / tokens_saved) * 100.0, 1) if tokens_saved > 0 else 0.0
            label = stage_name.replace("_", " ").title()
            lines.append(f"| {label} | {file_count} | {stage_saved} | {pct_of_total:.1f}% |")
            any_stage_data = True

    if not any_stage_data:
        lines.append("| — | — | 0 | 0.0% |")

    per_file = data.get("per_file", [])
    if isinstance(per_file, list) and per_file:
        lines.extend(
            [
                "",
                "## Per-File Breakdown",
                "",
                "| File | Stage | Before | After | Saved | Strategy |",
                "|------|-------|--------|-------|-------|----------|",
            ]
        )
        for record in per_file:
            if not isinstance(record, dict):
                continue
            label = str(record.get("stage") or "").replace("_", " ").title()
            chunk_strategy = str(record.get("chunk_strategy") or "")
            cache_status = str(record.get("cache_status") or "")
            strategy_cell = chunk_strategy or cache_status or "—"
            lines.append(
                f"| `{record.get('path', '')}` "
                f"| {label} "
                f"| {record.get('tokens_before', 0)} "
                f"| {record.get('tokens_after', 0)} "
                f"| {record.get('tokens_saved', 0)} "
                f"| {strategy_cell} |"
            )

    lines.append("")
    return "\n".join(lines)


def render_drift_markdown(data: dict) -> str:
    """Render a context drift report to Markdown."""

    repo = str(data.get("repo", "") or "")
    task_filter = str(data.get("task_filter", "") or "")
    window = int(data.get("window", 0) or 0)
    threshold_pct = float(data.get("threshold_pct", 10.0) or 10.0)
    entries_analyzed = int(data.get("entries_analyzed", 0) or 0)
    generated_at = str(data.get("generated_at", "") or "")

    baseline = data.get("baseline", {}) or {}
    current = data.get("current", {}) or {}
    drift = data.get("drift", {}) or {}

    token_drift = float(drift.get("token_drift_pct", 0.0) or 0.0)
    file_drift = float(drift.get("file_drift_pct", 0.0) or 0.0)
    complexity_drift = float(drift.get("complexity_drift_pct", 0.0) or 0.0)
    alert = bool(drift.get("alert", False))
    verdict = str(drift.get("verdict", "none") or "none")

    alert_badge = " **ALERT**" if alert else ""
    verdict_label = verdict.upper()

    lines = [
        "# ContextBudget Drift Report",
        "",
        f"Generated at: {generated_at}",
        f"Repository:   {repo}",
    ]
    if task_filter:
        lines.append(f"Task filter:  {task_filter}")
    lines += [
        f"Window:       {window} entries (analyzed: {entries_analyzed})",
        f"Threshold:    {threshold_pct:.1f}%",
        "",
        f"## Verdict: {verdict_label}{alert_badge}",
        "",
        "| Metric | Baseline | Current | Drift |",
        "|--------|----------|---------|-------|",
        (
            f"| Token count "
            f"| {int(baseline.get('token_count', 0) or 0):,} "
            f"| {int(current.get('token_count', 0) or 0):,} "
            f"| {token_drift:+.1f}% |"
        ),
        (
            f"| File count "
            f"| {int(baseline.get('file_count', 0) or 0)} "
            f"| {int(current.get('file_count', 0) or 0)} "
            f"| {file_drift:+.1f}% |"
        ),
        (
            f"| Complexity (tok/file) "
            f"| {float(baseline.get('complexity', 0.0) or 0.0):.1f} "
            f"| {float(current.get('complexity', 0.0) or 0.0):.1f} "
            f"| {complexity_drift:+.1f}% |"
        ),
        "",
        f"Baseline run: `{baseline.get('generated_at', '')}` — {baseline.get('task', '')}",
        f"Current run:  `{current.get('generated_at', '')}` — {current.get('task', '')}",
        "",
    ]

    contributors = data.get("top_contributors", [])
    if isinstance(contributors, list) and contributors:
        lines += [
            "## Top Contributors to Drift",
            "",
            "| File | Status | Baseline freq | Recent freq | Delta |",
            "|------|--------|--------------|-------------|-------|",
        ]
        for c in contributors:
            if not isinstance(c, dict):
                continue
            delta = float(c.get("frequency_delta", 0.0) or 0.0)
            lines.append(
                f"| `{c.get('file', '')}` "
                f"| {c.get('status', '')} "
                f"| {float(c.get('baseline_frequency', 0.0) or 0.0):.0%} "
                f"| {float(c.get('recent_frequency', 0.0) or 0.0):.0%} "
                f"| {delta:+.0%} |"
            )
        lines.append("")

    trend = data.get("trend", [])
    if isinstance(trend, list) and trend:
        lines += [
            "## Trend",
            "",
            "| # | Timestamp | Task | Tokens | Files | Complexity |",
            "|---|-----------|------|--------|-------|------------|",
        ]
        for i, point in enumerate(trend, 1):
            if not isinstance(point, dict):
                continue
            lines.append(
                f"| {i} "
                f"| {point.get('generated_at', '')[:19]} "
                f"| {str(point.get('task', ''))[:40]} "
                f"| {int(point.get('token_count', 0) or 0):,} "
                f"| {int(point.get('file_count', 0) or 0)} "
                f"| {float(point.get('complexity', 0.0) or 0.0):.1f} |"
            )
        lines.append("")

    return "\n".join(lines)


def render_pipeline_markdown(data: dict) -> str:
    """Render a full pipeline trace artifact to Markdown."""

    stages: list[dict] = data.get("stages") or []
    total_saved = int(data.get("total_tokens_saved") or 0)
    total_pct = float(data.get("total_reduction_pct") or 0.0)
    final_tokens = int(data.get("final_tokens") or 0)
    tokens_at_scan = int(data.get("tokens_at_scan") or 0)

    lines = [
        "# ContextBudget Pipeline Trace",
        "",
        f"Task: {data.get('task', '')}",
        f"Repository: {data.get('repo', '')}",
        f"Run artifact: {data.get('run_json', '')}",
        f"Generated: {data.get('generated_at', '')}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Scanned files | {data.get('scanned_files', 0):,} |",
        f"| Tokens at scan (ranked pool) | {tokens_at_scan:,} |",
        f"| Tokens after ranking | {int(data.get('tokens_after_ranking') or 0):,} |",
        f"| Tokens before pack | {int(data.get('tokens_before_pack') or 0):,} |",
        f"| Tokens after pack | {int(data.get('tokens_after_pack') or 0):,} |",
        f"| **Final context tokens** | **{final_tokens:,}** |",
        f"| Total tokens saved | {total_saved:,} |",
        f"| **Total reduction** | **{total_pct:.1f}%** |",
        f"| Cache active | {'yes' if data.get('has_cache') else 'no'} |",
        f"| Delta active | {'yes' if data.get('has_delta') else 'no'} |",
        "",
        "## Stage Breakdown",
        "",
        "| Stage | Files | Tokens In | Tokens Out | Saved | Reduction |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    OPT_INDENT = "\u00a0\u00a0\u00a0\u00bb "

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        label = stage.get("label", stage.get("name", ""))
        is_opt = bool(stage.get("is_optimisation", False))
        display_label = f"{OPT_INDENT}{label}" if is_opt else f"**{label}**"
        files = int(stage.get("files_in") or 0)
        t_in = int(stage.get("tokens_in") or 0)
        t_out = int(stage.get("tokens_out") or 0)
        t_saved = int(stage.get("tokens_saved") or 0)
        pct = float(stage.get("reduction_pct") or 0.0)
        pct_cell = f"{pct:.1f}%" if pct > 0 else "—"
        saved_cell = f"{t_saved:,}" if t_saved > 0 else "—"
        lines.append(
            f"| {display_label} | {files:,} | {t_in:,} | {t_out:,} | {saved_cell} | {pct_cell} |"
        )

    notes_rows = [s for s in stages if isinstance(s, dict) and s.get("notes")]
    if notes_rows:
        lines.extend(["", "## Stage Notes", ""])
        for stage in notes_rows:
            label = stage.get("label", stage.get("name", ""))
            lines.append(f"- **{label}**: {stage.get('notes', '')}")

    lines.append("")
    return "\n".join(lines)


def render_prepare_context_markdown(record: dict) -> str:
    """Render a prepare-context middleware result to Markdown."""

    pack_md = render_pack_markdown(record)
    mw = record.get("agent_middleware", {})
    if not isinstance(mw, dict):
        return pack_md

    meta = mw.get("metadata", {})
    if not isinstance(meta, dict):
        meta = {}

    policy_block = record.get("policy", {})
    lines = [
        pack_md,
        "",
        "## Agent Middleware",
        f"- Files included: {meta.get('files_included_count', 0)}",
        f"- Files removed: {meta.get('files_removed_count', 0)}",
        f"- Files skipped: {meta.get('files_skipped_count', 0)}",
        f"- Estimated input tokens: {meta.get('estimated_input_tokens', 0)}",
        f"- Estimated saved tokens: {meta.get('estimated_saved_tokens', 0)}",
        f"- Quality risk: {meta.get('quality_risk_estimate', 'unknown')}",
        f"- Delta enabled: {meta.get('delta_enabled', False)}",
    ]

    recorded = mw.get("recorded_path", "")
    if recorded:
        lines.append(f"- Recorded path: `{recorded}`")

    adapter = mw.get("adapter", "")
    if adapter:
        lines.append(f"- Adapter: {adapter}")

    if isinstance(policy_block, dict) and policy_block:
        passed = bool(policy_block.get("passed", True))
        lines.extend([
            "",
            "## Policy",
            f"- Result: {'PASS' if passed else 'FAIL'}",
        ])
        for v in policy_block.get("violations", []):
            lines.append(f"- Violation: {v}")

    lines.append("")
    return "\n".join(lines)


def render_read_profile_markdown(data: dict) -> str:
    """Render an agent read profile artifact to Markdown."""

    run_json = str(data.get("run_json") or "")
    lines = ["# ContextBudget Agent Read Profile", ""]
    if run_json:
        lines.append(f"Run artifact: {run_json}")
    lines.extend(
        [
            f"Generated at: {data.get('generated_at', '')}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Files read (total)                | {data.get('total_files_read', 0)} |",
            f"| Unique files read                 | {data.get('unique_files_read', 0)} |",
            f"| Duplicate reads detected          | {data.get('duplicate_reads', 0)} |",
            f"| Duplicate reads prevented (packer)| {data.get('duplicate_reads_prevented', 0)} |",
            f"| Unnecessary reads                 | {data.get('unnecessary_reads', 0)} |",
            f"| High token-cost reads             | {data.get('high_cost_reads', 0)} |",
            f"| Tokens wasted (duplicates)        | {data.get('tokens_wasted_duplicates', 0)} |",
            f"| Tokens wasted (unnecessary)       | {data.get('tokens_wasted_unnecessary', 0)} |",
            f"| Total tokens wasted               | {data.get('tokens_wasted_total', 0)} |",
            "",
        ]
    )

    # --- duplicate reads ---
    dup_files = data.get("duplicate_files", [])
    if isinstance(dup_files, list) and dup_files:
        lines.extend(
            [
                "## Duplicate Reads",
                "",
                "These files were read more than once in the same context pack.",
                "",
                "| File | Read Count | Tokens/Read | Tokens Wasted |",
                "|------|-----------|------------|---------------|",
            ]
        )
        for rec in dup_files:
            if not isinstance(rec, dict):
                continue
            lines.append(
                f"| `{rec.get('path', '')}` "
                f"| {rec.get('read_count', 1)} "
                f"| {rec.get('original_tokens', 0)} "
                f"| {rec.get('waste_tokens', 0)} |"
            )
        lines.append("")

    # --- unnecessary reads ---
    unneeded = data.get("unnecessary_files", [])
    if isinstance(unneeded, list) and unneeded:
        lines.extend(
            [
                "## Unnecessary Reads",
                "",
                "Low-relevance files with significant token cost.",
                "",
                "| File | Relevance Score | Tokens | Strategy |",
                "|------|----------------|--------|----------|",
            ]
        )
        for rec in unneeded:
            if not isinstance(rec, dict):
                continue
            chunk = str(rec.get("chunk_strategy") or rec.get("strategy") or "—")
            lines.append(
                f"| `{rec.get('path', '')}` "
                f"| {float(rec.get('relevance_score', 0.0)):.2f} "
                f"| {rec.get('original_tokens', 0)} "
                f"| {chunk} |"
            )
        lines.append("")

    # --- high-cost reads ---
    high_cost = data.get("high_cost_files", [])
    if isinstance(high_cost, list) and high_cost:
        lines.extend(
            [
                "## High Token-Cost Reads",
                "",
                "Files that individually cost the most tokens.",
                "",
                "| File | Original Tokens | Compressed Tokens | Strategy |",
                "|------|----------------|-------------------|----------|",
            ]
        )
        for rec in high_cost:
            if not isinstance(rec, dict):
                continue
            chunk = str(rec.get("chunk_strategy") or rec.get("strategy") or "—")
            lines.append(
                f"| `{rec.get('path', '')}` "
                f"| {rec.get('original_tokens', 0)} "
                f"| {rec.get('compressed_tokens', 0)} "
                f"| {chunk} |"
            )
        lines.append("")

    # --- all files ---
    all_files = data.get("files", [])
    if isinstance(all_files, list) and all_files:
        lines.extend(
            [
                "## All Files Read",
                "",
                "| File | Original Tokens | Compressed Tokens | Score | Flags |",
                "|------|----------------|-------------------|-------|-------|",
            ]
        )
        for rec in all_files:
            if not isinstance(rec, dict):
                continue
            flags = []
            if rec.get("is_duplicate"):
                flags.append("duplicate")
            if rec.get("is_unnecessary"):
                flags.append("unnecessary")
            if rec.get("is_high_cost"):
                flags.append("high-cost")
            flag_str = ", ".join(flags) if flags else "—"
            score = float(rec.get("relevance_score", 0.0) or 0.0)
            score_str = f"{score:.2f}" if score > 0.0 else "—"
            lines.append(
                f"| `{rec.get('path', '')}` "
                f"| {rec.get('original_tokens', 0)} "
                f"| {rec.get('compressed_tokens', 0)} "
                f"| {score_str} "
                f"| {flag_str} |"
            )
        lines.append("")

    return "\n".join(lines)


def render_dataset_markdown(data: dict) -> str:
    """Render a dataset report to Markdown."""

    agg = data.get("aggregate", {})
    entries = data.get("entries", [])

    lines = [
        "# ContextBudget Dataset Report",
        "",
        f"Repository: {data.get('repo', '')}",
        f"Generated at: {data.get('generated_at', '')}",
        f"Tasks: {data.get('task_count', 0)}",
        "",
        "## Aggregate",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total baseline tokens | {agg.get('total_baseline_tokens', 0)} |",
        f"| Total optimized tokens | {agg.get('total_optimized_tokens', 0)} |",
        f"| Avg baseline tokens | {agg.get('avg_baseline_tokens', 0)} |",
        f"| Avg optimized tokens | {agg.get('avg_optimized_tokens', 0)} |",
        f"| Avg reduction | {agg.get('avg_reduction_pct', 0):.1f}% |",
        "",
        "## Per-Task Results",
        "| # | Task | Baseline tokens | Optimized tokens | Reduction |",
        "|---|------|-----------------|------------------|-----------|",
    ]

    for idx, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        label = entry.get("task_name") or entry.get("task", "")
        lines.append(
            f"| {idx} | {label} "
            f"| {entry.get('baseline_tokens', 0)} "
            f"| {entry.get('optimized_tokens', 0)} "
            f"| {entry.get('reduction_pct', 0):.1f}% |"
        )

    lines.append("")
    return "\n".join(lines)
