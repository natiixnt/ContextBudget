#!/usr/bin/env python3
"""Benchmark dataset generator for Redcon.

Demonstrates token reduction on real coding tasks by running baseline context
selection vs optimised context selection and storing the delta.

Produces:
  docs/benchmarks/dataset-report.json   Full JSON artifact
  docs/benchmarks/dataset-report.md    Human-readable Markdown summary

Usage
-----
    python benchmarks/generate_dataset.py

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
from redcon.core.context_dataset_builder import (  # noqa: E402
    BUILTIN_TASKS,
    build_context_dataset,
    context_dataset_as_dict,
)
from redcon.core.dataset import DatasetTask  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_TOKENS: int = int(os.getenv("BENCHMARK_MAX_TOKENS", "8000"))
TOP_FILES: int = int(os.getenv("BENCHMARK_TOP_FILES", "20"))
OUT_DIR: Path = Path(os.getenv("BENCHMARK_OUT_DIR", str(_repo_root / "docs" / "benchmarks")))
DATASET_DIR: Path = Path(
    os.getenv("BENCHMARK_DATASET_DIR", str(_repo_root / "benchmarks" / "dataset"))
)

# The four core tasks the user requested — subset of BUILTIN_TASKS.
_TASK_NAMES = {"Add Caching", "Add Authentication", "Refactor Module", "Add Rate Limiting"}
GENERATOR_TASKS: list[DatasetTask] = [t for t in BUILTIN_TASKS if t.name in _TASK_NAMES]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def _render_markdown(report_dict: dict) -> str:
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
        "# Benchmark Dataset: Token Reduction Report",
        "",
        "Reproducible evidence of token savings when using Redcon's optimised "
        "context selection versus naive full-repository context.",
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
        baseline = int(entry.get("baseline_tokens", 0))
        optimized = int(entry.get("optimized_tokens", 0))
        reduction = float(entry.get("reduction_pct", 0.0))
        lines.append(
            f"| {name} | {baseline:,} | {optimized:,} | {_pct(reduction)} |"
        )

    lines += [
        "",
        "## Aggregate",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total baseline tokens | {total_baseline:,} |",
        f"| Total optimized tokens | {total_optimized:,} |",
        f"| Average baseline tokens | {avg_baseline:,} |",
        f"| Average optimized tokens | {avg_optimized:,} |",
        f"| **Average reduction** | **{_pct(avg_reduction)}** |",
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
            f"- **Saved:** {saved:,} tokens ({_pct(reduction)} reduction)",
            "",
        ]

    lines += [
        "## How to reproduce",
        "",
        "```bash",
        "python benchmarks/generate_dataset.py",
        "```",
        "",
        "Override settings via environment variables:",
        "",
        "```bash",
        "BENCHMARK_MAX_TOKENS=16000 BENCHMARK_TOP_FILES=30 python benchmarks/generate_dataset.py",
        "```",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')}_",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _clear_cache(dataset_dir: Path) -> None:
    """Remove the summary cache so the first run measures cold-cache performance."""
    cache_file = dataset_dir / ".redcon_cache.json"
    if cache_file.exists():
        cache_file.unlink()


def main() -> None:
    if not DATASET_DIR.exists():
        print(f"ERROR: dataset directory not found: {DATASET_DIR}", file=sys.stderr)
        sys.exit(1)

    if not GENERATOR_TASKS:
        print("ERROR: no tasks resolved from BUILTIN_TASKS", file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _clear_cache(DATASET_DIR)

    engine = RedconEngine()

    print("Redcon benchmark dataset generator")
    print(f"  dataset : {DATASET_DIR}")
    print(f"  output  : {OUT_DIR}")
    print(f"  budget  : {MAX_TOKENS:,} tokens, top {TOP_FILES} files")
    print(f"  tasks   : {len(GENERATOR_TASKS)}")
    print()

    report = build_context_dataset(
        DATASET_DIR,
        run_benchmark_fn=lambda task, repo, **kw: engine.benchmark(
            task=task,
            repo=repo,
            max_tokens=kw.get("max_tokens", MAX_TOKENS),
            top_files=kw.get("top_files", TOP_FILES),
        ),
        tasks=GENERATOR_TASKS,
        max_tokens=MAX_TOKENS,
        top_files=TOP_FILES,
    )

    report_dict = context_dataset_as_dict(
        report,
        builtin_task_count=len(GENERATOR_TASKS),
        extra_task_count=0,
    )

    # Print per-task summary to stdout
    for entry in report.entries:
        saved = entry.baseline_tokens - entry.optimized_tokens
        print(
            f"  [{entry.task_name or entry.task[:40]}] "
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

    # Write JSON artifact
    json_path = OUT_DIR / "dataset-report.json"
    json_path.write_text(json.dumps(report_dict, indent=2, default=str), encoding="utf-8")
    print(f"JSON artifact : {json_path.relative_to(_repo_root)}")

    # Write Markdown report
    md_path = OUT_DIR / "dataset-report.md"
    md_path.write_text(_render_markdown(report_dict), encoding="utf-8")
    print(f"Markdown report: {md_path.relative_to(_repo_root)}")


if __name__ == "__main__":
    main()
