#!/usr/bin/env python3
"""Agent run benchmark dataset builder for Redcon.

Measures token reduction across four canonical agent run scenarios by comparing
full-repository baseline context against Redcon's optimised compressed-pack
selection.  Produces both per-task artifacts and an aggregated dataset report.

Output artifacts (all under docs/benchmarks/)
---------------------------------------------
  add-caching.json / .md
  add-authentication.json / .md
  refactor-module.json / .md
  add-rate-limiting.json / .md
  agent-run-dataset-report.json
  agent-run-dataset-report.md

Usage
-----
    python benchmarks/build_agent_run_dataset.py

Options (env vars)
------------------
    BENCHMARK_MAX_TOKENS   Token budget for optimised run (default: 8000)
    BENCHMARK_TOP_FILES    Max files ranked per task (default: 20)
    BENCHMARK_OUT_DIR      Output directory (default: docs/benchmarks)
    BENCHMARK_DATASET_DIR  Repo to benchmark against (default: benchmarks/dataset)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

from redcon import RedconEngine  # noqa: E402
from redcon.core.agent_run_dataset_builder import (  # noqa: E402
    AGENT_RUN_TASKS,
    agent_run_dataset_as_dict,
)
from redcon.core.dataset import (  # noqa: E402
    DatasetEntry,
    DatasetReport,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_TOKENS: int = int(os.getenv("BENCHMARK_MAX_TOKENS", "8000"))
TOP_FILES: int = int(os.getenv("BENCHMARK_TOP_FILES", "20"))
OUT_DIR: Path = Path(os.getenv("BENCHMARK_OUT_DIR", str(_repo_root / "docs" / "benchmarks")))
DATASET_DIR: Path = Path(
    os.getenv("BENCHMARK_DATASET_DIR", str(_repo_root / "benchmarks" / "dataset"))
)

# Slug map for per-task file names (task name → file slug)
_SLUG: dict[str, str] = {
    "Add Caching": "add-caching",
    "Add Authentication": "add-authentication",
    "Refactor Module": "refactor-module",
    "Add Rate Limiting": "add-rate-limiting",
}

# Per-task descriptions used in Markdown reports
_TASK_DESCRIPTIONS: dict[str, str] = {
    "Add Caching": (
        "Evaluates how well Redcon selects the task service, "
        "route handlers, and repository layer when the goal is to "
        "introduce a caching layer."
    ),
    "Add Authentication": (
        "Evaluates context selection for an auth-focused change "
        "spanning route handlers, user model, and application bootstrap."
    ),
    "Refactor Module": (
        "Evaluates selection accuracy when the primary change targets "
        "the database connection module and its callers across services."
    ),
    "Add Rate Limiting": (
        "Evaluates context selection for rate-limiting middleware "
        "spanning route handlers, application bootstrap, and configuration."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{part / total * 100:.1f}%"


def _pct_float(value: float) -> str:
    return f"{value:.1f}%"


def _strategy_row(strategy: dict, baseline: int) -> str:
    name = strategy["strategy"]
    input_tok = strategy["estimated_input_tokens"]
    saved = strategy["estimated_saved_tokens"]
    risk = strategy["quality_risk_estimate"]
    runtime = strategy.get("runtime_ms", 0)
    return (
        f"| {name} "
        f"| {input_tok:,} "
        f"| {saved:,} ({_pct(saved, baseline)}) "
        f"| {risk} "
        f"| {runtime} ms |"
    )


# ---------------------------------------------------------------------------
# Per-task Markdown renderer
# ---------------------------------------------------------------------------


def _render_task_report(result: dict, task_name: str) -> str:
    slug = _SLUG.get(task_name, task_name.lower().replace(" ", "-"))
    description = _TASK_DESCRIPTIONS.get(task_name, "")
    task_str = result.get("task", "")
    baseline = result.get("baseline_full_context_tokens", 0)
    strategies = result.get("strategies", [])
    generated = result.get("generated_at", "")
    estimator = (result.get("token_estimator") or {}).get("backend", "heuristic")
    scan_ms = result.get("scan_runtime_ms", 0)

    compressed = next((s for s in strategies if s["strategy"] == "compressed_pack"), None)
    cached = next((s for s in strategies if s["strategy"] == "cache_assisted_pack"), None)

    lines: list[str] = [
        f"# Agent Run Benchmark: {slug}",
        "",
        f"> **Task:** {task_str}",
        "",
        description,
        "",
        "## Settings",
        "",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Token budget | {MAX_TOKENS:,} |",
        f"| Top files | {TOP_FILES} |",
        f"| Token estimator | {estimator} |",
        f"| Scan runtime | {scan_ms} ms |",
        f"| Generated | {generated} |",
        "",
        "## Baseline",
        "",
        f"Full repository context (no selection, no compression): **{baseline:,} tokens**",
        "",
        "## Strategy comparison",
        "",
        "| Strategy | Input tokens | Saved tokens | Quality risk | Runtime |",
        "|----------|-------------|--------------|--------------|---------|",
    ]
    for s in strategies:
        lines.append(_strategy_row(s, baseline))
    lines.append("")

    if compressed:
        c_tokens = compressed["estimated_input_tokens"]
        c_saved = compressed["estimated_saved_tokens"]
        c_risk = compressed["quality_risk_estimate"]
        c_files = len(compressed.get("files_included", []))
        lines += [
            "## Compressed pack details",
            "",
            f"- **Baseline tokens:** {baseline:,}",
            f"- **Optimized tokens:** {c_tokens:,} ({_pct(c_tokens, baseline)} of baseline)",
            f"- **Saved tokens:** {c_saved:,} ({_pct(c_saved, baseline)} reduction)",
            f"- **Quality risk:** {c_risk}",
            f"- **Files included:** {c_files}",
        ]
        if compressed.get("files_included"):
            lines += ["", "### Files included in packed context", ""]
            for f in sorted(compressed["files_included"]):
                lines.append(f"- `{f}`")
        if compressed.get("files_skipped"):
            lines += ["", "### Files skipped", ""]
            for f in sorted(compressed["files_skipped"]):
                lines.append(f"- `{f}`")
        lines.append("")

    if cached:
        lines += [
            "## Cache-assisted pack",
            "",
            f"Second run (warm cache): **{cached['estimated_input_tokens']:,} tokens**, "
            f"{cached.get('cache_hits', 0)} cache hits, "
            f"{cached.get('runtime_ms', 0)} ms",
            "",
        ]

    samples = result.get("estimator_samples", [])
    if samples:
        lines += [
            "## Token estimator comparison",
            "",
            "| Sample | heuristic | model_aligned | exact_tiktoken |",
            "|--------|-----------|---------------|----------------|",
        ]
        for s in samples:
            if not s.get("name"):
                continue
            estimators: dict[str, str] = {}
            for est in s.get("estimators", []):
                backend = est.get("backend", "")
                tokens = est.get("estimated_tokens")
                fallback = " *(fallback)*" if est.get("fallback_used") else ""
                estimators[backend] = f"{tokens}{fallback}" if tokens is not None else "-"
            h = estimators.get("heuristic", "-")
            m = estimators.get("model_aligned", "-")
            e = estimators.get("exact_tiktoken", "-")
            lines.append(f"| {s['name']} | {h} | {m} | {e} |")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dataset report Markdown renderer
# ---------------------------------------------------------------------------


def _render_dataset_report(report_dict: dict) -> str:
    generated = report_dict.get("generated_at", "")
    repo = report_dict.get("repo", "")
    agg = report_dict.get("aggregate", {})
    entries = report_dict.get("entries", [])

    avg_baseline = int(agg.get("avg_baseline_tokens", 0))
    avg_optimized = int(agg.get("avg_optimized_tokens", 0))
    avg_reduction = float(agg.get("avg_reduction_pct", 0.0))
    total_baseline = int(agg.get("total_baseline_tokens", 0))
    total_optimized = int(agg.get("total_optimized_tokens", 0))

    lines: list[str] = [
        "# Agent Run Benchmark Dataset: Token Reduction Report",
        "",
        "Reproducible evidence of token savings when using Redcon's optimised "
        "context selection versus naive full-repository context across four canonical "
        "agent run scenarios.",
        "",
        "## Settings",
        "",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Token budget | {MAX_TOKENS:,} |",
        f"| Top files | {TOP_FILES} |",
        f"| Dataset repo | `{repo}` |",
        f"| Generated | {generated} |",
        "",
        "## Results",
        "",
        "| Task | Baseline tokens | Optimized tokens | Reduction |",
        "|------|----------------|-----------------|-----------|",
    ]

    for entry in entries:
        name = entry.get("task_name") or entry.get("task", "")[:50]
        slug = _SLUG.get(name, name.lower().replace(" ", "-"))
        baseline = int(entry.get("baseline_tokens", 0))
        optimized = int(entry.get("optimized_tokens", 0))
        reduction = float(entry.get("reduction_pct", 0.0))
        lines.append(
            f"| [{name}](./{slug}.md) | {baseline:,} | {optimized:,} | {_pct_float(reduction)} |"
        )

    lines += [
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total baseline tokens | {total_baseline:,} |",
        f"| Total optimized tokens | {total_optimized:,} |",
        f"| Average baseline tokens | {avg_baseline:,} |",
        f"| Average optimized tokens | {avg_optimized:,} |",
        f"| **Average reduction** | **{_pct_float(avg_reduction)}** |",
        "",
        "## Task descriptions",
        "",
    ]

    for entry in entries:
        name = entry.get("task_name") or "Unnamed"
        task_desc = entry.get("task", "")
        baseline = int(entry.get("baseline_tokens", 0))
        optimized = int(entry.get("optimized_tokens", 0))
        reduction = float(entry.get("reduction_pct", 0.0))
        saved = baseline - optimized
        lines += [
            f"### {name}",
            "",
            f"> {task_desc}",
            "",
            f"- **Baseline:** {baseline:,} tokens (full repo, no selection)",
            f"- **Optimized:** {optimized:,} tokens (Redcon compressed pack)",
            f"- **Saved:** {saved:,} tokens ({_pct_float(reduction)} reduction)",
            "",
        ]

    lines += [
        "## How to reproduce",
        "",
        "```bash",
        "python benchmarks/build_agent_run_dataset.py",
        "```",
        "",
        "Override settings via environment variables:",
        "",
        "```bash",
        "BENCHMARK_MAX_TOKENS=16000 BENCHMARK_TOP_FILES=30 python benchmarks/build_agent_run_dataset.py",
        "```",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')}_",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _clear_cache(dataset_dir: Path) -> None:
    cache_file = dataset_dir / ".redcon_cache.json"
    if cache_file.exists():
        cache_file.unlink()


def _build_report_from_results(
    task_results: list[tuple[str, str, dict]],
    repo: Path,
) -> DatasetReport:
    """Build a DatasetReport from already-collected benchmark results.

    Parameters
    ----------
    task_results:
        List of ``(task_name, task_description, benchmark_dict)`` tuples.
    repo:
        Repository path used for the benchmarks.
    """
    entries: list[DatasetEntry] = []
    for task_name, task_desc, bm in task_results:
        baseline = int(bm.get("baseline_full_context_tokens", 0) or 0)
        strategies = bm.get("strategies", [])
        compressed = next((s for s in strategies if s["strategy"] == "compressed_pack"), {})
        optimized = int(compressed.get("estimated_input_tokens", baseline) or baseline)
        reduction = round(max(0.0, (baseline - optimized) / baseline * 100), 2) if baseline else 0.0
        entries.append(DatasetEntry(
            task=task_desc,
            task_name=task_name,
            baseline_tokens=baseline,
            optimized_tokens=optimized,
            reduction_pct=reduction,
            benchmark=bm,
        ))

    n = len(entries)
    total_baseline = sum(e.baseline_tokens for e in entries)
    total_optimized = sum(e.optimized_tokens for e in entries)
    avg_baseline = round(total_baseline / n, 2) if n else 0.0
    avg_optimized = round(total_optimized / n, 2) if n else 0.0
    avg_reduction = round(sum(e.reduction_pct for e in entries) / n, 2) if n else 0.0

    return DatasetReport(
        command="agent_run_dataset",
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo=str(repo),
        task_count=n,
        aggregate={
            "total_baseline_tokens": total_baseline,
            "total_optimized_tokens": total_optimized,
            "avg_baseline_tokens": avg_baseline,
            "avg_optimized_tokens": avg_optimized,
            "avg_reduction_pct": avg_reduction,
        },
        entries=entries,
    )


def main() -> None:
    if not DATASET_DIR.exists():
        print(f"ERROR: dataset directory not found: {DATASET_DIR}", file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _clear_cache(DATASET_DIR)

    engine = RedconEngine()

    print("Redcon agent run benchmark dataset builder")
    print(f"  dataset : {DATASET_DIR}")
    print(f"  output  : {OUT_DIR}")
    print(f"  budget  : {MAX_TOKENS:,} tokens, top {TOP_FILES} files")
    print(f"  tasks   : {len(AGENT_RUN_TASKS)}")
    print()

    # --- Per-task artifacts -------------------------------------------
    collected: list[tuple[str, str, dict]] = []  # (name, description, benchmark_dict)

    for task in AGENT_RUN_TASKS:
        slug = _SLUG.get(task.name, task.name.lower().replace(" ", "-"))
        print(f"[{slug}] running benchmark …")

        result = engine.benchmark(
            task=task.description,
            repo=DATASET_DIR,
            max_tokens=MAX_TOKENS,
            top_files=TOP_FILES,
        )
        collected.append((task.name, task.description, result))

        json_path = OUT_DIR / f"{slug}.json"
        json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

        md_path = OUT_DIR / f"{slug}.md"
        md_path.write_text(_render_task_report(result, task.name), encoding="utf-8")

        strategies = result.get("strategies", [])
        baseline = result.get("baseline_full_context_tokens", 0)
        compressed = next((s for s in strategies if s["strategy"] == "compressed_pack"), {})
        c_tokens = compressed.get("estimated_input_tokens", 0)
        c_saved = compressed.get("estimated_saved_tokens", 0)
        risk = compressed.get("quality_risk_estimate", "-")
        print(
            f"  baseline={baseline:,}  optimized={c_tokens:,}  "
            f"saved={c_saved:,} ({_pct(c_saved, baseline)})  risk={risk}"
        )
        print(f"  → {md_path.relative_to(_repo_root)}")

    print()

    # --- Aggregated dataset report (built from collected results) ----
    report = _build_report_from_results(collected, DATASET_DIR)
    report_dict = agent_run_dataset_as_dict(report, task_count=len(AGENT_RUN_TASKS))

    print("Aggregated results:")
    for entry in report.entries:
        saved = entry.baseline_tokens - entry.optimized_tokens
        print(
            f"  [{entry.task_name}] "
            f"baseline={entry.baseline_tokens:,}  "
            f"optimized={entry.optimized_tokens:,}  "
            f"saved={saved:,} ({entry.reduction_pct:.1f}%)"
        )

    agg = report_dict["aggregate"]
    print()
    print(
        f"  Average reduction: {agg['avg_reduction_pct']:.1f}%  "
        f"({int(agg['avg_baseline_tokens']):,} → {int(agg['avg_optimized_tokens']):,} tokens)"
    )
    print()

    json_report_path = OUT_DIR / "agent-run-dataset-report.json"
    json_report_path.write_text(
        json.dumps(report_dict, indent=2, default=str), encoding="utf-8"
    )
    print(f"JSON report   : {json_report_path.relative_to(_repo_root)}")

    md_report_path = OUT_DIR / "agent-run-dataset-report.md"
    md_report_path.write_text(_render_dataset_report(report_dict), encoding="utf-8")
    print(f"Markdown report: {md_report_path.relative_to(_repo_root)}")


if __name__ == "__main__":
    main()
