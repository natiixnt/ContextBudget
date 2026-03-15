#!/usr/bin/env python3
"""Run ContextBudget benchmarks against the included dataset.

Produces one JSON artifact and one Markdown report per task inside
docs/benchmarks/, plus a combined summary table.

Usage
-----
    python benchmarks/run_benchmarks.py

Options (env vars)
------------------
    BENCHMARK_MAX_TOKENS   Token budget passed to ContextBudget (default: 8000)
    BENCHMARK_TOP_FILES    Max files to rank (default: 20)
    BENCHMARK_OUT_DIR      Output directory (default: docs/benchmarks)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow importing contextbudget from the repo root without installation.
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

from contextbudget import ContextBudgetEngine  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATASET_DIR = Path(__file__).resolve().parent / "dataset"

TASKS: list[dict] = [
    {
        "slug": "add-caching",
        "task": "Add Redis caching to task lookup endpoints to reduce database load",
        "description": (
            "Evaluates how well ContextBudget selects the task service, "
            "route handlers, and repository layer when the goal is to "
            "introduce a caching layer."
        ),
    },
    {
        "slug": "add-authentication",
        "task": "Add JWT authentication middleware to protect task and user API routes",
        "description": (
            "Evaluates context selection for an auth-focused change "
            "spanning route handlers, user model, and application bootstrap."
        ),
    },
    {
        "slug": "refactor-module",
        "task": "Refactor database repository layer to use connection pooling",
        "description": (
            "Evaluates selection accuracy when the primary change targets "
            "the database connection module and its callers across services."
        ),
    },
]

MAX_TOKENS: int = int(os.getenv("BENCHMARK_MAX_TOKENS", "8000"))
TOP_FILES: int = int(os.getenv("BENCHMARK_TOP_FILES", "20"))
OUT_DIR: Path = Path(os.getenv("BENCHMARK_OUT_DIR", str(_repo_root / "docs" / "benchmarks")))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(part: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{part / total * 100:.1f}%"


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


def _render_report(result: dict, task_meta: dict) -> str:
    task_str = result.get("task", "")
    baseline = result.get("baseline_full_context_tokens", 0)
    strategies = result.get("strategies", [])
    generated = result.get("generated_at", "")
    estimator = (result.get("token_estimator") or {}).get("backend", "heuristic")
    scan_ms = result.get("scan_runtime_ms", 0)

    compressed = next((s for s in strategies if s["strategy"] == "compressed_pack"), None)
    cached = next((s for s in strategies if s["strategy"] == "cache_assisted_pack"), None)

    lines: list[str] = [
        f"# Benchmark: {task_meta['slug']}",
        "",
        f"> **Task:** {task_str}",
        "",
        f"{task_meta['description']}",
        "",
        "## Settings",
        "",
        f"| Parameter | Value |",
        f"|-----------|-------|",
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
            f"- **Input tokens:** {c_tokens:,} ({_pct(c_tokens, baseline)} of baseline)",
            f"- **Saved tokens:** {c_saved:,} ({_pct(c_saved, baseline)} reduction)",
            f"- **Quality risk:** {c_risk}",
            f"- **Files included:** {c_files}",
        ]
        if compressed.get("files_included"):
            lines.append("")
            lines.append("### Files included in packed context")
            lines.append("")
            for f in sorted(compressed["files_included"]):
                lines.append(f"- `{f}`")
        if compressed.get("files_skipped"):
            lines.append("")
            lines.append("### Files skipped")
            lines.append("")
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

    # Token estimator comparison
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


def _render_summary(all_results: list[tuple[dict, dict]]) -> str:
    lines = [
        "# Benchmark Summary",
        "",
        "Results from running ContextBudget against the included dataset "
        f"(token budget: {MAX_TOKENS:,}, top files: {TOP_FILES}).",
        "",
        "| Task | Baseline tokens | Compressed tokens | Reduction | Quality risk |",
        "|------|----------------|-------------------|-----------|--------------|",
    ]
    for result, meta in all_results:
        baseline = result.get("baseline_full_context_tokens", 0)
        strategies = result.get("strategies", [])
        compressed = next((s for s in strategies if s["strategy"] == "compressed_pack"), {})
        c_tokens = compressed.get("estimated_input_tokens", 0)
        c_saved = compressed.get("estimated_saved_tokens", 0)
        c_risk = compressed.get("quality_risk_estimate", "-")
        slug = meta["slug"]
        lines.append(
            f"| [{slug}](./{slug}.md) "
            f"| {baseline:,} "
            f"| {c_tokens:,} "
            f"| {_pct(c_saved, baseline)} "
            f"| {c_risk} |"
        )
    lines += [
        "",
        "## How to reproduce",
        "",
        "```bash",
        "python benchmarks/run_benchmarks.py",
        "```",
        "",
        "Or run a single task via the CLI:",
        "",
        "```bash",
        'contextbudget benchmark "Add Redis caching to task lookup endpoints" \\',
        f"    --repo benchmarks/dataset --max-tokens {MAX_TOKENS}",
        "```",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')}_",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _clear_dataset_cache() -> None:
    """Remove the ContextBudget summary cache from the dataset directory.

    This ensures the first compressed_pack measurement reflects a cold-cache
    run, producing reproducible token counts across repeated invocations.
    The cache-assisted row still shows warm-cache behaviour within the same
    run (two consecutive pack calls on the same task).
    """
    cache_file = DATASET_DIR / ".contextbudget_cache.json"
    if cache_file.exists():
        cache_file.unlink()


def main() -> None:
    if not DATASET_DIR.exists():
        print(f"ERROR: dataset directory not found: {DATASET_DIR}", file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _clear_dataset_cache()

    engine = ContextBudgetEngine()
    all_results: list[tuple[dict, dict]] = []

    print(f"ContextBudget benchmark runner")
    print(f"  dataset : {DATASET_DIR}")
    print(f"  output  : {OUT_DIR}")
    print(f"  budget  : {MAX_TOKENS:,} tokens, top {TOP_FILES} files")
    print()

    for meta in TASKS:
        slug = meta["slug"]
        task = meta["task"]
        print(f"[{slug}] running benchmark …")

        result = engine.benchmark(
            task=task,
            repo=DATASET_DIR,
            max_tokens=MAX_TOKENS,
            top_files=TOP_FILES,
        )

        # Write JSON artifact
        json_path = OUT_DIR / f"{slug}.json"
        json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

        # Write Markdown report
        md_path = OUT_DIR / f"{slug}.md"
        md_path.write_text(_render_report(result, meta), encoding="utf-8")

        strategies = result.get("strategies", [])
        baseline = result.get("baseline_full_context_tokens", 0)
        compressed = next((s for s in strategies if s["strategy"] == "compressed_pack"), {})
        c_tokens = compressed.get("estimated_input_tokens", 0)
        c_saved = compressed.get("estimated_saved_tokens", 0)
        risk = compressed.get("quality_risk_estimate", "-")

        print(
            f"  baseline={baseline:,}  compressed={c_tokens:,}  "
            f"saved={c_saved:,} ({_pct(c_saved, baseline)})  risk={risk}"
        )
        print(f"  → {md_path.relative_to(_repo_root)}")

        all_results.append((result, meta))

    # Write combined summary
    summary_path = OUT_DIR / "README.md"
    summary_path.write_text(_render_summary(all_results), encoding="utf-8")
    print()
    print(f"Summary written to {summary_path.relative_to(_repo_root)}")


if __name__ == "__main__":
    main()
