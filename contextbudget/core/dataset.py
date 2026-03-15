from __future__ import annotations

"""Dataset builder: run benchmark tasks in batch and export reduction metrics."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found, no-redef, assignment]
        except ImportError:
            tomllib = None  # type: ignore[assignment]


@dataclass(slots=True)
class DatasetTask:
    """A single task definition for the dataset builder."""

    description: str
    name: str = ""


@dataclass(slots=True)
class DatasetEntry:
    """Per-task benchmark result stored in the dataset."""

    task: str
    task_name: str
    baseline_tokens: int
    optimized_tokens: int
    reduction_pct: float
    benchmark: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DatasetReport:
    """Full dataset report aggregating multiple benchmark runs."""

    command: str
    generated_at: str
    repo: str
    task_count: int
    aggregate: dict[str, Any]
    entries: list[DatasetEntry]


def load_tasks_toml(path: Path) -> list[DatasetTask]:
    """Load task definitions from a TOML file.

    Expected format::

        [[tasks]]
        name = "Add authentication"
        description = "Add JWT authentication middleware to protect API routes"

        [[tasks]]
        description = "Refactor the database layer to use a repository pattern"
    """
    if tomllib is None:
        raise RuntimeError("TOML parser unavailable. Install 'tomli' for Python < 3.11.")

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    task_list = raw.get("tasks", [])
    if not isinstance(task_list, list):
        raise ValueError(f"Expected [[tasks]] array in {path}, got {type(task_list).__name__}")

    tasks: list[DatasetTask] = []
    for i, item in enumerate(task_list):
        if not isinstance(item, dict):
            raise ValueError(f"Task #{i} in {path} is not a table.")
        desc = item.get("description", "")
        if not isinstance(desc, str) or not desc.strip():
            raise ValueError(f"Task #{i} in {path} must have a non-empty 'description' field.")
        name = str(item.get("name", "")).strip()
        tasks.append(DatasetTask(description=desc.strip(), name=name))

    if not tasks:
        raise ValueError(f"No [[tasks]] entries found in {path}.")

    return tasks


def _extract_optimized_tokens(benchmark: dict[str, Any]) -> int:
    """Return the estimated input tokens for the best (compressed_pack) strategy."""
    for strategy in benchmark.get("strategies", []):
        if strategy.get("strategy") == "compressed_pack":
            return int(strategy.get("estimated_input_tokens", 0) or 0)
    return int(benchmark.get("baseline_full_context_tokens", 0) or 0)


def _reduction_pct(baseline: int, optimized: int) -> float:
    if baseline <= 0:
        return 0.0
    return round(max(0.0, (baseline - optimized) / baseline * 100), 2)


def run_dataset(
    tasks: list[DatasetTask],
    repo: Path,
    *,
    run_benchmark_fn: Any,  # Callable[[str, Path, ...], dict]
    max_tokens: int | None = None,
    top_files: int | None = None,
) -> DatasetReport:
    """Run a benchmark for each task and aggregate results into a dataset.

    Parameters
    ----------
    tasks:
        List of :class:`DatasetTask` instances to benchmark.
    repo:
        Repository path to benchmark against.
    run_benchmark_fn:
        Callable with signature ``(task, repo, *, max_tokens, top_files) -> dict``.
        In production this is ``ContextBudgetEngine.benchmark``.
    max_tokens:
        Token budget forwarded to each benchmark run.
    top_files:
        Top-files limit forwarded to each benchmark run.
    """
    entries: list[DatasetEntry] = []

    for t in tasks:
        bm = run_benchmark_fn(
            task=t.description,
            repo=repo,
            max_tokens=max_tokens,
            top_files=top_files,
        )
        baseline = int(bm.get("baseline_full_context_tokens", 0) or 0)
        optimized = _extract_optimized_tokens(bm)
        entries.append(DatasetEntry(
            task=t.description,
            task_name=t.name,
            baseline_tokens=baseline,
            optimized_tokens=optimized,
            reduction_pct=_reduction_pct(baseline, optimized),
            benchmark=bm,
        ))

    total_baseline = sum(e.baseline_tokens for e in entries)
    total_optimized = sum(e.optimized_tokens for e in entries)
    n = len(entries)
    avg_baseline = round(total_baseline / n, 2) if n else 0.0
    avg_optimized = round(total_optimized / n, 2) if n else 0.0
    avg_reduction = round(sum(e.reduction_pct for e in entries) / n, 2) if n else 0.0

    return DatasetReport(
        command="dataset",
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo=str(repo),
        task_count=n,
        aggregate={
            "total_baseline_tokens": total_baseline,
            "total_optimized_tokens": total_optimized,
            "avg_baseline_tokens": avg_baseline,
            "avg_optimized_tokens": avg_optimized,
            "avg_reduction_pct": avg_reduction,
        },
        entries=entries,
    )


def dataset_as_dict(report: DatasetReport) -> dict[str, Any]:
    """Convert a DatasetReport to a JSON-serialisable dictionary."""
    return {
        "command": report.command,
        "generated_at": report.generated_at,
        "repo": report.repo,
        "task_count": report.task_count,
        "aggregate": report.aggregate,
        "entries": [
            {
                "task": e.task,
                "task_name": e.task_name,
                "baseline_tokens": e.baseline_tokens,
                "optimized_tokens": e.optimized_tokens,
                "reduction_pct": e.reduction_pct,
                "benchmark": e.benchmark,
            }
            for e in report.entries
        ],
    }
