from __future__ import annotations

"""Context architecture advisor: detect and rank files for agent-friendly refactoring."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence

from redcon.config import RedconConfig
from redcon.core.tokens import estimate_tokens
from redcon.scorers.import_graph import build_import_graph
from redcon.scanners.repository import scan_repository
from redcon.schemas.models import FileRecord

SUGGESTION_SPLIT_FILE = "split_file"
SUGGESTION_EXTRACT_MODULE = "extract_module"
SUGGESTION_REDUCE_DEPENDENCIES = "reduce_dependencies"

_DEFAULT_LARGE_FILE_TOKENS = 500
_DEFAULT_HIGH_FANIN = 5
_DEFAULT_HIGH_FANOUT = 10
_DEFAULT_HIGH_FREQUENCY_RATE = 0.5


@dataclass(slots=True)
class AdviceSuggestion:
    """A single architecture suggestion for improving agent-friendliness."""

    path: str
    suggestion: str
    reason: str
    estimated_token_impact: int
    signals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AdviseReport:
    """Full context architecture advice report."""

    command: str
    generated_at: str
    repo: str
    scanned_files: int
    runs_analyzed: int
    large_file_token_threshold: int
    high_fanin_threshold: int
    high_fanout_threshold: int
    suggestions: list[AdviceSuggestion]
    summary: dict[str, int]


def _estimate_file_tokens(record: FileRecord) -> int:
    try:
        text = Path(record.absolute_path).read_text(encoding="utf-8", errors="ignore")
        return estimate_tokens(text)
    except OSError:
        return record.size_bytes // 4


def _load_pack_artifacts(history_inputs: Sequence[str | Path]) -> list[dict[str, Any]]:
    """Load valid pack JSON artifacts from the given paths."""
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_input in history_inputs:
        candidate = Path(raw_input).expanduser()
        if not candidate.exists():
            continue
        paths = sorted(candidate.rglob("*.json")) if candidate.is_dir() else [candidate]
        for path in paths:
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            if not isinstance(data.get("compressed_context"), list):
                continue
            command = data.get("command")
            if isinstance(command, str) and command and command != "pack":
                continue
            artifacts.append(data)
    return artifacts


def _compute_pack_frequency(
    files: list[FileRecord],
    artifacts: list[dict[str, Any]],
) -> dict[str, float]:
    """Return per-file inclusion rate across pack artifacts (keyed by file path)."""
    if not artifacts:
        return {}
    total = len(artifacts)
    counts: dict[str, int] = {}
    for artifact in artifacts:
        seen_in_run: set[str] = set()
        for item in artifact.get("compressed_context", []):
            if isinstance(item, dict):
                path = str(item.get("path", "") or "")
                if path:
                    seen_in_run.add(path)
        for path in seen_in_run:
            counts[path] = counts.get(path, 0) + 1
    return {record.path: round(counts.get(record.path, 0) / total, 4) for record in files}


def run_advise(
    repo: Path,
    *,
    config: RedconConfig,
    history: Sequence[str | Path] | None = None,
    large_file_tokens: int | None = None,
    high_fanin: int | None = None,
    high_fanout: int | None = None,
    high_frequency_rate: float | None = None,
    top_suggestions: int = 25,
) -> AdviseReport:
    """Scan a repository and produce ranked architecture suggestions."""

    eff_large = large_file_tokens if large_file_tokens is not None else _DEFAULT_LARGE_FILE_TOKENS
    eff_fanin = high_fanin if high_fanin is not None else _DEFAULT_HIGH_FANIN
    eff_fanout = high_fanout if high_fanout is not None else _DEFAULT_HIGH_FANOUT
    eff_freq = high_frequency_rate if high_frequency_rate is not None else _DEFAULT_HIGH_FREQUENCY_RATE

    files = scan_repository(
        repo,
        max_file_size_bytes=config.scan.max_file_size_bytes,
        preview_chars=config.scan.preview_chars,
        include_globs=config.scan.include_globs or None,
        ignore_globs=config.scan.ignore_globs or None,
    )

    graph = build_import_graph(files)
    token_map: dict[str, int] = {r.path: _estimate_file_tokens(r) for r in files}

    pack_artifacts: list[dict[str, Any]] = []
    if history:
        pack_artifacts = _load_pack_artifacts(history)
    frequency_map = _compute_pack_frequency(files, pack_artifacts)
    runs_analyzed = len(pack_artifacts)

    raw_suggestions: list[AdviceSuggestion] = []

    for record in files:
        path = record.path
        tokens = token_map.get(path, 0)
        fan_in = len(graph.incoming.get(path, set()))
        fan_out = len(graph.outgoing.get(path, set()))
        freq = frequency_map.get(path, 0.0)

        signals: list[str] = []
        if tokens >= eff_large:
            signals.append(f"large_file({tokens}_tokens)")
        if fan_in >= eff_fanin:
            signals.append(f"high_fanin({fan_in}_importers)")
        if fan_out >= eff_fanout:
            signals.append(f"high_fanout({fan_out}_deps)")
        if runs_analyzed > 0 and freq >= eff_freq:
            signals.append(f"frequent_in_packs({freq * 100:.0f}%_of_{runs_analyzed}_runs)")

        if not signals:
            continue

        if fan_in >= eff_fanin and tokens >= eff_large:
            # Large file that many others depend on: extracting a focused API module
            # reduces cost proportionally across all importers.
            impact = tokens * max(1, fan_in)
            reason = (
                f"Imported by {fan_in} files and contains {tokens} tokens. "
                "Extracting a smaller public interface module reduces context cost for all importers."
            )
            if runs_analyzed > 0 and freq >= eff_freq:
                reason += f" Included in {freq * 100:.0f}% of packs."
            raw_suggestions.append(AdviceSuggestion(
                path=path,
                suggestion=SUGGESTION_EXTRACT_MODULE,
                reason=reason,
                estimated_token_impact=impact,
                signals=signals,
            ))
        elif tokens >= eff_large:
            # Large file: splitting creates smaller, focused modules loaded on demand.
            freq_multiplier = max(1, round(freq * runs_analyzed)) if runs_analyzed else 1
            impact = tokens * freq_multiplier
            reason = f"Contains {tokens} tokens, exceeding the large-file threshold of {eff_large}."
            if runs_analyzed > 0 and freq >= eff_freq:
                reason += f" Included in {freq * 100:.0f}% of context packs."
            raw_suggestions.append(AdviceSuggestion(
                path=path,
                suggestion=SUGGESTION_SPLIT_FILE,
                reason=reason,
                estimated_token_impact=impact,
                signals=signals,
            ))
        elif fan_in >= eff_fanin:
            # High fan-in but not large: exposing a slimmer public API module would
            # shrink the context surface imported by each dependent.
            impact = tokens * fan_in
            reason = (
                f"Imported by {fan_in} files. Extracting a focused public API module "
                "would let importers pull in less context."
            )
            raw_suggestions.append(AdviceSuggestion(
                path=path,
                suggestion=SUGGESTION_EXTRACT_MODULE,
                reason=reason,
                estimated_token_impact=impact,
                signals=signals,
            ))
        elif fan_out >= eff_fanout:
            # High fan-out: trimming dependencies narrows transitive context.
            dep_tokens = sum(token_map.get(dep, 0) for dep in graph.outgoing.get(path, set()))
            impact = dep_tokens
            reason = (
                f"Imports {fan_out} files (~{dep_tokens} tokens of transitive context). "
                "Reducing dependencies narrows the context footprint."
            )
            raw_suggestions.append(AdviceSuggestion(
                path=path,
                suggestion=SUGGESTION_REDUCE_DEPENDENCIES,
                reason=reason,
                estimated_token_impact=impact,
                signals=signals,
            ))

    # Deduplicate: one suggestion per path, keeping the highest impact.
    by_path: dict[str, AdviceSuggestion] = {}
    for s in raw_suggestions:
        if s.path not in by_path or s.estimated_token_impact > by_path[s.path].estimated_token_impact:
            by_path[s.path] = s

    ranked = sorted(by_path.values(), key=lambda s: (-s.estimated_token_impact, s.path))
    ranked = ranked[:top_suggestions]

    counts: dict[str, int] = {
        SUGGESTION_SPLIT_FILE: 0,
        SUGGESTION_EXTRACT_MODULE: 0,
        SUGGESTION_REDUCE_DEPENDENCIES: 0,
    }
    for s in ranked:
        counts[s.suggestion] = counts.get(s.suggestion, 0) + 1

    return AdviseReport(
        command="advise",
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo=str(repo),
        scanned_files=len(files),
        runs_analyzed=runs_analyzed,
        large_file_token_threshold=eff_large,
        high_fanin_threshold=eff_fanin,
        high_fanout_threshold=eff_fanout,
        suggestions=ranked,
        summary={
            "total_suggestions": len(ranked),
            "split_file": counts[SUGGESTION_SPLIT_FILE],
            "extract_module": counts[SUGGESTION_EXTRACT_MODULE],
            "reduce_dependencies": counts[SUGGESTION_REDUCE_DEPENDENCIES],
        },
    )


def advise_as_dict(report: AdviseReport) -> dict[str, Any]:
    """Convert an AdviseReport to a JSON-serializable dictionary."""
    return asdict(report)
