"""Prompt-optimization insights (Pro feature, fully local and deterministic).

Reads the run history redcon already records and surfaces patterns worth
acting on: recurring prompts that keep pulling large contexts, and single
prompts that pack most of the repo. Every number comes from the local history
- there is no LLM call and no network, so the feature costs nothing to run.

The estimates are deliberately conservative and honest: a run's "potential
savings" is how many tokens it spent ABOVE the user's own median run. That is
a real, computable quantity ("this prompt is X tokens heavier than your typical
one"), not a fabricated promise. Everything user-facing is prefixed with "~".
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from redcon.cache.run_history import RunHistoryEntry

# Below this many runs there is not enough signal to say anything useful.
MIN_RUNS = 5
# A prompt must recur at least this many times to count as "recurring".
MIN_REPEATS = 3
# Ignore trivially small runs - they are not where the tokens go.
MIN_INPUT_TOKENS = 2_000
# A run is "broad" when it sits in the heaviest fifth of the user's runs.
BROAD_PERCENTILE = 0.8
# ...and, when we know the scan size, when it also kept most of what it saw.
BROAD_SELECTION_RATIO = 0.5


@dataclass(slots=True)
class InsightItem:
    """One actionable pattern found in the run history."""

    kind: str  # "recurring_prompt" | "broad_context"
    title: str
    detail: str
    suggestion: str
    run_count: int
    tokens_total: int
    potential_savings_tokens: int


@dataclass(slots=True)
class InsightsReport:
    """Deterministic summary of prompt-optimization opportunities."""

    runs_analyzed: int
    opportunities: list[InsightItem] = field(default_factory=list)
    total_potential_savings_tokens: int = 0
    note: str = ""


@dataclass(slots=True)
class _Run:
    index: int
    task: str
    norm_task: str
    input_tokens: int
    selected: int
    scanned: int


def _norm_task(task: str) -> str:
    return " ".join(task.lower().split())


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _collect_runs(entries: list[RunHistoryEntry]) -> list[_Run]:
    runs: list[_Run] = []
    for index, entry in enumerate(entries):
        usage = entry.token_usage or {}
        input_tokens = _int(usage.get("estimated_input_tokens"))
        if input_tokens <= 0:
            continue
        runs.append(
            _Run(
                index=index,
                task=entry.task.strip(),
                norm_task=_norm_task(entry.task),
                input_tokens=input_tokens,
                selected=len(entry.selected_files or []),
                scanned=_int(usage.get("files_scanned")),
            )
        )
    return runs


def _percentile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, int(fraction * (len(sorted_values) - 1)))
    return sorted_values[idx]


def _truncate(task: str, limit: int = 60) -> str:
    task = task or "(empty prompt)"
    return task if len(task) <= limit else task[: limit - 3] + "..."


def build_insights(entries: list[RunHistoryEntry]) -> InsightsReport:
    """Analyze run history and return prompt-optimization opportunities.

    Pure and deterministic: no clock, no IO, no network. The same history
    always yields the same report.
    """
    runs = _collect_runs(entries)
    if len(runs) < MIN_RUNS:
        return InsightsReport(
            runs_analyzed=len(runs),
            note=f"Need at least {MIN_RUNS} recorded runs to find patterns - keep using redcon.",
        )

    inputs = sorted(r.input_tokens for r in runs)
    median = int(statistics.median(inputs))
    broad_threshold = max(_percentile(inputs, BROAD_PERCENTILE), MIN_INPUT_TOKENS)

    # Per-run excess over the median run - the honest "addressable" amount.
    def excess(run: _Run) -> int:
        return max(0, run.input_tokens - median)

    opportunities: list[InsightItem] = []
    contributing: dict[int, int] = {}  # run index -> excess, deduped across items

    # 1) Recurring prompts: the same phrasing run many times, each pulling a
    #    large context. A saved, narrower phrasing compounds every repeat.
    groups: dict[str, list[_Run]] = defaultdict(list)
    for run in runs:
        if run.norm_task:
            groups[run.norm_task].append(run)
    for group in groups.values():
        if len(group) < MIN_REPEATS:
            continue
        avg = int(statistics.mean(r.input_tokens for r in group))
        if avg < MIN_INPUT_TOKENS:
            continue
        total = sum(r.input_tokens for r in group)
        potential = sum(excess(r) for r in group)
        for run in group:
            contributing[run.index] = excess(run)
        opportunities.append(
            InsightItem(
                kind="recurring_prompt",
                title=f'Recurring prompt: "{_truncate(group[0].task)}"',
                detail=(
                    f"Run {len(group)} times, averaging ~{avg} tokens each (~{total} tokens total)."
                ),
                suggestion=(
                    "Save a narrower, reusable phrasing for this task (name the "
                    "subsystem or files) so every repeat packs less."
                ),
                run_count=len(group),
                tokens_total=total,
                potential_savings_tokens=potential,
            )
        )

    # 2) Broad contexts: a single prompt that is both heavy and, when we know
    #    the scan size, kept most of the files it saw.
    for run in runs:
        if run.input_tokens < broad_threshold:
            continue
        if run.scanned > 0 and run.selected / run.scanned < BROAD_SELECTION_RATIO:
            continue  # heavy but already well-scoped - not a scoping problem
        if run.index in contributing:
            continue  # already surfaced as part of a recurring group
        scope = f" ({run.selected} of {run.scanned} files)" if run.scanned > 0 else ""
        contributing[run.index] = excess(run)
        opportunities.append(
            InsightItem(
                kind="broad_context",
                title=f'Broad context: "{_truncate(run.task)}"',
                detail=f"Packed ~{run.input_tokens} tokens{scope} - heavier than your typical run.",
                suggestion=(
                    "Scope this prompt to the specific area you are changing so "
                    "redcon packs fewer files."
                ),
                run_count=1,
                tokens_total=run.input_tokens,
                potential_savings_tokens=excess(run),
            )
        )

    # Heaviest opportunities first; total dedups runs shared across items.
    opportunities.sort(key=lambda item: item.potential_savings_tokens, reverse=True)
    total_potential = sum(contributing.values())

    note = "" if opportunities else "No obvious prompt-scoping opportunities - nice and lean."
    return InsightsReport(
        runs_analyzed=len(runs),
        opportunities=opportunities,
        total_potential_savings_tokens=total_potential,
        note=note,
    )


def insights_as_dict(report: InsightsReport) -> dict:
    """JSON-serializable view of an insights report."""
    return {
        "command": "insights",
        "runs_analyzed": report.runs_analyzed,
        "total_potential_savings_tokens": report.total_potential_savings_tokens,
        "opportunities": [
            {
                "kind": item.kind,
                "title": item.title,
                "detail": item.detail,
                "suggestion": item.suggestion,
                "run_count": item.run_count,
                "tokens_total": item.tokens_total,
                "potential_savings_tokens": item.potential_savings_tokens,
            }
            for item in report.opportunities
        ],
        "note": report.note,
    }
