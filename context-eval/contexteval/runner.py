"""Evaluation loop: worktrees, greedy packing, coverage scoring."""

from __future__ import annotations

import subprocess
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from contexteval.adapters import Adapter
from contexteval.tasks import Task, is_source_path


# One shared estimator for every tool keeps the comparison fair; chars/4 is
# the same heuristic redcon documents (within ~15% of tiktoken).
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class ToolResult:
    tool: str
    coverage_pct: float
    tokens_used: int
    files_included: int
    hits: int
    error: str | None = None

    @property
    def tokens_per_point(self) -> float | None:
        if self.coverage_pct <= 0:
            return None
        return self.tokens_used / self.coverage_pct


@dataclass
class TaskResult:
    task: Task
    universe_size: int
    ground_truth: tuple[str, ...]
    tools: dict[str, ToolResult] = field(default_factory=dict)


def _list_universe(root: Path) -> list[str]:
    skip_dirs = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}
    universe: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in skip_dirs for part in rel.split("/")):
            continue
        if is_source_path(rel):
            universe.append(rel)
    return universe


def _pack(root: Path, ranking: list[str], budget: int) -> tuple[list[str], int]:
    """Greedy fill: take files in ranking order while they fit the budget."""
    included: list[str] = []
    used = 0
    for rel in ranking:
        try:
            cost = estimate_tokens((root / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if used + cost > budget:
            continue
        included.append(rel)
        used += cost
    return included, used


def evaluate(
    repo: Path,
    tasks: list[Task],
    adapters: dict[str, Adapter],
    *,
    budget: int = 16_000,
    log=print,
) -> list[TaskResult]:
    results: list[TaskResult] = []
    for index, task in enumerate(tasks, start=1):
        with tempfile.TemporaryDirectory(prefix="ctxeval-") as tmp:
            worktree = Path(tmp) / "wt"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), task.parent],
                capture_output=True,
                text=True,
                check=True,
            )
            try:
                universe = _list_universe(worktree)
                universe_set = set(universe)
                ground_truth = tuple(f for f in task.ground_truth if f in universe_set)
                if not ground_truth:
                    log(f"[{index}/{len(tasks)}] skip (ground truth empty): {task.description}")
                    continue

                task_result = TaskResult(
                    task=task, universe_size=len(universe), ground_truth=ground_truth
                )
                log(
                    f"[{index}/{len(tasks)}] {task.description}  "
                    f"(gt={len(ground_truth)}, universe={len(universe)})"
                )

                for name, adapter in adapters.items():
                    try:
                        ranking = adapter(task.description, worktree, list(universe))
                        included, used = _pack(worktree, ranking, budget)
                        hits = sum(1 for f in ground_truth if f in set(included))
                        coverage = 100.0 * hits / len(ground_truth)
                        task_result.tools[name] = ToolResult(
                            tool=name,
                            coverage_pct=coverage,
                            tokens_used=used,
                            files_included=len(included),
                            hits=hits,
                        )
                        log(
                            f"    {name:<14} coverage={coverage:5.1f}%  "
                            f"tokens={used:>6,}  files={len(included)}"
                        )
                    except Exception:
                        task_result.tools[name] = ToolResult(
                            tool=name,
                            coverage_pct=0.0,
                            tokens_used=0,
                            files_included=0,
                            hits=0,
                            error=traceback.format_exc(limit=2),
                        )
                        log(f"    {name:<14} ERROR (see results.json)")
                results.append(task_result)
            finally:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
    return results
