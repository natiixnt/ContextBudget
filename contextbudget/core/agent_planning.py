from __future__ import annotations

"""Deterministic lifecycle planning for multi-step agent workflows."""

from dataclasses import dataclass
import math
from typing import Callable

from contextbudget.core.text import task_keywords
from contextbudget.schemas.models import AgentPlanContextFile, AgentPlanStep, FileRecord, RankedFile


_CANDIDATE_BUFFER = 2
_MAX_SHARED_CONTEXT_FILES = 3


@dataclass(frozen=True, slots=True)
class StepBlueprint:
    """One deterministic workflow step template."""

    id: str
    title: str
    objective: str
    planning_prompt: str
    file_limit: int


@dataclass(slots=True)
class AgentWorkflowPlan:
    """Computed workflow plan before final artifact serialization."""

    steps: list[AgentPlanStep]
    shared_context: list[AgentPlanContextFile]
    total_estimated_tokens: int
    unique_context_tokens: int
    reused_context_tokens: int
    selected_repos: list[str]


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _task_subject(task: str) -> str:
    keywords = task_keywords(task)
    if keywords:
        return " ".join(keywords[:4])
    return str(task).strip() or "task"


def decompose_agent_task(task: str, *, workspace_mode: bool = False) -> list[StepBlueprint]:
    """Break a natural-language task into deterministic workflow steps."""

    task_text = task.lower()
    subject = _task_subject(task)
    has_bugfix = _has_any(task_text, ("bug", "fix", "regression", "broken", "issue", "error"))
    has_docs = _has_any(task_text, ("docs", "doc", "readme", "guide", "changelog", "migration"))
    has_refactor = _has_any(task_text, ("refactor", "rename", "extract", "migrate", "split", "consolidate"))
    has_cross_repo = workspace_mode or _has_any(
        task_text,
        ("across", "services", "service", "workspace", "repo", "repos", "monorepo", "shared"),
    )
    has_security = _has_any(
        task_text,
        ("auth", "token", "permission", "security", "secret", "credential", "access"),
    )

    steps: list[StepBlueprint] = []
    if has_bugfix:
        steps.append(
            StepBlueprint(
                id="inspect",
                title="Diagnose current behavior",
                objective=f"Trace the failing path and isolate the current behavior for {subject}.",
                planning_prompt=f"diagnose bug regression failure current behavior for {task}",
                file_limit=4,
            )
        )
    else:
        steps.append(
            StepBlueprint(
                id="inspect",
                title="Inspect current implementation",
                objective=f"Locate the main code paths and entrypoints for {subject}.",
                planning_prompt=f"inspect current implementation architecture entrypoints for {task}",
                file_limit=4,
            )
        )

    implement_title = "Implement and harden primary change" if has_security else "Implement primary change"
    steps.append(
        StepBlueprint(
            id="implement",
            title=implement_title,
            objective=f"Modify the core implementation needed to complete {subject}.",
            planning_prompt=f"implement code changes dependencies interfaces for {task}",
            file_limit=6 if has_cross_repo else 5,
        )
    )

    if has_cross_repo or has_refactor or has_security:
        steps.append(
            StepBlueprint(
                id="propagate",
                title="Propagate integration impact",
                objective=(
                    f"Update dependent modules, callers, and configuration affected by {subject}."
                ),
                planning_prompt=f"propagate integration caller dependency import config changes for {task}",
                file_limit=4,
            )
        )

    test_objective = "Add regression coverage for the changed behavior." if has_bugfix else "Update or add tests."
    steps.append(
        StepBlueprint(
            id="test",
            title="Update tests",
            objective=f"{test_objective} Keep coverage aligned with {subject}.",
            planning_prompt=f"update tests regression coverage fixtures validation for {task}",
            file_limit=4,
        )
    )

    if has_docs:
        steps.append(
            StepBlueprint(
                id="document",
                title="Update documentation",
                objective=f"Refresh operator and developer-facing documentation for {subject}.",
                planning_prompt=f"update documentation readme guide migration notes for {task}",
                file_limit=3,
            )
        )

    steps.append(
        StepBlueprint(
            id="validate",
            title="Validate final workflow",
            objective=f"Verify the end-to-end behavior and integration state for {subject}.",
            planning_prompt=f"validate end to end integration entrypoint ci runtime config for {task}",
            file_limit=4,
        )
    )
    return steps


def _estimate_file_tokens(record: FileRecord, estimate_tokens: Callable[[str], int]) -> int:
    preview = record.content_preview
    if not preview:
        if record.size_bytes <= 0:
            return 0
        return max(1, math.ceil(record.size_bytes / 4))

    preview_tokens = max(1, int(estimate_tokens(preview)))
    preview_chars = len(preview)
    if preview_chars <= 0 or record.size_bytes <= preview_chars:
        return preview_tokens

    scaled_tokens = math.ceil(preview_tokens * (record.size_bytes / preview_chars))
    return max(preview_tokens, scaled_tokens)


def _best_ranked_for_path(
    path: str,
    *,
    primary: dict[str, RankedFile],
    fallback: dict[str, RankedFile],
) -> RankedFile | None:
    if path in primary:
        return primary[path]
    return fallback.get(path)


def _serialize_context_file(
    ranked: RankedFile,
    *,
    estimated_tokens: int,
    source: str,
    reuse_count: int = 0,
    step_ids: list[str] | None = None,
) -> AgentPlanContextFile:
    entry = AgentPlanContextFile(
        path=ranked.file.path,
        score=ranked.score,
        estimated_tokens=estimated_tokens,
        reasons=list(ranked.reasons),
        line_count=ranked.file.line_count,
        source=source,
        reuse_count=reuse_count,
        step_ids=list(step_ids or []),
    )
    if ranked.file.repo_label:
        entry.repo = ranked.file.repo_label
        entry.relative_path = ranked.file.relative_path
    return entry


def build_agent_workflow_plan(
    *,
    task: str,
    files: list[FileRecord],
    ranked: list[RankedFile],
    top_n: int,
    estimate_tokens: Callable[[str], int],
    score_task: Callable[[str], list[RankedFile]],
    workspace_mode: bool = False,
) -> AgentWorkflowPlan:
    """Build a deterministic step-by-step context plan for an agent workflow."""

    blueprints = decompose_agent_task(task, workspace_mode=workspace_mode)
    file_tokens = {record.path: _estimate_file_tokens(record, estimate_tokens) for record in files}
    global_ranked_by_path = {item.file.path: item for item in ranked}
    step_rankings: dict[str, list[RankedFile]] = {}
    step_ids_by_path: dict[str, list[str]] = {}
    occurrence_by_path: dict[str, int] = {}
    best_ranked_by_path: dict[str, RankedFile] = dict(global_ranked_by_path)

    capped_top_n = max(1, int(top_n))
    for blueprint in blueprints:
        step_ranked = score_task(blueprint.planning_prompt)
        step_rankings[blueprint.id] = step_ranked

        effective_limit = max(1, min(blueprint.file_limit, capped_top_n))
        candidate_limit = min(len(step_ranked), effective_limit + _CANDIDATE_BUFFER)
        candidate_paths = [item.file.path for item in step_ranked[:candidate_limit]]

        for item in step_ranked[:candidate_limit]:
            existing = best_ranked_by_path.get(item.file.path)
            if existing is None or item.score > existing.score:
                best_ranked_by_path[item.file.path] = item

        for path in candidate_paths:
            occurrence_by_path[path] = occurrence_by_path.get(path, 0) + 1
            step_ids = step_ids_by_path.setdefault(path, [])
            if blueprint.id not in step_ids:
                step_ids.append(blueprint.id)

    shared_cap = min(_MAX_SHARED_CONTEXT_FILES, max(1, capped_top_n // 3))
    shared_paths = sorted(
        (path for path, count in occurrence_by_path.items() if count >= 2),
        key=lambda path: (
            -occurrence_by_path[path],
            -_best_ranked_for_path(path, primary=global_ranked_by_path, fallback=best_ranked_by_path).score,
            file_tokens.get(path, 0),
            path,
        ),
    )[:shared_cap]
    shared_path_set = set(shared_paths)

    shared_context: list[AgentPlanContextFile] = []
    for path in shared_paths:
        ranked_item = _best_ranked_for_path(path, primary=global_ranked_by_path, fallback=best_ranked_by_path)
        if ranked_item is None:
            continue
        shared_context.append(
            _serialize_context_file(
                ranked_item,
                estimated_tokens=file_tokens.get(path, 0),
                source="shared",
                reuse_count=occurrence_by_path.get(path, 0),
                step_ids=sorted(step_ids_by_path.get(path, [])),
            )
        )

    steps: list[AgentPlanStep] = []
    selected_paths: set[str] = set()
    for blueprint in blueprints:
        step_ranked = step_rankings.get(blueprint.id, [])
        effective_limit = max(1, min(blueprint.file_limit, capped_top_n))
        chosen_paths: list[str] = []

        for item in step_ranked:
            if item.file.path in shared_path_set and item.file.path not in chosen_paths:
                chosen_paths.append(item.file.path)
            if len(chosen_paths) >= effective_limit:
                break

        for item in step_ranked:
            if item.file.path in chosen_paths:
                continue
            chosen_paths.append(item.file.path)
            if len(chosen_paths) >= effective_limit:
                break

        context_entries: list[AgentPlanContextFile] = []
        shared_context_tokens = 0
        step_context_tokens = 0
        for path in chosen_paths:
            ranked_item = next((item for item in step_ranked if item.file.path == path), None)
            if ranked_item is None:
                continue
            is_shared = path in shared_path_set
            estimated = file_tokens.get(path, 0)
            context_entries.append(
                _serialize_context_file(
                    ranked_item,
                    estimated_tokens=estimated,
                    source="shared" if is_shared else "step",
                    reuse_count=occurrence_by_path.get(path, 0),
                    step_ids=sorted(step_ids_by_path.get(path, [])),
                )
            )
            if is_shared:
                shared_context_tokens += estimated
            else:
                step_context_tokens += estimated
            selected_paths.add(path)

        steps.append(
            AgentPlanStep(
                id=blueprint.id,
                title=blueprint.title,
                objective=blueprint.objective,
                planning_prompt=blueprint.planning_prompt,
                context=context_entries,
                estimated_tokens=shared_context_tokens + step_context_tokens,
                shared_context_tokens=shared_context_tokens,
                step_context_tokens=step_context_tokens,
            )
        )

    unique_context_tokens = sum(file_tokens.get(path, 0) for path in sorted(selected_paths))
    total_estimated_tokens = sum(step.estimated_tokens for step in steps)
    reused_context_tokens = max(0, total_estimated_tokens - unique_context_tokens)
    selected_repos = sorted(
        {
            item.file.repo_label
            for item in (
                _best_ranked_for_path(path, primary=global_ranked_by_path, fallback=best_ranked_by_path)
                for path in selected_paths
            )
            if item is not None and item.file.repo_label
        }
    )

    return AgentWorkflowPlan(
        steps=steps,
        shared_context=shared_context,
        total_estimated_tokens=total_estimated_tokens,
        unique_context_tokens=unique_context_tokens,
        reused_context_tokens=reused_context_tokens,
        selected_repos=selected_repos,
    )
