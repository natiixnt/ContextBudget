from __future__ import annotations

"""Multi-tier file representation builder for progressive budget packing.

For each ranked file, :func:`build_tiers` generates an ordered list of
representations with decreasing detail (and token cost):

    full -> symbol -> slice -> snippet -> summary

The caller (:func:`compress_ranked_files`) then selects the best affordable
tier per file, degrading before dropping.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from redcon.compressors.language_chunks import SliceRelationshipContext
from redcon.compressors.summarizers import SummaryRequest, SummarizationService
from redcon.config import CompressionSettings
from redcon.cache.summary_cache import SummaryCacheBackend
from redcon.core.tokens import estimate_tokens
from redcon.schemas.models import RankedFile


@dataclass(slots=True)
class Tier:
    """Single representation of a file at a specific compression level."""

    strategy: str  # "full", "symbol", "slice", "snippet", "summary"
    text: str
    tokens: int
    chunk_strategy: str
    chunk_reason: str
    selected_ranges: list[dict[str, int | str]] = field(default_factory=list)
    symbols: list[dict[str, int | str | bool]] = field(default_factory=list)


@dataclass(slots=True)
class FileTiers:
    """All viable representations for a single file, ordered by detail."""

    path: str
    ranked: RankedFile
    raw_tokens: int
    full_text: str
    line_count: int
    tiers: list[Tier]  # Ordered: most detailed first


_TEST_FILE_PATTERNS = ("test_", "_test.", "/test/", "/tests/", "spec_", "_spec.")
_UTILITY_FILE_PATTERNS = (
    "/config.", "/config/", "helpers.", "utils.", "/utils/",
    "validators.", "constants.", "settings.", "/types.", "exceptions.", "errors.",
)


def _is_test_file(path: str) -> bool:
    p = path.lower().replace("\\", "/")
    return any(pat in p for pat in _TEST_FILE_PATTERNS)


def _is_utility_file(path: str) -> bool:
    p = path.lower().replace("\\", "/")
    return any(pat in p for pat in _UTILITY_FILE_PATTERNS)


# Strategy ordering: most detailed first.
_STRATEGY_PRIORITY = {"full": 0, "symbol": 1, "slice": 2, "snippet": 3, "summary": 4}


def build_tiers(
    ranked: RankedFile,
    full_text: str,
    keywords: list[str],
    relationship_context: SliceRelationshipContext,
    cfg: CompressionSettings,
    summarizer: SummarizationService,
    cache: SummaryCacheBackend,
    symbol_selection: Any | None,
    slice_selection: Any | None,
    symbol_failure_reason: str = "",
    token_estimator: Callable[[str], int] = estimate_tokens,
) -> list[Tier]:
    """Build an ordered list of tiers for *ranked*, most detailed first.

    *symbol_selection* and *slice_selection* are pre-computed by the caller
    (which owns the monkeypatchable imports).  This keeps the tier builder
    free of extraction dependencies.
    """
    from redcon.compressors.context_compressor import (
        _collapse_blank_lines,
        _selected_ranges_with_reason,
        _snippet_from_text,
        _strip_decorative_dividers,
        _strip_docstrings_in_text,
        _truncate_data_blocks_in_text,
    )

    file_record = ranked.file
    path = file_record.path
    relevance_score = ranked.heuristic_score if ranked.heuristic_score > 0 else ranked.score
    line_count = len(full_text.splitlines())
    raw_tokens = token_estimator(full_text)
    is_test = _is_test_file(path)
    is_utility = _is_utility_file(path)
    is_py_file = file_record.extension == ".py"

    def _cleanup(text: str, strategy: str) -> str:
        if strategy != "full":
            if is_py_file:
                text = _strip_docstrings_in_text(text)
                text = _truncate_data_blocks_in_text(text)
            text = _strip_decorative_dividers(text)
            text = _collapse_blank_lines(text)
        else:
            text = _collapse_blank_lines(text)
        return text

    tiers: list[Tier] = []

    # -- Eligibility flags (mirroring the greedy decision tree) --
    force_compress = (is_test or is_utility) and symbol_selection is not None
    score_qualifies = relevance_score >= cfg.snippet_score_threshold

    # Slice metrics
    slice_text_raw = f"# {path}\n{slice_selection.text}" if slice_selection is not None else ""
    slice_tokens_raw = token_estimator(slice_text_raw) if slice_text_raw else 0
    slice_line_count = 0
    if slice_selection is not None:
        for item in slice_selection.selected_ranges:
            start = int(item.get("start_line", 0) or 0)
            end = int(item.get("end_line", 0) or 0)
            if start > 0 and end >= start:
                slice_line_count += end - start + 1
    slice_supported = slice_selection is not None and slice_tokens_raw > 0 and slice_line_count > 0
    relationship_driven = bool(
        relationship_context.outgoing_related_paths
        or relationship_context.incoming_related_paths
        or relationship_context.incoming_entrypoint_paths
    )
    slice_is_reduction = bool(
        slice_supported and slice_line_count < line_count and slice_tokens_raw < raw_tokens
    )

    # Symbol vs slice comparison
    symbol_text_raw = f"# {path}\n{symbol_selection.text}" if symbol_selection is not None else ""
    symbol_tokens_raw = token_estimator(symbol_text_raw) if symbol_text_raw else 0
    symbol_beats_slice = bool(
        symbol_selection is not None
        and relationship_driven
        and slice_supported
        and symbol_tokens_raw < slice_tokens_raw
    )

    eligible_symbol = symbol_selection is not None and (
        score_qualifies or force_compress or symbol_beats_slice
    )
    eligible_slice = slice_supported and (
        slice_is_reduction or score_qualifies or relationship_driven
    )
    eligible_snippet = score_qualifies

    # -- Tier: full --
    if raw_tokens <= cfg.full_file_threshold_tokens and not force_compress:
        full_content = f"# Full: {path}\n{full_text}"
        full_content = _cleanup(full_content, "full")
        tiers.append(Tier(
            strategy="full",
            text=full_content,
            tokens=token_estimator(full_content),
            chunk_strategy="full-file",
            chunk_reason="file fits within full-file threshold",
            selected_ranges=[{
                "start_line": 1,
                "end_line": line_count,
                "kind": "full",
                "reason": "file fits within full-file threshold",
            }] if line_count > 0 else [],
        ))

    # -- Tier: symbol --
    if eligible_symbol:
        sym_text = _cleanup(symbol_text_raw, "symbol")
        chunk_reason = symbol_selection.chunk_reason
        if symbol_failure_reason:
            chunk_reason = f"{symbol_failure_reason}; {chunk_reason}"
        tiers.append(Tier(
            strategy="symbol",
            text=sym_text,
            tokens=token_estimator(sym_text),
            chunk_strategy=symbol_selection.chunk_strategy,
            chunk_reason=chunk_reason,
            selected_ranges=_selected_ranges_with_reason(
                symbol_selection.selected_ranges, chunk_reason,
            ),
            symbols=symbol_selection.symbols,
        ))

    # -- Tier: slice --
    # When symbol extraction failed, the slice fallback is labeled "snippet"
    # with the failure reason prepended (matching the old greedy behavior).
    if eligible_slice:
        slc_text = _cleanup(slice_text_raw, "slice")
        slc_tokens = token_estimator(slc_text)
        chunk_reason = slice_selection.chunk_reason
        slice_strategy_label = "slice"
        if symbol_failure_reason and not eligible_symbol:
            chunk_reason = f"{symbol_failure_reason}; {chunk_reason}"
            slice_strategy_label = "snippet"
        if slc_tokens > 0:
            tiers.append(Tier(
                strategy=slice_strategy_label,
                text=slc_text,
                tokens=slc_tokens,
                chunk_strategy=slice_selection.chunk_strategy,
                chunk_reason=chunk_reason,
                selected_ranges=slice_selection.selected_ranges,
            ))

    # -- Tier: snippet (keyword-window) --
    if eligible_snippet:
        snippet = _snippet_from_text(path, full_text, keywords, cfg)
        if snippet.text.strip():
            snip_text = _cleanup(snippet.text, "snippet")
            snip_tokens = token_estimator(snip_text)
            chunk_reason = "fallback keyword-window snippet selection"
            if symbol_failure_reason and not eligible_symbol:
                chunk_reason = f"{symbol_failure_reason}; {chunk_reason}"
            if snip_tokens > 0:
                tiers.append(Tier(
                    strategy="snippet",
                    text=snip_text,
                    tokens=snip_tokens,
                    chunk_strategy="keyword-window",
                    chunk_reason=chunk_reason,
                    selected_ranges=_selected_ranges_with_reason(
                        snippet.selected_ranges, chunk_reason,
                    ),
                ))

    # -- Tier: summary (always available as fallback) --
    cache_key = f"{file_record.path}:{file_record.size_bytes}:{file_record.content_hash}"
    summary = summarizer.summarize(
        cache=cache,
        cache_key_prefix=cache_key,
        request=SummaryRequest(
            task="",
            path=path,
            text=full_text,
            line_limit=cfg.summary_preview_lines,
            score=ranked.score,
            keywords=keywords,
        ),
    )
    sum_text = _cleanup(summary.text, "summary")
    sum_tokens = token_estimator(sum_text)
    preview_end = min(cfg.summary_preview_lines, line_count)
    tiers.append(Tier(
        strategy="summary",
        text=sum_text,
        tokens=max(1, sum_tokens),
        chunk_strategy=summary.chunk_strategy,
        chunk_reason=summary.chunk_reason,
        selected_ranges=[{
            "start_line": 1,
            "end_line": preview_end,
            "kind": "summary",
            "reason": summary.chunk_reason,
        }] if preview_end > 0 else [],
    ))

    # Order by detail level (most detailed first).
    seen_strategies: set[str] = set()
    unique_tiers: list[Tier] = []
    for tier in sorted(tiers, key=lambda t: _STRATEGY_PRIORITY.get(t.strategy, 99)):
        if tier.strategy not in seen_strategies:
            seen_strategies.add(tier.strategy)
            unique_tiers.append(tier)
    return unique_tiers
