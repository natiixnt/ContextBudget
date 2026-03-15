from __future__ import annotations

"""Run-history aggregation for context heatmap analytics."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


@dataclass(slots=True)
class HeatmapHistoryPoint:
    """Per-run token contribution for a file or directory."""

    artifact_path: str
    generated_at: str
    original_tokens: int
    compressed_tokens: int
    saved_tokens: int


@dataclass(slots=True)
class HeatmapStats:
    """Aggregated token statistics for a file or directory."""

    path: str
    total_original_tokens: int
    total_compressed_tokens: int
    total_saved_tokens: int
    inclusion_count: int
    run_count: int
    inclusion_rate: float
    average_original_tokens: float
    average_compressed_tokens: float
    average_saved_tokens: float
    max_original_tokens: int
    max_compressed_tokens: int
    max_saved_tokens: int
    last_included_at: str
    history: list[HeatmapHistoryPoint] = field(default_factory=list)


@dataclass(slots=True)
class HeatmapRunSummary:
    """Compact metadata for an analyzed run artifact."""

    artifact_path: str
    generated_at: str
    task: str
    repo: str
    files_included: int
    estimated_input_tokens: int
    estimated_saved_tokens: int


@dataclass(slots=True)
class HeatmapSkippedArtifact:
    """Artifact skipped during history aggregation."""

    artifact_path: str
    reason: str


@dataclass(slots=True)
class HeatmapReport:
    """Serializable heatmap report exported by CLI and engine."""

    command: str
    generated_at: str
    history_inputs: list[str]
    artifacts_scanned: list[str]
    runs_analyzed: int
    unique_files: int
    unique_directories: int
    analyzed_runs: list[HeatmapRunSummary] = field(default_factory=list)
    skipped_artifacts: list[HeatmapSkippedArtifact] = field(default_factory=list)
    files: list[HeatmapStats] = field(default_factory=list)
    directories: list[HeatmapStats] = field(default_factory=list)
    top_token_heavy_files: list[HeatmapStats] = field(default_factory=list)
    top_token_heavy_directories: list[HeatmapStats] = field(default_factory=list)
    most_frequently_included_files: list[HeatmapStats] = field(default_factory=list)
    largest_token_savings_opportunities: list[HeatmapStats] = field(default_factory=list)


@dataclass(slots=True)
class _Accumulator:
    """Mutable internal aggregator for a single file or directory."""

    path: str
    total_original_tokens: int = 0
    total_compressed_tokens: int = 0
    total_saved_tokens: int = 0
    inclusion_count: int = 0
    max_original_tokens: int = 0
    max_compressed_tokens: int = 0
    max_saved_tokens: int = 0
    last_included_at: str = ""
    history: list[HeatmapHistoryPoint] = field(default_factory=list)
    run_ids: set[str] = field(default_factory=set)

    def add(
        self,
        *,
        artifact_path: str,
        generated_at: str,
        original_tokens: int,
        compressed_tokens: int,
    ) -> None:
        saved_tokens = max(0, original_tokens - compressed_tokens)
        self.total_original_tokens += original_tokens
        self.total_compressed_tokens += compressed_tokens
        self.total_saved_tokens += saved_tokens
        self.inclusion_count += 1
        self.max_original_tokens = max(self.max_original_tokens, original_tokens)
        self.max_compressed_tokens = max(self.max_compressed_tokens, compressed_tokens)
        self.max_saved_tokens = max(self.max_saved_tokens, saved_tokens)
        self.run_ids.add(artifact_path)
        self.history.append(
            HeatmapHistoryPoint(
                artifact_path=artifact_path,
                generated_at=generated_at,
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                saved_tokens=saved_tokens,
            )
        )
        if _timestamp_sort_key(generated_at) >= _timestamp_sort_key(self.last_included_at):
            self.last_included_at = generated_at

    def finalize(self, *, total_runs: int) -> HeatmapStats:
        run_count = len(self.run_ids)
        divisor = max(1, self.inclusion_count)
        history = sorted(self.history, key=lambda item: (_timestamp_sort_key(item.generated_at), item.artifact_path))
        return HeatmapStats(
            path=self.path,
            total_original_tokens=self.total_original_tokens,
            total_compressed_tokens=self.total_compressed_tokens,
            total_saved_tokens=self.total_saved_tokens,
            inclusion_count=self.inclusion_count,
            run_count=run_count,
            inclusion_rate=round(run_count / max(1, total_runs), 4),
            average_original_tokens=round(self.total_original_tokens / divisor, 2),
            average_compressed_tokens=round(self.total_compressed_tokens / divisor, 2),
            average_saved_tokens=round(self.total_saved_tokens / divisor, 2),
            max_original_tokens=self.max_original_tokens,
            max_compressed_tokens=self.max_compressed_tokens,
            max_saved_tokens=self.max_saved_tokens,
            last_included_at=self.last_included_at,
            history=history,
        )


def _timestamp_sort_key(value: str) -> tuple[int, str]:
    if not value:
        return (1, "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return (0, value)
    return (0, parsed.astimezone(timezone.utc).isoformat())


def _to_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_pack_artifact(data: Mapping[str, Any]) -> bool:
    compressed_context = data.get("compressed_context")
    if not isinstance(compressed_context, list):
        return False
    command = data.get("command")
    if isinstance(command, str) and command and command != "pack":
        return False
    return True


def _collect_artifact_paths(history_inputs: Sequence[str | Path]) -> list[Path]:
    artifacts: list[Path] = []
    seen: set[str] = set()
    for raw_input in history_inputs:
        candidate = Path(raw_input).expanduser()
        if not candidate.exists():
            raise ValueError(f"History path not found: {candidate}")
        if candidate.is_dir():
            matches = sorted(path.resolve() for path in candidate.rglob("*.json") if path.is_file())
        else:
            matches = [candidate.resolve()]
        for match in matches:
            key = str(match)
            if key in seen:
                continue
            seen.add(key)
            artifacts.append(match)
    return artifacts


def _directory_prefixes(path: str) -> list[str]:
    slash_index = path.find("/")
    colon_index = path.find(":")
    prefix = ""
    remainder = path
    if colon_index != -1 and (slash_index == -1 or colon_index < slash_index):
        prefix = path[:colon_index]
        remainder = path[colon_index + 1 :].lstrip("/")

    relative = PurePosixPath(remainder)
    dir_parts = [part for part in relative.parent.parts if part not in ("", ".")]
    directories: list[str] = []
    if prefix:
        directories.append(prefix)
    current_parts: list[str] = []
    for part in dir_parts:
        current_parts.append(part)
        if prefix:
            directories.append("/".join([prefix, *current_parts]))
        else:
            directories.append("/".join(current_parts))
    return directories


def _sorted_runs(runs: list[HeatmapRunSummary]) -> list[HeatmapRunSummary]:
    return sorted(runs, key=lambda item: (_timestamp_sort_key(item.generated_at), item.artifact_path))


def _sort_by_token_weight(items: list[HeatmapStats]) -> list[HeatmapStats]:
    return sorted(items, key=lambda item: (-item.total_compressed_tokens, -item.inclusion_count, item.path))


def _sort_by_frequency(items: list[HeatmapStats]) -> list[HeatmapStats]:
    return sorted(items, key=lambda item: (-item.inclusion_count, -item.total_compressed_tokens, item.path))


def _sort_by_savings(items: list[HeatmapStats]) -> list[HeatmapStats]:
    return sorted(items, key=lambda item: (-item.total_saved_tokens, -item.total_original_tokens, item.path))


def build_heatmap_report(
    history_inputs: Sequence[str | Path] | None = None,
    *,
    limit: int = 10,
) -> HeatmapReport:
    """Aggregate pack run history into file and directory heatmaps."""

    if limit <= 0:
        raise ValueError("--limit must be greater than 0")

    resolved_inputs = list(history_inputs or [Path.cwd()])
    artifact_paths = _collect_artifact_paths(resolved_inputs)
    if not artifact_paths:
        raise ValueError("No JSON artifacts found in provided history paths.")

    file_totals: dict[str, _Accumulator] = {}
    directory_totals: dict[str, _Accumulator] = {}
    analyzed_runs: list[HeatmapRunSummary] = []
    skipped_artifacts: list[HeatmapSkippedArtifact] = []

    for artifact_path in artifact_paths:
        artifact_label = str(artifact_path)
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except OSError as exc:
            skipped_artifacts.append(HeatmapSkippedArtifact(artifact_path=artifact_label, reason=str(exc)))
            continue
        except json.JSONDecodeError as exc:
            skipped_artifacts.append(
                HeatmapSkippedArtifact(artifact_path=artifact_label, reason=f"invalid JSON: {exc.msg}")
            )
            continue

        if not isinstance(payload, dict) or not _is_pack_artifact(payload):
            skipped_artifacts.append(
                HeatmapSkippedArtifact(artifact_path=artifact_label, reason="not a pack run artifact")
            )
            continue

        compressed_context = payload.get("compressed_context", [])
        if not isinstance(compressed_context, list):
            skipped_artifacts.append(
                HeatmapSkippedArtifact(artifact_path=artifact_label, reason="compressed_context must be a list")
            )
            continue

        budget = payload.get("budget", {})
        budget_data = budget if isinstance(budget, dict) else {}
        generated_at = str(payload.get("generated_at", "") or "")
        files_included = payload.get("files_included", [])
        included_count = len(files_included) if isinstance(files_included, list) else 0
        analyzed_runs.append(
            HeatmapRunSummary(
                artifact_path=artifact_label,
                generated_at=generated_at,
                task=str(payload.get("task", "") or ""),
                repo=str(payload.get("repo", "") or ""),
                files_included=included_count if included_count else len(compressed_context),
                estimated_input_tokens=_to_int(budget_data.get("estimated_input_tokens")),
                estimated_saved_tokens=_to_int(budget_data.get("estimated_saved_tokens")),
            )
        )

        for item in compressed_context:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "") or "")
            if not path:
                continue
            original_tokens = _to_int(item.get("original_tokens"))
            compressed_tokens = _to_int(item.get("compressed_tokens"))

            file_totals.setdefault(path, _Accumulator(path=path)).add(
                artifact_path=artifact_label,
                generated_at=generated_at,
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
            )
            for directory in _directory_prefixes(path):
                directory_totals.setdefault(directory, _Accumulator(path=directory)).add(
                    artifact_path=artifact_label,
                    generated_at=generated_at,
                    original_tokens=original_tokens,
                    compressed_tokens=compressed_tokens,
                )

    total_runs = len(analyzed_runs)
    if total_runs == 0:
        raise ValueError("No pack run artifacts found in provided history paths.")

    files = [_accumulator.finalize(total_runs=total_runs) for _accumulator in file_totals.values()]
    directories = [_accumulator.finalize(total_runs=total_runs) for _accumulator in directory_totals.values()]

    files_by_tokens = _sort_by_token_weight(files)
    directories_by_tokens = _sort_by_token_weight(directories)
    files_by_frequency = _sort_by_frequency(files)
    files_by_savings = _sort_by_savings(files)

    return HeatmapReport(
        command="heatmap",
        generated_at=datetime.now(timezone.utc).isoformat(),
        history_inputs=[str(Path(item).expanduser()) for item in resolved_inputs],
        artifacts_scanned=[str(path) for path in artifact_paths],
        runs_analyzed=total_runs,
        unique_files=len(files),
        unique_directories=len(directories),
        analyzed_runs=_sorted_runs(analyzed_runs),
        skipped_artifacts=sorted(skipped_artifacts, key=lambda item: item.artifact_path),
        files=files_by_tokens,
        directories=directories_by_tokens,
        top_token_heavy_files=files_by_tokens[:limit],
        top_token_heavy_directories=directories_by_tokens[:limit],
        most_frequently_included_files=files_by_frequency[:limit],
        largest_token_savings_opportunities=files_by_savings[:limit],
    )


def heatmap_as_dict(report: HeatmapReport) -> dict[str, Any]:
    """Convert heatmap report to a JSON-serializable dictionary."""

    return asdict(report)
