from __future__ import annotations

"""Agent workflow simulator: estimate token costs before running a coding agent."""

import math
import statistics
from typing import Callable, Optional

from redcon.core.agent_cost import compute_workflow_cost
from redcon.core.agent_planning import build_agent_workflow_plan
from redcon.schemas.models import FileRecord, RankedFile


_DEFAULT_PROMPT_OVERHEAD = 800
_DEFAULT_OUTPUT_TOKENS = 600
_DEFAULT_MODEL = "gpt-4o"

CONTEXT_MODES = ("isolated", "rolling", "full")


def _step_context_tokens(step_context: list) -> tuple[list[dict], int]:
    """Extract file-read entries and total context tokens from a plan step context list."""
    files_read: list[dict] = []
    total = 0
    for entry in step_context:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", ""))
        tokens = int(entry.get("estimated_tokens", 0) or 0)
        source = str(entry.get("source", "step"))
        files_read.append({"path": path, "tokens": tokens, "read_type": source})
        total += tokens
    return files_read, total


def _cumulative_context(
    *,
    mode: str,
    prev_context: int,
    this_context: int,
) -> int:
    """Compute context window size at the current step based on accumulation mode."""
    if mode == "isolated":
        return this_context
    if mode == "rolling":
        # Two-step rolling window: previous step + current step
        return prev_context + this_context
    # full: monotonically growing context
    return prev_context + this_context


def simulate_agent_workflow(
    *,
    task: str,
    files: list[FileRecord],
    ranked: list[RankedFile],
    top_n: int,
    estimate_tokens: Callable[[str], int],
    score_task: Callable[[str], list[RankedFile]],
    prompt_overhead_per_step: int = _DEFAULT_PROMPT_OVERHEAD,
    output_tokens_per_step: int = _DEFAULT_OUTPUT_TOKENS,
    context_mode: str = "isolated",
    workspace_mode: bool = False,
    model: str = _DEFAULT_MODEL,
    price_per_1m_input: Optional[float] = None,
    price_per_1m_output: Optional[float] = None,
) -> dict:
    """Simulate agent workflow execution and estimate token and USD costs per step.

    Returns a dict with:
      - steps: list of per-step token + cost breakdowns
      - total_tokens, token_variance, token_std_dev, min/max/avg_step_tokens
      - total_context_tokens, unique_context_tokens
      - total_prompt_tokens, total_output_tokens
      - cost_estimate: full USD cost breakdown keyed by model + pricing
      - simulation parameters used
    """
    if context_mode not in CONTEXT_MODES:
        context_mode = "isolated"

    workflow_plan = build_agent_workflow_plan(
        task=task,
        files=files,
        ranked=ranked,
        top_n=top_n,
        estimate_tokens=estimate_tokens,
        score_task=score_task,
        workspace_mode=workspace_mode,
    )

    steps_out: list[dict] = []
    all_seen_paths: set[str] = set()
    total_context_tokens = 0
    unique_context_tokens = 0
    total_prompt_tokens = 0
    total_output_tokens_sum = 0
    step_totals: list[int] = []
    cumulative_total = 0
    prev_context = 0

    for step in workflow_plan.steps:
        # Serialize context from AgentPlanStep dataclass
        step_context_dicts = [
            {
                "path": ctx.path,
                "estimated_tokens": ctx.estimated_tokens,
                "source": ctx.source,
            }
            for ctx in step.context
        ]

        files_read, context_tokens = _step_context_tokens(step_context_dicts)
        prompt_tokens = prompt_overhead_per_step
        output_tokens = output_tokens_per_step
        step_total = prompt_tokens + context_tokens + output_tokens

        for ctx in step.context:
            total_context_tokens += ctx.estimated_tokens
            if ctx.path not in all_seen_paths:
                unique_context_tokens += ctx.estimated_tokens
                all_seen_paths.add(ctx.path)

        cumulative_ctx = _cumulative_context(
            mode=context_mode,
            prev_context=prev_context,
            this_context=context_tokens,
        )
        cumulative_total += step_total

        steps_out.append({
            "id": step.id,
            "title": step.title,
            "objective": step.objective,
            "files_read": files_read,
            "file_count": len(files_read),
            "prompt_tokens": prompt_tokens,
            "context_tokens": context_tokens,
            "output_tokens": output_tokens,
            "step_total_tokens": step_total,
            "cumulative_context_tokens": cumulative_ctx,
            "cumulative_total_tokens": cumulative_total,
        })

        total_prompt_tokens += prompt_tokens
        total_output_tokens_sum += output_tokens
        step_totals.append(step_total)

        if context_mode == "rolling":
            # Rolling: next step "inherits" this step's context only
            prev_context = context_tokens
        elif context_mode == "full":
            prev_context = cumulative_ctx
        # isolated: prev_context stays 0

    total_tokens = sum(step_totals)
    if len(step_totals) >= 2:
        token_variance = statistics.pvariance(step_totals)
        token_std_dev = statistics.pstdev(step_totals)
    elif len(step_totals) == 1:
        token_variance = 0.0
        token_std_dev = 0.0
    else:
        token_variance = 0.0
        token_std_dev = 0.0

    min_step_tokens = min(step_totals) if step_totals else 0
    max_step_tokens = max(step_totals) if step_totals else 0
    avg_step_tokens = total_tokens / len(step_totals) if step_totals else 0.0

    cost_estimate = compute_workflow_cost(
        model=model,
        steps=steps_out,
        total_tokens=total_tokens,
        total_output_tokens=total_output_tokens_sum,
        price_per_1m_input=price_per_1m_input,
        price_per_1m_output=price_per_1m_output,
    )

    return {
        "steps": steps_out,
        "total_tokens": total_tokens,
        "token_variance": round(token_variance, 2),
        "token_std_dev": round(token_std_dev, 2),
        "min_step_tokens": min_step_tokens,
        "max_step_tokens": max_step_tokens,
        "avg_step_tokens": round(avg_step_tokens, 2),
        "total_context_tokens": total_context_tokens,
        "unique_context_tokens": unique_context_tokens,
        "total_prompt_tokens": total_prompt_tokens,
        "total_output_tokens": total_output_tokens_sum,
        "prompt_overhead_per_step": prompt_overhead_per_step,
        "output_tokens_per_step": output_tokens_per_step,
        "context_mode": context_mode,
        "model": model,
        "cost_estimate": cost_estimate,
    }
