from __future__ import annotations

"""Context Dataset Builder: generate token-reduction benchmark datasets from built-in tasks."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextbudget.core.dataset import (
    DatasetReport,
    DatasetTask,
    dataset_as_dict,
    load_tasks_toml,
    run_dataset,
)

# ---------------------------------------------------------------------------
# Built-in benchmark tasks
# ---------------------------------------------------------------------------

BUILTIN_TASKS: list[DatasetTask] = [
    DatasetTask(
        name="Add Caching",
        description="Add Redis caching to reduce redundant database queries and improve response times",
    ),
    DatasetTask(
        name="Add Authentication",
        description="Add JWT authentication middleware to protect API routes and validate user sessions",
    ),
    DatasetTask(
        name="Refactor Module",
        description="Refactor the database layer to use a repository pattern for better separation of concerns",
    ),
    DatasetTask(
        name="Add Logging",
        description="Add structured logging throughout the application for observability and debugging",
    ),
    DatasetTask(
        name="Add Error Handling",
        description="Add comprehensive error handling and input validation across API endpoints",
    ),
    DatasetTask(
        name="Add Rate Limiting",
        description="Add rate limiting middleware to API endpoints to prevent abuse and ensure fair usage",
    ),
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContextDatasetBuilderConfig:
    """Configuration for the Context Dataset Builder.

    Attributes
    ----------
    tasks:
        Task list to benchmark.  Defaults to :data:`BUILTIN_TASKS`.
    max_tokens:
        Token budget forwarded to each benchmark run.
    top_files:
        Top-files limit forwarded to each benchmark run.
    """

    tasks: list[DatasetTask] = field(default_factory=lambda: list(BUILTIN_TASKS))
    max_tokens: int | None = None
    top_files: int | None = None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_context_dataset(
    repo: Path,
    *,
    run_benchmark_fn: Any,
    tasks: list[DatasetTask] | None = None,
    max_tokens: int | None = None,
    top_files: int | None = None,
) -> DatasetReport:
    """Build a token-reduction benchmark dataset from predefined or custom tasks.

    When *tasks* is omitted the :data:`BUILTIN_TASKS` list is used, enabling
    reproducible benchmarks without any external configuration file.

    Parameters
    ----------
    repo:
        Repository path to benchmark against.
    run_benchmark_fn:
        Callable with signature ``(task, repo, *, max_tokens, top_files) -> dict``.
        In production this is ``ContextBudgetEngine.benchmark``.
    tasks:
        Task list to benchmark.  Defaults to :data:`BUILTIN_TASKS`.
    max_tokens:
        Token budget forwarded to each benchmark run.
    top_files:
        Top-files limit forwarded to each benchmark run.
    """
    effective_tasks = tasks if tasks is not None else list(BUILTIN_TASKS)
    return run_dataset(
        effective_tasks,
        repo,
        run_benchmark_fn=run_benchmark_fn,
        max_tokens=max_tokens,
        top_files=top_files,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_extra_tasks_toml(path: Path) -> list[DatasetTask]:
    """Load additional tasks from a TOML file to combine with :data:`BUILTIN_TASKS`."""
    return load_tasks_toml(path)


def context_dataset_as_dict(
    report: DatasetReport,
    *,
    builtin_task_count: int = 0,
    extra_task_count: int = 0,
) -> dict[str, Any]:
    """Convert a context dataset report to a JSON-serialisable dictionary.

    The returned dict has the same structure as :func:`dataset_as_dict` plus
    two extra keys, ``builtin_task_count`` and ``extra_task_count``, that
    record how many tasks came from the built-in list versus a user-supplied
    TOML file.
    """
    result = dataset_as_dict(report)
    result["builtin_task_count"] = builtin_task_count
    result["extra_task_count"] = extra_task_count
    return result


__all__ = [
    "BUILTIN_TASKS",
    "ContextDatasetBuilderConfig",
    "build_context_dataset",
    "context_dataset_as_dict",
    "load_extra_tasks_toml",
]
