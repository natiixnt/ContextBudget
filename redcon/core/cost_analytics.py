from __future__ import annotations

"""Token cost analytics — compute financial savings from context optimisation.

Aggregates run data from pack artifacts and observe-history to produce
cost reports broken down by repository, agent run, and optimisation stage.
"""

import json
from pathlib import Path
from typing import Any

from redcon.telemetry.pricing import (
    DEFAULT_MODEL,
    MODEL_PRICING,
    compute_run_costs,
    get_pricing,
    tokens_to_usd,
)


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------

_KNOWN_COMMANDS = {"pack", "benchmark", "simulate-agent", "plan", "plan-agent"}


def _load_pack_artifacts(paths: list[Path]) -> list[dict[str, Any]]:
    """Return pack-command run artifacts found under *paths*, newest first."""
    found: list[dict[str, Any]] = []
    visited: set[Path] = set()

    def _load(p: Path) -> None:
        if p in visited:
            return
        visited.add(p)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(data, dict) and data.get("command") in _KNOWN_COMMANDS:
            data["_artifact_path"] = str(p)
            found.append(data)

    for path in paths:
        if path.is_file():
            _load(path)
        elif path.is_dir():
            for p in sorted(path.rglob("*.json")):
                if not any(part.startswith(".") for part in p.parts):
                    _load(p)

    return sorted(found, key=lambda d: d.get("generated_at", ""), reverse=True)


def _load_observe_entries(paths: list[Path]) -> list[dict[str, Any]]:
    """Load AgentRunMetrics entries from observe-history.json files."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        root = path if path.is_dir() else path.parent
        obs_file = root / ".redcon" / "observe-history.json"
        if not obs_file.exists():
            continue
        try:
            data = json.loads(obs_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for entry in data.get("entries", []):
            if not isinstance(entry, dict):
                continue
            key = entry.get("generated_at", "") + entry.get("run_json", "")
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    return sorted(entries, key=lambda e: e.get("generated_at", ""), reverse=True)


def _load_history_entries(paths: list[Path]) -> list[dict[str, Any]]:
    """Load entries from .redcon/history.json (pack run history)."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        root = path if path.is_dir() else path.parent
        hist_file = root / ".redcon" / "history.json"
        if not hist_file.exists():
            continue
        try:
            data = json.loads(hist_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for entry in data.get("entries", []):
            if not isinstance(entry, dict):
                continue
            ts = entry.get("generated_at", "")
            if not ts or ts in seen:
                continue
            seen.add(ts)
            entries.append(entry)
    return sorted(entries, key=lambda e: e.get("generated_at", ""), reverse=True)


# ---------------------------------------------------------------------------
# Per-run record builder
# ---------------------------------------------------------------------------

def _extract_run_record(
    source: dict[str, Any],
    source_type: str,
) -> dict[str, Any] | None:
    """Extract token fields from an artifact or history entry.

    Returns a normalised dict with keys:
        generated_at, task, repo, command,
        baseline_tokens, optimized_tokens,
        cache_tokens_saved, duplicate_reads_prevented,
        source_type, artifact_path
    Returns None if there are no usable token metrics.
    """
    cmd = source.get("command", "pack")

    if source_type == "artifact":
        if cmd == "pack":
            budget = source.get("budget") or {}
            cache = source.get("cache") or {}
            optimized = int(budget.get("estimated_input_tokens") or 0)
            saved = int(budget.get("estimated_saved_tokens") or 0)
            baseline = optimized + saved
            cache_saved = int(cache.get("tokens_saved") or 0)
            dup_prevented = int(budget.get("duplicate_reads_prevented") or 0)
        elif cmd == "simulate-agent":
            optimized = int(source.get("total_tokens") or 0)
            baseline = optimized  # no baseline available for simulations
            saved = 0
            cache_saved = 0
            dup_prevented = 0
        else:
            return None

        if baseline == 0 and optimized == 0:
            return None

        return {
            "generated_at": str(source.get("generated_at", "") or ""),
            "task": str(source.get("task", "") or ""),
            "repo": str(source.get("repo", "") or ""),
            "command": cmd,
            "baseline_tokens": baseline,
            "optimized_tokens": optimized,
            "cache_tokens_saved": cache_saved,
            "duplicate_reads_prevented": dup_prevented,
            "source_type": "artifact",
            "artifact_path": str(source.get("_artifact_path", "") or ""),
        }

    elif source_type == "observe":
        optimized = int(source.get("total_tokens") or 0)
        baseline = int(source.get("baseline_tokens") or 0)
        if not baseline and optimized:
            baseline = optimized + int(source.get("tokens_saved") or 0)
        cache_saved = int(source.get("cache_tokens_saved") or 0)
        dup_prevented = int(source.get("duplicate_reads_prevented") or 0)

        if baseline == 0 and optimized == 0:
            return None

        return {
            "generated_at": str(source.get("generated_at", "") or ""),
            "task": str(source.get("task", "") or ""),
            "repo": str(source.get("repo", "") or ""),
            "command": "observe",
            "baseline_tokens": baseline,
            "optimized_tokens": optimized,
            "cache_tokens_saved": cache_saved,
            "duplicate_reads_prevented": dup_prevented,
            "source_type": "observe",
            "artifact_path": str(source.get("run_json", "") or ""),
        }

    elif source_type == "history":
        tu = source.get("token_usage") or {}
        optimized = int(tu.get("estimated_input_tokens") or 0)
        saved = int(tu.get("estimated_saved_tokens") or 0)
        baseline = optimized + saved

        if baseline == 0 and optimized == 0:
            return None

        return {
            "generated_at": str(source.get("generated_at", "") or ""),
            "task": str(source.get("task", "") or ""),
            "repo": str(source.get("repo", "") or ""),
            "command": "pack",
            "baseline_tokens": baseline,
            "optimized_tokens": optimized,
            "cache_tokens_saved": 0,
            "duplicate_reads_prevented": 0,
            "source_type": "history",
            "artifact_path": str(
                (source.get("result_artifacts") or {}).get("json", "") or ""
            ),
        }

    return None


# ---------------------------------------------------------------------------
# Aggregation queries
# ---------------------------------------------------------------------------

def _cost_by_repository(
    records: list[dict[str, Any]],
    rate: float,
) -> list[dict[str, Any]]:
    """Return cost totals grouped by repository path."""
    repos: dict[str, dict[str, Any]] = {}
    for r in records:
        repo = r["repo"] or "(unknown)"
        if repo not in repos:
            repos[repo] = {
                "repository": repo,
                "runs": 0,
                "baseline_tokens": 0,
                "optimized_tokens": 0,
                "tokens_saved": 0,
                "baseline_cost_usd": 0.0,
                "optimized_cost_usd": 0.0,
                "savings_usd": 0.0,
            }
        entry = repos[repo]
        tokens_saved = max(0, r["baseline_tokens"] - r["optimized_tokens"])
        entry["runs"] += 1
        entry["baseline_tokens"] += r["baseline_tokens"]
        entry["optimized_tokens"] += r["optimized_tokens"]
        entry["tokens_saved"] += tokens_saved
        entry["baseline_cost_usd"] += tokens_to_usd(r["baseline_tokens"], rate)
        entry["optimized_cost_usd"] += tokens_to_usd(r["optimized_tokens"], rate)
        entry["savings_usd"] += tokens_to_usd(tokens_saved, rate)

    result = []
    for entry in repos.values():
        entry["baseline_cost_usd"] = round(entry["baseline_cost_usd"], 8)
        entry["optimized_cost_usd"] = round(entry["optimized_cost_usd"], 8)
        entry["savings_usd"] = round(entry["savings_usd"], 8)
        bc = entry["baseline_cost_usd"]
        entry["savings_pct"] = round(entry["savings_usd"] / bc, 6) if bc > 0 else 0.0
        result.append(entry)

    return sorted(result, key=lambda x: x["savings_usd"], reverse=True)


def _cost_by_run(
    records: list[dict[str, Any]],
    rate: float,
) -> list[dict[str, Any]]:
    """Return per-run cost breakdown."""
    result = []
    for r in records:
        tokens_saved = max(0, r["baseline_tokens"] - r["optimized_tokens"])
        baseline_cost = tokens_to_usd(r["baseline_tokens"], rate)
        optimized_cost = tokens_to_usd(r["optimized_tokens"], rate)
        savings_usd = max(0.0, baseline_cost - optimized_cost)
        result.append({
            "generated_at": r["generated_at"],
            "task": r["task"],
            "repo": r["repo"] or "(unknown)",
            "command": r["command"],
            "baseline_tokens": r["baseline_tokens"],
            "optimized_tokens": r["optimized_tokens"],
            "tokens_saved": tokens_saved,
            "baseline_cost_usd": round(baseline_cost, 8),
            "optimized_cost_usd": round(optimized_cost, 8),
            "savings_usd": round(savings_usd, 8),
            "savings_pct": round(savings_usd / baseline_cost, 6) if baseline_cost > 0 else 0.0,
            "source_type": r["source_type"],
            "artifact_path": r["artifact_path"],
        })
    return result


def _cost_by_stage(
    records: list[dict[str, Any]],
    rate: float,
) -> dict[str, Any]:
    """Return cost savings broken down by optimisation stage.

    Stages
    ------
    cache
        Tokens (and USD) saved because file summaries were already cached
        and did not need to be re-transmitted.
    compression
        All remaining savings beyond the cache layer — primarily from
        intelligent ranking, chunking, and symbol-level compression applied
        by the packer.

    Note: ``duplicate_reads_prevented`` is included as an informational count
    because its exact token equivalent is not available without per-file data.
    """
    cache_tokens = 0
    compression_tokens = 0
    total_dup_prevented = 0

    for r in records:
        total_saved = max(0, r["baseline_tokens"] - r["optimized_tokens"])
        c_saved = min(r["cache_tokens_saved"], total_saved)
        cache_tokens += c_saved
        compression_tokens += max(0, total_saved - c_saved)
        total_dup_prevented += r["duplicate_reads_prevented"]

    cache_cost = tokens_to_usd(cache_tokens, rate)
    compression_cost = tokens_to_usd(compression_tokens, rate)

    return {
        "cache": {
            "tokens_saved": cache_tokens,
            "savings_usd": round(cache_cost, 8),
            "description": "Tokens saved by summary/fragment cache hits",
        },
        "compression": {
            "tokens_saved": compression_tokens,
            "savings_usd": round(compression_cost, 8),
            "description": "Tokens saved by ranking, chunking, and compression strategies",
        },
        "duplicate_reads_prevented_count": total_dup_prevented,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_cost_report(
    paths: list[Path],
    *,
    model: str = DEFAULT_MODEL,
    custom_pricing: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a full token cost analytics report.

    Aggregates pack artifacts, observe-history, and history.json entries found
    under *paths* and computes baseline vs optimised costs using the given
    model's per-token pricing.

    Parameters
    ----------
    paths:
        Directories or JSON artifact files to scan.
    model:
        Model identifier from :data:`~redcon.telemetry.pricing.MODEL_PRICING`
        used for cost calculations.  Defaults to ``claude-sonnet-4-6``.
    custom_pricing:
        Optional pricing overrides in the same shape as
        :data:`~redcon.telemetry.pricing.MODEL_PRICING`.

    Returns
    -------
    dict
        ``{model, pricing, summary, by_repository, by_run, by_stage,
           available_models}``
    """
    pricing = get_pricing(model, custom_pricing)
    rate = pricing["input_per_million"]

    # Collect run records from all three sources; deduplicate by generated_at.
    records: list[dict[str, Any]] = []
    seen_ts: set[str] = set()

    # 1. Pack/simulate-agent artifacts (highest fidelity — include cache data)
    for artifact in _load_pack_artifacts(paths):
        rec = _extract_run_record(artifact, "artifact")
        if rec is None:
            continue
        ts = rec["generated_at"]
        if ts and ts in seen_ts:
            continue
        if ts:
            seen_ts.add(ts)
        records.append(rec)

    # 2. Observe-history entries (agent middleware runs with richer stage data)
    for obs in _load_observe_entries(paths):
        rec = _extract_run_record(obs, "observe")
        if rec is None:
            continue
        ts = rec["generated_at"]
        if ts and ts in seen_ts:
            continue
        if ts:
            seen_ts.add(ts)
        records.append(rec)

    # 3. history.json entries (lightweight fallback — no cache breakdown)
    for hist in _load_history_entries(paths):
        rec = _extract_run_record(hist, "history")
        if rec is None:
            continue
        ts = rec["generated_at"]
        if ts and ts in seen_ts:
            continue
        if ts:
            seen_ts.add(ts)
        records.append(rec)

    records.sort(key=lambda r: r["generated_at"], reverse=True)

    # ── aggregate summary ──────────────────────────────────────────────────
    total_baseline = sum(r["baseline_tokens"] for r in records)
    total_optimized = sum(r["optimized_tokens"] for r in records)
    total_saved_tokens = sum(max(0, r["baseline_tokens"] - r["optimized_tokens"]) for r in records)
    total_baseline_cost = tokens_to_usd(total_baseline, rate)
    total_optimized_cost = tokens_to_usd(total_optimized, rate)
    total_savings_usd = max(0.0, total_baseline_cost - total_optimized_cost)
    savings_pct = round(total_savings_usd / total_baseline_cost, 6) if total_baseline_cost > 0 else 0.0

    summary = {
        "total_runs": len(records),
        "model": model,
        "display_name": pricing["display_name"],
        "input_per_million_usd": rate,
        "total_baseline_tokens": total_baseline,
        "total_optimized_tokens": total_optimized,
        "total_tokens_saved": total_saved_tokens,
        "total_baseline_cost_usd": round(total_baseline_cost, 8),
        "total_optimized_cost_usd": round(total_optimized_cost, 8),
        "total_savings_usd": round(total_savings_usd, 8),
        "savings_pct": savings_pct,
    }

    return {
        "model": model,
        "pricing": {
            "model": model,
            "display_name": pricing["display_name"],
            "input_per_million_usd": rate,
            "output_per_million_usd": pricing["output_per_million"],
        },
        "summary": summary,
        "by_repository": _cost_by_repository(records, rate),
        "by_run": _cost_by_run(records, rate),
        "by_stage": _cost_by_stage(records, rate),
        "available_models": [
            {
                "id": mid,
                "display_name": info["display_name"],
                "input_per_million_usd": info["input_per_million"],
            }
            for mid, info in sorted(MODEL_PRICING.items(), key=lambda x: x[1]["input_per_million"])
        ],
    }


__all__ = [
    "build_cost_report",
]
