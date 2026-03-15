from __future__ import annotations

"""Context optimization pipeline tracer.

Reconstructs the full context pipeline that ran during a ``pack`` command and
presents it as an ordered sequence of named stages, each annotated with token
counts, tokens saved, and per-cent reductions.

Stages modelled
---------------
1. repo_scan              - files discovered in the repository
2. file_ranking           - files scored and ranked against the task
3. budget_selection       - top-N files selected to fit the token budget
4. cache_reuse            - files served from the warm context cache
5. symbol_extraction      - symbol-only views extracted for large files
6. context_slicing        - language-aware slices pulled from files
7. compression            - LLM / deterministic summarisation applied
8. snippet_selection      - short snippet windows selected
9. full_include           - files included verbatim (no reduction)
10. delta_context         - incremental delta versus a previous run
11. final_context         - resulting context delivered to the model
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from redcon.core.profiler import (
    STAGE_CACHE_REUSE,
    STAGE_COMPRESSION,
    STAGE_DELTA,
    STAGE_FULL,
    STAGE_SLICING,
    STAGE_SNIPPET,
    STAGE_SYMBOL_EXTRACTION,
    _ORDERED_STAGES,
    _classify_stage,
)


# ---------------------------------------------------------------------------
# Stage metadata
# ---------------------------------------------------------------------------

_STAGE_LABELS: dict[str, str] = {
    "repo_scan":          "Repo Scan",
    "file_ranking":       "File Ranking",
    "budget_selection":   "Budget Selection",
    STAGE_CACHE_REUSE:    "Cache Reuse",
    STAGE_SYMBOL_EXTRACTION: "Symbol Extraction",
    STAGE_SLICING:        "Context Slicing",
    STAGE_COMPRESSION:    "Compression",
    STAGE_SNIPPET:        "Snippet Selection",
    STAGE_FULL:           "Full Include",
    STAGE_DELTA:          "Delta Context",
    "final_context":      "Final Context",
}

# Stages that are "optimisation" sub-stages of the packing step
_OPTIMISATION_STAGES: tuple[str, ...] = (
    STAGE_CACHE_REUSE,
    STAGE_SYMBOL_EXTRACTION,
    STAGE_SLICING,
    STAGE_COMPRESSION,
    STAGE_SNIPPET,
    STAGE_FULL,
    STAGE_DELTA,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PipelineStage:
    """Token flow metrics for one named pipeline stage."""

    name: str
    label: str
    files_in: int
    files_out: int
    tokens_in: int
    tokens_out: int
    tokens_saved: int
    reduction_pct: float      # % of *stage* tokens_in saved at this stage
    cumulative_tokens: int    # running total after this stage
    is_optimisation: bool = False
    notes: str = ""


@dataclass(slots=True)
class PipelineTrace:
    """Full pipeline trace reconstructed from a ``pack`` run artifact."""

    command: str
    run_json: str
    task: str
    repo: str
    generated_at: str
    scanned_files: int
    stages: list[PipelineStage]
    tokens_at_scan: int
    tokens_after_ranking: int
    tokens_before_pack: int
    tokens_after_pack: int
    final_tokens: int
    total_tokens_saved: int
    total_reduction_pct: float
    has_delta: bool
    has_cache: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(saved: int, total: int) -> float:
    if total <= 0 or saved <= 0:
        return 0.0
    return round(min(100.0, saved / total * 100), 1)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_pipeline_trace(
    run_data: dict[str, Any],
    *,
    run_json: str = "",
) -> PipelineTrace:
    """Reconstruct the full pipeline trace from a ``pack`` run artifact dict.

    Parameters
    ----------
    run_data:
        Parsed contents of a run JSON file produced by ``redcon pack``.
    run_json:
        Path label used for display / output naming.
    """

    # ---- source data -------------------------------------------------------
    scanned_files = int(run_data.get("scanned_files") or 0)
    ranked_files: list[dict] = [
        r for r in (run_data.get("ranked_files") or []) if isinstance(r, dict)
    ]
    compressed_context: list[dict] = [
        e for e in (run_data.get("compressed_context") or []) if isinstance(e, dict)
    ]
    files_included: list[str] = run_data.get("files_included") or []
    files_skipped: list[str] = run_data.get("files_skipped") or []
    budget: dict = run_data.get("budget") or {}
    delta: dict = run_data.get("delta") or {}

    # ---- token totals -------------------------------------------------------
    # Total tokens across the ranked pool (proxy for "tokens at scan").
    # ranked_files[].estimated_tokens comes from the scorer and is available
    # in every run JSON.
    tokens_ranked_pool = sum(
        int(r.get("estimated_tokens") or 0) for r in ranked_files
    )

    # Tokens entering the pack step = sum of original_tokens in compressed_context
    tokens_before_pack = sum(
        int(e.get("original_tokens") or 0) for e in compressed_context
    )

    # Tokens after the pack step = sum of compressed_tokens
    tokens_after_pack = sum(
        int(e.get("compressed_tokens") or 0) for e in compressed_context
    )

    # Delta savings
    delta_saved = 0
    if isinstance(delta, dict):
        delta_budget = delta.get("budget") or {}
        delta_saved = int(delta_budget.get("tokens_saved") or 0) if isinstance(delta_budget, dict) else 0

    # Final context size (from budget report; fall back to computed value)
    final_tokens = int(budget.get("estimated_input_tokens") or 0) or tokens_after_pack

    # ---- per-optimisation-stage breakdown ----------------------------------
    opt_stage_data: dict[str, dict[str, Any]] = {
        s: {"entries": [], "tokens_in": 0, "tokens_out": 0}
        for s in _ORDERED_STAGES
    }
    for entry in compressed_context:
        stage = _classify_stage(entry)
        orig = int(entry.get("original_tokens") or 0)
        comp = int(entry.get("compressed_tokens") or 0)
        opt_stage_data[stage]["entries"].append(entry)
        opt_stage_data[stage]["tokens_in"] += orig
        opt_stage_data[stage]["tokens_out"] += comp

    # Delta stage is not a file-level entry - set separately
    opt_stage_data[STAGE_DELTA]["tokens_in"] = delta_saved
    opt_stage_data[STAGE_DELTA]["tokens_out"] = 0

    # ---- build stage list --------------------------------------------------
    stages: list[PipelineStage] = []

    # 1. Repo Scan
    # We don't have per-file token data for *all* scanned files, so we report
    # the ranked-pool total as the best available proxy.
    stages.append(PipelineStage(
        name="repo_scan",
        label=_STAGE_LABELS["repo_scan"],
        files_in=scanned_files,
        files_out=len(ranked_files),
        tokens_in=tokens_ranked_pool,
        tokens_out=tokens_ranked_pool,
        tokens_saved=0,
        reduction_pct=0.0,
        cumulative_tokens=tokens_ranked_pool,
        notes=f"{scanned_files} scanned, {len(ranked_files)} matched task",
    ))

    # 2. File Ranking
    # Saves from files that scored below the cut-off.
    ranking_saved = max(0, tokens_ranked_pool - tokens_before_pack)
    stages.append(PipelineStage(
        name="file_ranking",
        label=_STAGE_LABELS["file_ranking"],
        files_in=len(ranked_files),
        files_out=len(files_included) if files_included else len(compressed_context),
        tokens_in=tokens_ranked_pool,
        tokens_out=tokens_before_pack,
        tokens_saved=ranking_saved,
        reduction_pct=_pct(ranking_saved, tokens_ranked_pool),
        cumulative_tokens=tokens_before_pack,
        notes=f"{len(files_included)} included, {len(files_skipped)} budget-skipped",
    ))

    # 3. Budget Selection  (same checkpoint, different label - shows the gate)
    n_selected = len(compressed_context)
    stages.append(PipelineStage(
        name="budget_selection",
        label=_STAGE_LABELS["budget_selection"],
        files_in=len(files_included) if files_included else n_selected,
        files_out=n_selected,
        tokens_in=tokens_before_pack,
        tokens_out=tokens_before_pack,
        tokens_saved=0,
        reduction_pct=0.0,
        cumulative_tokens=tokens_before_pack,
        notes=(
            f"token budget: {int(budget.get('max_tokens') or 0):,}  "
            f"risk: {budget.get('quality_risk_estimate', 'unknown')}"
        ),
    ))

    # 4-10. Optimisation sub-stages
    for stage_name in _ORDERED_STAGES:
        sd = opt_stage_data[stage_name]

        if stage_name == STAGE_DELTA:
            n_files = 0
            t_in = delta_saved
            t_out = 0
            t_saved = delta_saved
        else:
            n_files = len(sd["entries"])
            t_in = sd["tokens_in"]
            t_out = sd["tokens_out"]
            t_saved = max(0, t_in - t_out)

        # Skip fully empty stages (except FULL and DELTA which always appear)
        if n_files == 0 and t_saved == 0 and stage_name not in (STAGE_FULL, STAGE_DELTA):
            continue

        stages.append(PipelineStage(
            name=stage_name,
            label=_STAGE_LABELS.get(stage_name, stage_name),
            files_in=n_files,
            files_out=n_files,
            tokens_in=t_in,
            tokens_out=t_out,
            tokens_saved=t_saved,
            reduction_pct=_pct(t_saved, tokens_before_pack),
            cumulative_tokens=t_out,
            is_optimisation=True,
        ))

    # 11. Final Context
    total_saved = max(0, tokens_ranked_pool - final_tokens)
    stages.append(PipelineStage(
        name="final_context",
        label=_STAGE_LABELS["final_context"],
        files_in=n_selected,
        files_out=n_selected,
        tokens_in=tokens_after_pack,
        tokens_out=final_tokens,
        tokens_saved=max(0, tokens_after_pack - final_tokens),
        reduction_pct=_pct(total_saved, tokens_ranked_pool),
        cumulative_tokens=final_tokens,
        notes=(
            f"{_pct(total_saved, tokens_ranked_pool):.1f}% total reduction "
            f"from ranked pool"
        ),
    ))

    return PipelineTrace(
        command="pipeline",
        run_json=run_json,
        task=str(run_data.get("task") or ""),
        repo=str(run_data.get("repo") or ""),
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        scanned_files=scanned_files,
        stages=stages,
        tokens_at_scan=tokens_ranked_pool,
        tokens_after_ranking=tokens_before_pack,
        tokens_before_pack=tokens_before_pack,
        tokens_after_pack=tokens_after_pack,
        final_tokens=final_tokens,
        total_tokens_saved=total_saved,
        total_reduction_pct=_pct(total_saved, tokens_ranked_pool),
        has_delta=delta_saved > 0,
        has_cache=bool(opt_stage_data[STAGE_CACHE_REUSE]["tokens_in"]),
    )


def pipeline_trace_as_dict(trace: PipelineTrace) -> dict[str, Any]:
    """Convert a PipelineTrace to a JSON-serialisable dict."""
    return asdict(trace)
