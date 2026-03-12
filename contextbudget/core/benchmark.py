from __future__ import annotations

"""Benchmark mode for comparing deterministic context packing strategies."""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from contextbudget.config import ContextBudgetConfig, load_config
from contextbudget.core.tokens import estimate_tokens
from contextbudget.core.pipeline import run_pack
from contextbudget.schemas.models import DEFAULT_TOP_FILES
from contextbudget.stages.workflow import run_scan_stage, run_score_stage


def _risk_from_coverage(input_tokens: int, baseline_tokens: int) -> str:
    if baseline_tokens <= 0:
        return "low"
    coverage = input_tokens / baseline_tokens
    if coverage >= 0.8:
        return "low"
    if coverage >= 0.4:
        return "medium"
    return "high"


def _read_file_tokens(absolute_path: str) -> int | None:
    try:
        text = Path(absolute_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    return estimate_tokens(text)


def _round_runtime_ms(start: float, end: float) -> int:
    return int(round((end - start) * 1000))


def run_benchmark(
    task: str,
    repo: Path,
    max_tokens: int | None = None,
    top_files: int | None = None,
    config: ContextBudgetConfig | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Run benchmark strategies and return JSON-serializable report."""

    cfg = config if config is not None else load_config(repo, config_path=config_path)
    effective_max_tokens = max_tokens if max_tokens is not None else cfg.budget.max_tokens
    effective_top_files = top_files if top_files is not None else cfg.budget.top_files

    scan_start = time.perf_counter()
    files = run_scan_stage(repo, cfg)
    ranked = run_score_stage(task, files, cfg)
    scan_end = time.perf_counter()

    if effective_top_files is not None:
        ranked_top = ranked[:effective_top_files]
    else:
        ranked_top = ranked[:DEFAULT_TOP_FILES]
        effective_top_files = DEFAULT_TOP_FILES

    readable_tokens: dict[str, int] = {}
    unreadable_paths: list[str] = []
    for record in files:
        token_count = _read_file_tokens(record.absolute_path)
        if token_count is None:
            unreadable_paths.append(record.path)
            continue
        readable_tokens[record.path] = token_count

    baseline_total_tokens = sum(readable_tokens.values())

    strategies: list[dict[str, Any]] = []

    naive_start = time.perf_counter()
    naive_end = time.perf_counter()
    naive_files_included = sorted(readable_tokens.keys())
    strategies.append(
        {
            "strategy": "naive_full_context",
            "description": "Send full readable repository context without selection or compression.",
            "estimated_input_tokens": baseline_total_tokens,
            "estimated_saved_tokens": 0,
            "files_included": naive_files_included,
            "files_skipped": unreadable_paths,
            "duplicate_reads_prevented": 0,
            "quality_risk_estimate": "low",
            "cache_hits": 0,
            "runtime_ms": _round_runtime_ms(naive_start, naive_end),
        }
    )

    topk_start = time.perf_counter()
    topk_included = [item.file.path for item in ranked_top if item.file.path in readable_tokens]
    topk_skipped = sorted(set(readable_tokens.keys()) - set(topk_included))
    topk_input_tokens = sum(readable_tokens[path] for path in topk_included)
    topk_end = time.perf_counter()
    strategies.append(
        {
            "strategy": "top_k_selection",
            "description": "Select top-ranked files and include full content without compression.",
            "estimated_input_tokens": topk_input_tokens,
            "estimated_saved_tokens": max(0, baseline_total_tokens - topk_input_tokens),
            "files_included": topk_included,
            "files_skipped": topk_skipped,
            "duplicate_reads_prevented": 0,
            "quality_risk_estimate": _risk_from_coverage(topk_input_tokens, baseline_total_tokens),
            "cache_hits": 0,
            "runtime_ms": _round_runtime_ms(topk_start, topk_end),
            "notes": f"top_files={effective_top_files}",
        }
    )

    pack_start = time.perf_counter()
    packed_run = asdict(
        run_pack(
            task,
            repo,
            max_tokens=effective_max_tokens,
            top_files=effective_top_files,
            config=cfg,
        )
    )
    pack_end = time.perf_counter()
    pack_budget = packed_run.get("budget", {})
    compressed_input_tokens = int(pack_budget.get("estimated_input_tokens", 0) or 0)
    compressed_saved_tokens = max(0, baseline_total_tokens - compressed_input_tokens)
    strategies.append(
        {
            "strategy": "compressed_pack",
            "description": "Use ContextBudget scoring + compression under configured token budget.",
            "estimated_input_tokens": compressed_input_tokens,
            "estimated_saved_tokens": compressed_saved_tokens,
            "files_included": list(packed_run.get("files_included", [])),
            "files_skipped": list(packed_run.get("files_skipped", [])),
            "duplicate_reads_prevented": int(pack_budget.get("duplicate_reads_prevented", 0) or 0),
            "quality_risk_estimate": str(pack_budget.get("quality_risk_estimate", "unknown")),
            "cache_hits": int(packed_run.get("cache_hits", 0) or 0),
            "runtime_ms": _round_runtime_ms(pack_start, pack_end),
        }
    )

    cache_start = time.perf_counter()
    cache_run = asdict(
        run_pack(
            task,
            repo,
            max_tokens=effective_max_tokens,
            top_files=effective_top_files,
            config=cfg,
        )
    )
    cache_end = time.perf_counter()
    cache_budget = cache_run.get("budget", {})
    cache_input_tokens = int(cache_budget.get("estimated_input_tokens", 0) or 0)
    cache_saved_tokens = max(0, baseline_total_tokens - cache_input_tokens)
    strategies.append(
        {
            "strategy": "cache_assisted_pack",
            "description": "Repeat compressed pack on warm cache to measure cache-assisted behavior.",
            "estimated_input_tokens": cache_input_tokens,
            "estimated_saved_tokens": cache_saved_tokens,
            "files_included": list(cache_run.get("files_included", [])),
            "files_skipped": list(cache_run.get("files_skipped", [])),
            "duplicate_reads_prevented": int(cache_budget.get("duplicate_reads_prevented", 0) or 0),
            "quality_risk_estimate": str(cache_budget.get("quality_risk_estimate", "unknown")),
            "cache_hits": int(cache_run.get("cache_hits", 0) or 0),
            "runtime_ms": _round_runtime_ms(cache_start, cache_end),
            "notes": "second run with warmed summary cache",
        }
    )

    return {
        "command": "benchmark",
        "task": task,
        "repo": str(repo),
        "max_tokens": effective_max_tokens,
        "top_files": effective_top_files,
        "baseline_full_context_tokens": baseline_total_tokens,
        "scan_runtime_ms": _round_runtime_ms(scan_start, scan_end),
        "strategies": strategies,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
