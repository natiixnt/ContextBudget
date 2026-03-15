from __future__ import annotations

"""Token savings profiler - explains where savings come from in a pack run."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Stage names (canonical, stable)
# ---------------------------------------------------------------------------

STAGE_SYMBOL_EXTRACTION = "symbol_extraction"
STAGE_SLICING = "slicing"
STAGE_COMPRESSION = "compression"
STAGE_SNIPPET = "snippet"
STAGE_CACHE_REUSE = "cache_reuse"
STAGE_DELTA = "delta"
STAGE_FULL = "full"

_ORDERED_STAGES = (
    STAGE_CACHE_REUSE,
    STAGE_SYMBOL_EXTRACTION,
    STAGE_SLICING,
    STAGE_COMPRESSION,
    STAGE_SNIPPET,
    STAGE_DELTA,
    STAGE_FULL,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StageSavings:
    stage: str
    tokens_saved: int = 0
    file_count: int = 0


@dataclass(slots=True)
class FileSavingsRecord:
    path: str
    stage: str
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    chunk_strategy: str = ""
    cache_status: str = ""


@dataclass(slots=True)
class TokenSavingsProfile:
    command: str
    run_json: str
    generated_at: str
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    savings_pct: float
    by_stage: dict[str, StageSavings] = field(default_factory=dict)
    per_file: list[FileSavingsRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage classification
# ---------------------------------------------------------------------------


def _classify_stage(entry: dict[str, Any]) -> str:
    """Map a compressed-context entry to its savings stage."""
    cache_status = str(entry.get("cache_status") or "")
    strategy = str(entry.get("strategy") or "")
    chunk_strategy = str(entry.get("chunk_strategy") or "")

    if cache_status == "reused":
        return STAGE_CACHE_REUSE

    if strategy == "symbol" or chunk_strategy.startswith("symbol-extract"):
        return STAGE_SYMBOL_EXTRACTION

    if strategy == "slice" or chunk_strategy.startswith("lang-"):
        return STAGE_SLICING

    if strategy == "summary" or chunk_strategy.startswith("summary"):
        return STAGE_COMPRESSION

    if strategy == "snippet" or chunk_strategy.startswith("snippet"):
        return STAGE_SNIPPET

    return STAGE_FULL


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------


def build_savings_profile(run_data: dict[str, Any], *, run_json: str = "") -> TokenSavingsProfile:
    """Build a token savings profile from a pack run JSON artifact."""

    compressed_context = run_data.get("compressed_context", [])
    if not isinstance(compressed_context, list):
        compressed_context = []

    by_stage: dict[str, StageSavings] = {stage: StageSavings(stage=stage) for stage in _ORDERED_STAGES}
    per_file: list[FileSavingsRecord] = []

    total_before = 0
    total_after = 0

    for entry in compressed_context:
        if not isinstance(entry, dict):
            continue

        path = str(entry.get("path") or "")
        original = int(entry.get("original_tokens") or 0)
        compressed = int(entry.get("compressed_tokens") or 0)
        saved = max(0, original - compressed)
        stage = _classify_stage(entry)

        total_before += original
        total_after += compressed

        by_stage[stage].tokens_saved += saved
        by_stage[stage].file_count += 1

        per_file.append(
            FileSavingsRecord(
                path=path,
                stage=stage,
                tokens_before=original,
                tokens_after=compressed,
                tokens_saved=saved,
                chunk_strategy=str(entry.get("chunk_strategy") or ""),
                cache_status=str(entry.get("cache_status") or ""),
            )
        )

    # Delta savings are tracked separately in the run report
    delta = run_data.get("delta")
    if isinstance(delta, dict):
        delta_budget = delta.get("budget", {})
        if isinstance(delta_budget, dict):
            delta_saved = int(delta_budget.get("tokens_saved") or 0)
            if delta_saved > 0:
                by_stage[STAGE_DELTA].tokens_saved += delta_saved

    total_saved = max(0, total_before - total_after)
    savings_pct = round((total_saved / total_before) * 100.0, 1) if total_before > 0 else 0.0

    return TokenSavingsProfile(
        command="profile",
        run_json=run_json,
        generated_at=datetime.now(timezone.utc).isoformat(),
        tokens_before=total_before,
        tokens_after=total_after,
        tokens_saved=total_saved,
        savings_pct=savings_pct,
        by_stage=by_stage,
        per_file=per_file,
    )


def savings_profile_as_dict(profile: TokenSavingsProfile) -> dict[str, Any]:
    """Convert a profile to a JSON-serializable dict."""
    d = asdict(profile)
    # Convert by_stage from dict-of-dataclass to dict-of-dict (asdict already handles this)
    return d


__all__ = [
    "STAGE_CACHE_REUSE",
    "STAGE_COMPRESSION",
    "STAGE_DELTA",
    "STAGE_FULL",
    "STAGE_SLICING",
    "STAGE_SNIPPET",
    "STAGE_SYMBOL_EXTRACTION",
    "FileSavingsRecord",
    "StageSavings",
    "TokenSavingsProfile",
    "build_savings_profile",
    "savings_profile_as_dict",
]
