from __future__ import annotations

"""Agent read profiler - analyzes how a coding agent read repository files.

Reads a pack run artifact (run.json) and identifies:
- duplicate reads  - the same file path appears more than once in the context
- unnecessary reads - low-relevance files that still cost significant tokens
- high token-cost reads - individual files above a token-size threshold
- tokens wasted - tokens consumed by duplicate or unnecessary reads
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------

#: Files with original_tokens >= this value are flagged as high-cost reads.
HIGH_COST_READ_THRESHOLD: int = 500

#: Files with a relevance score <= this value are candidates for "unnecessary".
UNNECESSARY_READ_SCORE_THRESHOLD: float = 1.0

#: Files must cost at least this many tokens to be flagged as unnecessary
#: (avoids flagging tiny low-score files).
UNNECESSARY_READ_MIN_TOKENS: int = 50


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FileReadRecord:
    """Read record for a single file path seen in the packed context."""

    path: str
    original_tokens: int
    compressed_tokens: int
    strategy: str
    chunk_strategy: str
    read_count: int
    """Number of times this file path appears in compressed_context."""
    relevance_score: float
    """Relevance score from ranked_files; 0.0 if not ranked."""
    is_duplicate: bool
    is_unnecessary: bool
    is_high_cost: bool
    waste_tokens: int
    """Tokens wasted: (read_count - 1) * original_tokens for duplicates, or
    original_tokens for unnecessary reads (whichever applies first)."""
    reasons: list[str]
    """Human-readable explanations of why flags were raised."""


@dataclass(slots=True)
class ReadProfileReport:
    """Full agent read profile derived from a single pack run artifact."""

    command: str
    run_json: str
    generated_at: str

    # --- summary ---
    total_files_read: int
    """Total file entries in compressed_context (including duplicates)."""
    unique_files_read: int
    """Number of distinct file paths in compressed_context."""
    duplicate_reads: int
    """Paths appearing more than once (counted as extra reads beyond the first)."""
    duplicate_reads_prevented: int
    """Deduplicated reads that were already filtered by the packer (from budget field)."""
    unnecessary_reads: int
    """Files flagged as unnecessary (low relevance + high token cost)."""
    high_cost_reads: int
    """Files with original_tokens >= HIGH_COST_READ_THRESHOLD."""
    tokens_wasted_duplicates: int
    """Tokens attributed to duplicate reads."""
    tokens_wasted_unnecessary: int
    """Tokens attributed to unnecessary reads."""
    tokens_wasted_total: int
    """tokens_wasted_duplicates + tokens_wasted_unnecessary."""

    # --- per-file records ---
    files: list[FileReadRecord] = field(default_factory=list)
    """All file read records, ordered by original_tokens descending."""

    # --- sub-lists (derived for convenience) ---
    duplicate_files: list[FileReadRecord] = field(default_factory=list)
    unnecessary_files: list[FileReadRecord] = field(default_factory=list)
    high_cost_files: list[FileReadRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_score_index(run_data: dict[str, Any]) -> dict[str, float]:
    """Return a path -> relevance_score mapping from the ranked_files list."""
    score_index: dict[str, float] = {}
    ranked = run_data.get("ranked_files", [])
    if not isinstance(ranked, list):
        return score_index
    for entry in ranked:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        score = float(entry.get("score") or 0.0)
        if path:
            score_index[path] = score
    return score_index


def _read_count_index(compressed_context: list[dict[str, Any]]) -> dict[str, int]:
    """Return a path -> count mapping of how many times each path appears."""
    counts: dict[str, int] = {}
    for entry in compressed_context:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        if path:
            counts[path] = counts.get(path, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------


def build_read_profile(run_data: dict[str, Any], *, run_json: str = "") -> ReadProfileReport:
    """Build a read profile from a pack run artifact.

    Parameters
    ----------
    run_data:
        Deserialized run.json dict produced by ``contextbudget pack``.
    run_json:
        Optional path string recorded in the profile for traceability.
    """
    compressed_context = run_data.get("compressed_context", [])
    if not isinstance(compressed_context, list):
        compressed_context = []

    budget = run_data.get("budget", {})
    duplicate_reads_prevented = int(
        (budget.get("duplicate_reads_prevented") if isinstance(budget, dict) else None) or 0
    )

    score_index = _build_score_index(run_data)
    read_counts = _read_count_index(compressed_context)

    # Track which paths we've already emitted a record for (first occurrence wins).
    seen: dict[str, FileReadRecord] = {}
    all_records: list[FileReadRecord] = []

    total_files_read = 0
    duplicate_count = 0
    tokens_wasted_duplicates = 0
    tokens_wasted_unnecessary = 0

    for entry in compressed_context:
        if not isinstance(entry, dict):
            continue

        path = str(entry.get("path") or "")
        if not path:
            continue

        total_files_read += 1
        original = int(entry.get("original_tokens") or 0)
        compressed = int(entry.get("compressed_tokens") or 0)
        strategy = str(entry.get("strategy") or "")
        chunk_strategy = str(entry.get("chunk_strategy") or "")
        read_count = read_counts.get(path, 1)
        relevance_score = score_index.get(path, 0.0)

        if path in seen:
            # Already fully accounted for in the first occurrence - skip.
            continue

        is_duplicate = read_count > 1
        is_high_cost = original >= HIGH_COST_READ_THRESHOLD
        is_unnecessary = (
            original >= UNNECESSARY_READ_MIN_TOKENS
            and relevance_score > 0.0  # only flag if we have a score
            and relevance_score <= UNNECESSARY_READ_SCORE_THRESHOLD
        )

        waste_tokens = 0
        reasons: list[str] = []

        if is_duplicate:
            extra_reads = read_count - 1
            dup_waste = extra_reads * original
            waste_tokens += dup_waste
            tokens_wasted_duplicates += dup_waste
            duplicate_count += extra_reads
            reasons.append(
                f"read {read_count}x - {extra_reads} extra read(s) waste {dup_waste} tokens"
            )

        if is_unnecessary:
            # Waste = tokens spent on a file with low relevance.
            waste_tokens += original
            tokens_wasted_unnecessary += original
            reasons.append(
                f"low relevance score {relevance_score:.2f} "
                f"(<= {UNNECESSARY_READ_SCORE_THRESHOLD}) but costs {original} tokens"
            )

        if is_high_cost:
            reasons.append(
                f"high token cost: {original} tokens "
                f"(>= {HIGH_COST_READ_THRESHOLD} threshold)"
            )

        record = FileReadRecord(
            path=path,
            original_tokens=original,
            compressed_tokens=compressed,
            strategy=strategy,
            chunk_strategy=chunk_strategy,
            read_count=read_count,
            relevance_score=relevance_score,
            is_duplicate=is_duplicate,
            is_unnecessary=is_unnecessary,
            is_high_cost=is_high_cost,
            waste_tokens=waste_tokens,
            reasons=reasons,
        )
        seen[path] = record
        all_records.append(record)

    # Sort by original_tokens descending so the most expensive files appear first.
    all_records.sort(key=lambda r: r.original_tokens, reverse=True)

    duplicate_files = [r for r in all_records if r.is_duplicate]
    unnecessary_files = [r for r in all_records if r.is_unnecessary]
    high_cost_files = [r for r in all_records if r.is_high_cost]

    tokens_wasted_total = tokens_wasted_duplicates + tokens_wasted_unnecessary

    return ReadProfileReport(
        command="read-profiler",
        run_json=run_json,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_files_read=total_files_read,
        unique_files_read=len(seen),
        duplicate_reads=duplicate_count,
        duplicate_reads_prevented=duplicate_reads_prevented,
        unnecessary_reads=len(unnecessary_files),
        high_cost_reads=len(high_cost_files),
        tokens_wasted_duplicates=tokens_wasted_duplicates,
        tokens_wasted_unnecessary=tokens_wasted_unnecessary,
        tokens_wasted_total=tokens_wasted_total,
        files=all_records,
        duplicate_files=duplicate_files,
        unnecessary_files=unnecessary_files,
        high_cost_files=high_cost_files,
    )


def read_profile_as_dict(report: ReadProfileReport) -> dict[str, Any]:
    """Convert a read profile to a JSON-serialisable dict."""
    return asdict(report)


__all__ = [
    "HIGH_COST_READ_THRESHOLD",
    "UNNECESSARY_READ_MIN_TOKENS",
    "UNNECESSARY_READ_SCORE_THRESHOLD",
    "FileReadRecord",
    "ReadProfileReport",
    "build_read_profile",
    "read_profile_as_dict",
]
