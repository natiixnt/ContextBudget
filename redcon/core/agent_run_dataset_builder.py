from __future__ import annotations

"""Agent Run Dataset Builder: generate token-reduction benchmarks for agent run scenarios.

Defines the four canonical agent run tasks and provides a builder function that
measures baseline (full-repo) vs optimised (compressed-pack) token counts for
each, producing a reproducible :class:`~redcon.core.dataset.DatasetReport`.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from redcon.core.dataset import (
    DatasetReport,
    DatasetTask,
    dataset_as_dict,
    run_dataset,
)

# ---------------------------------------------------------------------------
# Canonical agent run tasks
# ---------------------------------------------------------------------------

AGENT_RUN_TASKS: list[DatasetTask] = [
    DatasetTask(
        name="Add Caching",
        description=(
            "Add Redis caching to task lookup endpoints to reduce database load "
            "and improve response times"
        ),
    ),
    DatasetTask(
        name="Add Authentication",
        description=(
            "Add JWT authentication middleware to protect task and user API routes "
            "and validate user sessions"
        ),
    ),
    DatasetTask(
        name="Refactor Module",
        description=(
            "Refactor the database repository layer to use connection pooling "
            "for better performance and separation of concerns"
        ),
    ),
    DatasetTask(
        name="Add Rate Limiting",
        description=(
            "Add rate limiting middleware to API endpoints to prevent abuse "
            "and ensure fair usage"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentRunDatasetBuilderConfig:
    """Configuration for the Agent Run Dataset Builder.

    Attributes
    ----------
    tasks:
        Task list to benchmark.  Defaults to :data:`AGENT_RUN_TASKS`.
    max_tokens:
        Token budget forwarded to each benchmark run.
    top_files:
        Top-files limit forwarded to each benchmark run.
    """

    tasks: list[DatasetTask] = field(default_factory=lambda: list(AGENT_RUN_TASKS))
    max_tokens: int | None = None
    top_files: int | None = None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_agent_run_dataset(
    repo: Path,
    *,
    run_benchmark_fn: Any,
    tasks: list[DatasetTask] | None = None,
    max_tokens: int | None = None,
    top_files: int | None = None,
) -> DatasetReport:
    """Build a token-reduction benchmark dataset for agent run scenarios.

    Runs each task through Redcon's benchmark pipeline, comparing the
    full-repository baseline against the compressed-pack optimised context, and
    returns an aggregated :class:`~redcon.core.dataset.DatasetReport`.

    Parameters
    ----------
    repo:
        Repository path to benchmark against.
    run_benchmark_fn:
        Callable with signature ``(task, repo, *, max_tokens, top_files) -> dict``.
        In production this is ``RedconEngine.benchmark``.
    tasks:
        Task list to benchmark.  Defaults to :data:`AGENT_RUN_TASKS`.
    max_tokens:
        Token budget forwarded to each benchmark run.
    top_files:
        Top-files limit forwarded to each benchmark run.
    """
    effective_tasks = tasks if tasks is not None else list(AGENT_RUN_TASKS)
    return run_dataset(
        effective_tasks,
        repo,
        run_benchmark_fn=run_benchmark_fn,
        max_tokens=max_tokens,
        top_files=top_files,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def agent_run_dataset_as_dict(
    report: DatasetReport,
    *,
    task_count: int | None = None,
) -> dict[str, Any]:
    """Convert an agent run dataset report to a JSON-serialisable dictionary.

    Returns the standard :func:`~redcon.core.dataset.dataset_as_dict`
    structure plus a ``builder`` metadata block identifying this as an
    agent-run-specific report.

    Parameters
    ----------
    report:
        The :class:`~redcon.core.dataset.DatasetReport` to serialise.
    task_count:
        Explicit task count to embed in metadata.  Defaults to
        ``report.task_count``.
    """
    result = dataset_as_dict(report)
    result["builder"] = "agent_run"
    result["agent_run_task_count"] = task_count if task_count is not None else report.task_count
    return result


__all__ = [
    "AGENT_RUN_TASKS",
    "AgentRunDatasetBuilderConfig",
    "agent_run_dataset_as_dict",
    "build_agent_run_dataset",
]
