#!/usr/bin/env python3
"""Run the large-scale Redcon benchmark across 1000 tasks.

Usage
-----
    # Run full 1000-task suite (takes ~10 min):
    python redcon-benchmarks/run_large_benchmark.py

    # Run a random sample of N tasks:
    python redcon-benchmarks/run_large_benchmark.py --sample 100

    # Run tasks from a specific category only:
    python redcon-benchmarks/run_large_benchmark.py --category caching

    # Use a custom repo for benchmarking:
    python redcon-benchmarks/run_large_benchmark.py --repo /path/to/your/repo

Results are written to:
    redcon-benchmarks/results/large-benchmark-TIMESTAMP.json
    redcon-benchmarks/results/large-benchmark-TIMESTAMP-summary.md

The benchmark runs against the Redcon repository itself by default.
Any repository with Python, TypeScript, Go, or Rust files works.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, quantiles, stdev
from typing import Any

# Resolve repo root so the script works from any cwd
REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from redcon.core.benchmark import run_benchmark  # noqa: E402

TASKS_FILE = Path(__file__).parent / "corpus" / "tasks-1000.toml"
RESULTS_DIR = Path(__file__).parent / "results"

_STRATEGIES = ["naive_full_context", "top_k_selection", "compressed_pack", "cache_assisted_pack"]


# ---------------------------------------------------------------------------
# TOML loading (stdlib tomllib Python 3.11+, else inline parser)
# ---------------------------------------------------------------------------

def _load_tasks_toml(path: Path) -> list[dict]:
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            # Fallback: simple line-by-line parser for our known format
            return _parse_tasks_toml_fallback(path)
    return tomllib.loads(path.read_text(encoding="utf-8")).get("tasks", [])


def _parse_tasks_toml_fallback(path: Path) -> list[dict]:
    """Minimal TOML parser for the tasks-1000.toml format."""
    tasks: list[dict] = []
    current: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line == "[[tasks]]":
            if current:
                tasks.append(current)
            current = {}
        elif line.startswith("id = "):
            current["id"] = int(line[5:])
        elif line.startswith("category = "):
            current["category"] = line[12:].strip('"')
        elif line.startswith('task = "'):
            current["task"] = line[8:].rstrip('"').replace('\\"', '"')
    if current:
        tasks.append(current)
    return tasks


# ---------------------------------------------------------------------------
# Single-task benchmark
# ---------------------------------------------------------------------------

def _run_one(task: str, repo: Path, max_tokens: int, top_files: int) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        result = run_benchmark(task=task, repo=repo, max_tokens=max_tokens, top_files=top_files)
    except Exception as exc:
        return {"error": str(exc), "task": task, "elapsed_ms": 0}
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    out: dict[str, Any] = {
        "task": task,
        "elapsed_ms": elapsed_ms,
        "baseline_tokens": result.get("baseline_full_context_tokens", 0),
    }
    for s in result.get("strategies", []):
        name = s.get("strategy", "")
        if name:
            out[name] = {
                "tokens": s.get("estimated_input_tokens", 0),
                "saved": s.get("estimated_saved_tokens", 0),
                "risk": s.get("quality_risk_estimate", "unknown"),
                "cache_hits": s.get("cache_hits", 0),
                "runtime_ms": s.get("runtime_ms", 0),
            }
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _pct(value: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return round((value / baseline) * 100, 1)


def _percentiles(data: list[float]) -> dict[str, float]:
    if not data:
        return {"p10": 0, "p25": 0, "p50": 0, "p75": 0, "p90": 0, "p95": 0, "p99": 0}
    qs = quantiles(data, n=100)
    return {
        "p10": round(qs[9], 1),
        "p25": round(qs[24], 1),
        "p50": round(qs[49], 1),
        "p75": round(qs[74], 1),
        "p90": round(qs[89], 1),
        "p95": round(qs[94], 1),
        "p99": round(qs[98], 1),
    }


def _aggregate(runs: list[dict]) -> dict[str, Any]:
    ok = [r for r in runs if "error" not in r]
    errors = len(runs) - len(ok)

    baselines = [r["baseline_tokens"] for r in ok if r.get("baseline_tokens", 0) > 0]
    avg_baseline = round(mean(baselines)) if baselines else 0

    per_strategy: dict[str, Any] = {}
    for strat in _STRATEGIES:
        tokens_list = [r[strat]["tokens"] for r in ok if strat in r]
        saved_list = [r[strat]["saved"] for r in ok if strat in r]
        runtime_list = [r[strat]["runtime_ms"] for r in ok if strat in r]
        if not tokens_list:
            continue
        savings_pct_list = [
            (s / (t + s) * 100) if (t + s) > 0 else 0
            for t, s in zip(tokens_list, saved_list)
        ]
        risks = [r[strat]["risk"] for r in ok if strat in r]
        risk_counts = {level: risks.count(level) for level in ("low", "medium", "high", "unknown")}
        per_strategy[strat] = {
            "n": len(tokens_list),
            "mean_tokens": round(mean(tokens_list)),
            "median_tokens": round(median(tokens_list)),
            "mean_saved": round(mean(saved_list)),
            "mean_savings_pct": round(mean(savings_pct_list), 1),
            "median_savings_pct": round(median(savings_pct_list), 1),
            "tokens_percentiles": _percentiles(list(map(float, tokens_list))),
            "savings_pct_percentiles": _percentiles(savings_pct_list),
            "mean_runtime_ms": round(mean(runtime_list), 1),
            "risk_distribution": risk_counts,
        }

    per_category: dict[str, dict] = {}
    for run in ok:
        cat = run.get("category", "unknown")
        if cat not in per_category:
            per_category[cat] = {"n": 0, "compressed_savings_pct": [], "cache_savings_pct": []}
        per_category[cat]["n"] += 1
        if "compressed_pack" in run:
            t, s = run["compressed_pack"]["tokens"], run["compressed_pack"]["saved"]
            if t + s > 0:
                per_category[cat]["compressed_savings_pct"].append(s / (t + s) * 100)
        if "cache_assisted_pack" in run:
            t, s = run["cache_assisted_pack"]["tokens"], run["cache_assisted_pack"]["saved"]
            if t + s > 0:
                per_category[cat]["cache_savings_pct"].append(s / (t + s) * 100)

    cat_summary = {}
    for cat, data in per_category.items():
        cp = data["compressed_savings_pct"]
        ca = data["cache_savings_pct"]
        cat_summary[cat] = {
            "n": data["n"],
            "compressed_mean_savings_pct": round(mean(cp), 1) if cp else 0,
            "cache_mean_savings_pct": round(mean(ca), 1) if ca else 0,
        }

    elapsed_list = [r["elapsed_ms"] for r in ok]
    return {
        "total_tasks": len(runs),
        "successful": len(ok),
        "errors": errors,
        "avg_baseline_tokens": avg_baseline,
        "strategies": per_strategy,
        "by_category": cat_summary,
        "elapsed_ms": {
            "mean": round(mean(elapsed_list), 1) if elapsed_list else 0,
            "median": round(median(elapsed_list), 1) if elapsed_list else 0,
            "p95": _percentiles(list(map(float, elapsed_list)))["p95"] if elapsed_list else 0,
        },
    }


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------

def _render_summary(agg: dict, *, repo: str, n_tasks: int, duration_s: float) -> str:
    strats = agg["strategies"]
    cp = strats.get("compressed_pack", {})
    ca = strats.get("cache_assisted_pack", {})
    cats = agg["by_category"]

    lines = [
        "# Redcon Large-Scale Benchmark",
        "",
        f"**{n_tasks} tasks** across **{len(agg['by_category'])} categories** — "
        f"repo: `{repo}` — "
        f"duration: {duration_s:.0f}s",
        "",
        "---",
        "",
        "## Overall results",
        "",
        "| Metric | compressed_pack | cache_assisted_pack |",
        "|--------|----------------|---------------------|",
        f"| Mean tokens | {cp.get('mean_tokens', 0):,} | {ca.get('mean_tokens', 0):,} |",
        f"| Median tokens | {cp.get('median_tokens', 0):,} | {ca.get('median_tokens', 0):,} |",
        f"| Mean savings | {cp.get('mean_savings_pct', 0):.1f}% | {ca.get('mean_savings_pct', 0):.1f}% |",
        f"| Median savings | {cp.get('median_savings_pct', 0):.1f}% | {ca.get('median_savings_pct', 0):.1f}% |",
        f"| Baseline (avg) | {agg['avg_baseline_tokens']:,} | {agg['avg_baseline_tokens']:,} |",
        f"| Mean runtime | {cp.get('mean_runtime_ms', 0):.0f}ms | {ca.get('mean_runtime_ms', 0):.0f}ms |",
        "",
        "## Savings percentiles (compressed_pack)",
        "",
        "| p10 | p25 | p50 | p75 | p90 | p95 | p99 |",
        "|-----|-----|-----|-----|-----|-----|-----|",
    ]
    sp = cp.get("savings_pct_percentiles", {})
    lines.append(
        f"| {sp.get('p10', 0):.1f}% | {sp.get('p25', 0):.1f}% | "
        f"{sp.get('p50', 0):.1f}% | {sp.get('p75', 0):.1f}% | "
        f"{sp.get('p90', 0):.1f}% | {sp.get('p95', 0):.1f}% | "
        f"{sp.get('p99', 0):.1f}% |"
    )
    lines += [
        "",
        "## Savings percentiles (cache_assisted_pack)",
        "",
        "| p10 | p25 | p50 | p75 | p90 | p95 | p99 |",
        "|-----|-----|-----|-----|-----|-----|-----|",
    ]
    sp2 = ca.get("savings_pct_percentiles", {})
    lines.append(
        f"| {sp2.get('p10', 0):.1f}% | {sp2.get('p25', 0):.1f}% | "
        f"{sp2.get('p50', 0):.1f}% | {sp2.get('p75', 0):.1f}% | "
        f"{sp2.get('p90', 0):.1f}% | {sp2.get('p95', 0):.1f}% | "
        f"{sp2.get('p99', 0):.1f}% |"
    )

    # Quality risk for compressed_pack
    risk = cp.get("risk_distribution", {})
    n_cp = cp.get("n", 1) or 1
    lines += [
        "",
        "## Quality risk distribution (compressed_pack)",
        "",
        "| low | medium | high |",
        "|-----|--------|------|",
        f"| {risk.get('low', 0)/n_cp*100:.1f}% | "
        f"{risk.get('medium', 0)/n_cp*100:.1f}% | "
        f"{risk.get('high', 0)/n_cp*100:.1f}% |",
        "",
        "## Results by category",
        "",
        "| Category | n | compressed savings | cache savings |",
        "|----------|---|--------------------|---------------|",
    ]
    for cat, data in sorted(cats.items(), key=lambda x: -x[1]["compressed_mean_savings_pct"]):
        lines.append(
            f"| {cat} | {data['n']} "
            f"| {data['compressed_mean_savings_pct']:.1f}% "
            f"| {data['cache_mean_savings_pct']:.1f}% |"
        )

    lines += [
        "",
        "---",
        "",
        f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')} — Redcon v1.1.0 — "
        f"{n_tasks} tasks, {agg['successful']} succeeded, {agg['errors']} errors*",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run large-scale Redcon benchmark")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="Repository path to benchmark")
    parser.add_argument("--sample", type=int, default=0, help="Run a random sample of N tasks (0 = all)")
    parser.add_argument("--category", default="", help="Filter to a specific category")
    parser.add_argument("--max-tokens", type=int, default=32_000, help="Token budget per task")
    parser.add_argument("--top-files", type=int, default=30, help="Top files per task")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for sampling")
    parser.add_argument("--progress", action="store_true", default=True, help="Show progress")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.exists():
        print(f"error: repo not found: {repo}", file=sys.stderr)
        sys.exit(1)

    # Load tasks
    tasks = _load_tasks_toml(TASKS_FILE)
    print(f"Loaded {len(tasks)} tasks from {TASKS_FILE.name}")

    if args.category:
        tasks = [t for t in tasks if t.get("category") == args.category]
        print(f"Filtered to category '{args.category}': {len(tasks)} tasks")

    if args.sample and args.sample < len(tasks):
        random.seed(args.seed)
        tasks = random.sample(tasks, args.sample)
        print(f"Sampled {len(tasks)} tasks")

    print(f"Benchmarking against: {repo}")
    print(f"max_tokens={args.max_tokens}  top_files={args.top_files}")
    print()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    json_path = RESULTS_DIR / f"large-benchmark-{ts}.json"
    md_path = RESULTS_DIR / f"large-benchmark-{ts}-summary.md"

    runs: list[dict] = []
    t_start = time.perf_counter()

    for i, entry in enumerate(tasks, 1):
        task_text = entry["task"]
        category = entry.get("category", "unknown")

        result = _run_one(
            task=task_text,
            repo=repo,
            max_tokens=args.max_tokens,
            top_files=args.top_files,
        )
        result["id"] = entry.get("id", i)
        result["category"] = category

        runs.append(result)

        if args.progress:
            n = len(tasks)
            pct = i / n * 100
            elapsed = time.perf_counter() - t_start
            eta = (elapsed / i) * (n - i) if i > 0 else 0
            cp_savings = ""
            if "compressed_pack" in result:
                s = result["compressed_pack"]
                t_tok, saved = s["tokens"], s["saved"]
                if t_tok + saved > 0:
                    cp_savings = f"  savings={saved/(t_tok+saved)*100:.0f}%"
            err = "  [error]" if "error" in result else ""
            print(
                f"  [{i:4d}/{n}] {pct:5.1f}%  ETA={eta:.0f}s"
                f"  {category[:18]:18s}  {task_text[:40]:40s}{cp_savings}{err}"
            )

    duration_s = time.perf_counter() - t_start
    agg = _aggregate(runs)

    # Save raw results
    output = {
        "meta": {
            "repo": str(repo),
            "n_tasks": len(tasks),
            "max_tokens": args.max_tokens,
            "top_files": args.top_files,
            "duration_s": round(duration_s, 1),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "aggregate": agg,
        "runs": runs,
    }
    json_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    # Save markdown summary
    summary_md = _render_summary(
        agg,
        repo=str(repo),
        n_tasks=len(tasks),
        duration_s=duration_s,
    )
    md_path.write_text(summary_md, encoding="utf-8")

    # Print summary to stdout
    cp = agg["strategies"].get("compressed_pack", {})
    ca = agg["strategies"].get("cache_assisted_pack", {})

    print()
    print("=" * 64)
    print(f"  {len(tasks)} tasks  |  {duration_s:.0f}s  |  {agg['errors']} errors")
    print("=" * 64)
    print(f"  Baseline (avg)          : {agg['avg_baseline_tokens']:>8,} tokens")
    print(f"  compressed_pack (avg)   : {cp.get('mean_tokens', 0):>8,} tokens  "
          f"({cp.get('mean_savings_pct', 0):.1f}% savings)")
    print(f"  cache_assisted_pack     : {ca.get('mean_tokens', 0):>8,} tokens  "
          f"({ca.get('mean_savings_pct', 0):.1f}% savings)")
    sp = cp.get("savings_pct_percentiles", {})
    print(f"  p50 savings             : {sp.get('p50', 0):.1f}%")
    print(f"  p95 savings             : {sp.get('p95', 0):.1f}%")
    print()
    print(f"  JSON  -> {json_path}")
    print(f"  Report -> {md_path}")
    print("=" * 64)


if __name__ == "__main__":
    main()
