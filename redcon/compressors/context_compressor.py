from __future__ import annotations

"""Pack/compression stage for budgeted context generation."""

import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from redcon.compressors.representations import FileTiers

from redcon.cache.summary_cache import SummaryCacheBackend
from redcon.config import CompressionSettings, SummarizationSettings
from redcon.compressors.language_chunks import (
    SliceRelationshipContext,
    select_language_aware_chunks,
)
from redcon.compressors.symbols import (
    select_symbol_aware_chunks,
    _truncate_data_blocks,
    _STUB_SCORE_THRESHOLD,
)
from redcon.compressors.summarizers import SummaryRequest, SummarizationService
from redcon.core.text import task_keywords
from redcon.core.tokens import estimate_tokens
from redcon.schemas.models import CacheReport, CompressedFile, RankedFile, SummarizerReport
from redcon.scorers.import_graph import ImportGraph, build_import_graph


@dataclass(slots=True)
class CompressionResult:
    """Result bundle produced by the compression stage."""

    compressed_files: list[CompressedFile]
    files_included: list[str]
    files_skipped: list[str]
    estimated_input_tokens: int
    estimated_saved_tokens: int
    duplicate_reads_prevented: int
    cache: CacheReport
    cache_hits: int
    quality_risk_estimate: str
    summarizer: SummarizerReport
    degraded_files: list[str] = field(default_factory=list)
    degradation_savings: int = 0


@dataclass(slots=True)
class _SnippetSelection:
    """Fallback line-window snippet with explicit range metadata."""

    text: str
    selected_ranges: list[dict[str, int | str]]


from redcon.compressors.file_patterns import _is_test_file, _is_utility_file  # noqa: E402


def _strip_docstrings_in_text(text: str) -> str:
    """Strip triple-quoted docstrings that follow def/class lines in arbitrary Python text.

    Applied as a post-pass to slice and snippet output where per-symbol
    docstring stripping has not already run.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.endswith(":") and any(
            stripped.startswith(kw)
            for kw in ("def ", "async def ", "class ", "):", "        ):")
        ):
            out.append(line)
            i += 1
            blank_start = i
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i >= len(lines):
                out.extend(lines[blank_start:i])
                break
            first_body = lines[i].strip()
            found = False
            for delim in ('"""', "'''"):
                if first_body.startswith(delim):
                    rest = first_body[len(delim):]
                    if rest.endswith(delim) and len(rest) >= len(delim):
                        i += 1
                        found = True
                        break
                    i += 1
                    while i < len(lines) and delim not in lines[i]:
                        i += 1
                    i += 1
                    found = True
                    break
            if not found:
                out.extend(lines[blank_start:i])
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of 2+ consecutive blank lines to a single blank line."""
    out: list[str] = []
    blanks = 0
    for line in text.splitlines():
        if not line.strip():
            blanks += 1
            if blanks <= 1:
                out.append(line)
        else:
            blanks = 0
            out.append(line)
    return "\n".join(out)


_DIVIDER_RE = re.compile(r"^\s*#\s*[-=*#_]{15,}\s*$")


def _truncate_data_blocks_in_text(text: str) -> str:
    """Apply _truncate_data_blocks to arbitrary source text."""
    return "\n".join(_truncate_data_blocks(text.splitlines()))


def _strip_decorative_dividers(text: str) -> str:
    """Remove banner-style comment dividers that carry no semantic content.

    Strips lines whose entire content (after ``#``) is 15+ repeated
    non-alphanumeric characters, e.g. ``# --------`` or ``# ========``.
    """
    return "\n".join(
        line for line in text.splitlines() if not _DIVIDER_RE.match(line)
    )


def _dedup_imports(text: str, seen_imports: set[str]) -> str:
    """Strip import lines already seen in earlier compressed files.

    Operates on the raw compressed text string. Updates *seen_imports* in place
    so subsequent calls omit duplicates discovered here.
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and stripped in seen_imports:
            continue
        if stripped.startswith(("import ", "from ")):
            seen_imports.add(stripped)
        out.append(line)
    return "\n".join(out)


def _format_keywords(keywords: list[str]) -> str:
    if not keywords:
        return "task context"
    return ", ".join(keywords[:3])


def _selected_line_count(selected_ranges: list[dict[str, int | str]]) -> int:
    total = 0
    for item in selected_ranges:
        start = int(item.get("start_line", 0) or 0)
        end = int(item.get("end_line", 0) or 0)
        if start > 0 and end >= start:
            total += end - start + 1
    return total


def _selected_ranges_with_reason(
    selected_ranges: list[dict[str, int | str]],
    reason: str,
) -> list[dict[str, int | str]]:
    output: list[dict[str, int | str]] = []
    for item in selected_ranges:
        updated = dict(item)
        updated.setdefault("reason", reason)
        output.append(updated)
    return output


def _fragment_locator(selected_ranges: list[dict[str, int | str]]) -> str:
    symbol_parts: list[str] = []
    range_parts: list[str] = []
    for item in selected_ranges:
        kind = str(item.get("kind", "slice")).strip() or "slice"
        symbol = str(item.get("symbol", "")).strip()
        if symbol:
            symbol_parts.append(f"{kind}:{symbol}")
            continue
        start = int(item.get("start_line", 0) or 0)
        end = int(item.get("end_line", 0) or 0)
        if start > 0 and end >= start:
            range_parts.append(f"{kind}:{start}-{end}")
    if symbol_parts:
        return "symbols=" + ",".join(dict.fromkeys(symbol_parts))
    if range_parts:
        return "ranges=" + ",".join(range_parts)
    return "ranges=unknown"


def _fragment_cache_key(path: str, selected_ranges: list[dict[str, int | str]], text: str) -> str:
    content_hash = sha256(text.encode("utf-8")).hexdigest()
    return f"fragment:{path}:{_fragment_locator(selected_ranges)}:{content_hash}"


def _fragment_reference_id(cache_key: str) -> str:
    return f"cb-frag:{sha256(cache_key.encode('utf-8')).hexdigest()[:16]}"


def _fragment_reference_text(reference_id: str) -> str:
    return f"@cached-summary:{reference_id}"


def _render_selected_ranges(lines: list[str], selected_ranges: list[dict[str, int | str]]) -> str:
    parts: list[str] = []
    for item in selected_ranges:
        start = max(1, int(item.get("start_line", 1) or 1))
        end = max(start, int(item.get("end_line", start) or start))
        body = "\n".join(lines[start - 1 : end])
        parts.append(body)
    return "\n\n...\n\n".join(parts)


def _merge_line_numbers(line_numbers: list[int]) -> list[tuple[int, int]]:
    if not line_numbers:
        return []

    merged: list[tuple[int, int]] = []
    start = line_numbers[0]
    end = line_numbers[0]
    for number in line_numbers[1:]:
        if number == end + 1:
            end = number
            continue
        merged.append((start, end))
        start = number
        end = number
    merged.append((start, end))
    return merged


def _range_keyword_reason(lines: list[str], start: int, end: int, keywords: list[str]) -> str:
    if not keywords:
        return "keyword proximity: task context"
    lowered = "\n".join(lines[start : end + 1]).lower()
    hits = [keyword for keyword in keywords if keyword and keyword in lowered]
    if not hits:
        return f"keyword proximity: {_format_keywords(keywords)}"
    return f"keyword proximity: {', '.join(hits[:3])}"


def _snippet_from_text(path: str, text: str, keywords: list[str], settings: CompressionSettings) -> _SnippetSelection:
    raw_lines = text.splitlines()
    if not raw_lines:
        return _SnippetSelection(text=f"# {path}\n", selected_ranges=[])

    hit_indexes: list[int] = []
    for idx, line in enumerate(raw_lines):
        lower = line.lower()
        if any(keyword in lower for keyword in keywords):
            hit_indexes.append(idx)

    selected_ranges: list[dict[str, int | str]] = []
    if not hit_indexes:
        end = min(len(raw_lines), settings.snippet_fallback_lines) - 1
        if end >= 0:
            selected_ranges.append(
                {
                    "start_line": 1,
                    "end_line": end + 1,
                    "kind": "fallback",
                    "reason": "fallback leading preview because no task keywords matched",
                }
            )
    else:
        selected_lines: list[int] = []
        seen: set[int] = set()
        limit = max(1, settings.snippet_total_line_limit)
        for idx in hit_indexes[: settings.snippet_hit_limit]:
            start = max(0, idx - settings.snippet_context_lines)
            end = min(len(raw_lines) - 1, idx + settings.snippet_context_lines)
            for line_no in range(start, end + 1):
                if line_no in seen:
                    continue
                seen.add(line_no)
                selected_lines.append(line_no)
                if len(selected_lines) >= limit:
                    break
            if len(selected_lines) >= limit:
                break

        for start, end in _merge_line_numbers(sorted(selected_lines)):
            selected_ranges.append(
                {
                    "start_line": start + 1,
                    "end_line": end + 1,
                    "kind": "keyword-window",
                    "reason": _range_keyword_reason(raw_lines, start, end, keywords),
                }
            )

    rendered = _render_selected_ranges(raw_lines, selected_ranges) if selected_ranges else ""
    return _SnippetSelection(
        text=f"# {path}\n{rendered}".rstrip(),
        selected_ranges=selected_ranges,
    )


def _build_slice_relationship_contexts(
    ranked_files: list[RankedFile],
    import_graph: ImportGraph | None = None,
) -> dict[str, SliceRelationshipContext]:
    if not ranked_files:
        return {}

    files = [item.file for item in ranked_files]
    graph = import_graph if import_graph is not None else build_import_graph(files)
    ranked_paths = {item.file.path for item in ranked_files}
    contexts: dict[str, SliceRelationshipContext] = {}
    for path in ranked_paths:
        outgoing = tuple(sorted((graph.outgoing.get(path, set()) & ranked_paths) - {path}))
        incoming = tuple(sorted((graph.incoming.get(path, set()) & ranked_paths) - {path}))
        entrypoints = tuple(sorted((graph.incoming.get(path, set()) & graph.entrypoints) - {path}))
        contexts[path] = SliceRelationshipContext(
            outgoing_related_paths=outgoing,
            incoming_related_paths=incoming,
            incoming_entrypoint_paths=entrypoints,
        )
    return contexts


def _build_risk_estimate(
    cfg: CompressionSettings,
    files_skipped: list[str],
    ranked_files: list[RankedFile],
    total_compressed: int,
    total_raw: int,
) -> str:
    skipped_ratio = len(files_skipped) / max(1, len(ranked_files))
    compression_ratio = total_compressed / max(1, total_raw)
    bounded_ratio = min(1.0, max(0.0, compression_ratio))
    total_weight = cfg.risk_skip_weight + cfg.risk_compression_weight
    if total_weight <= 0:
        total_weight = 1.0
    skip_weight = cfg.risk_skip_weight / total_weight
    compression_weight = cfg.risk_compression_weight / total_weight
    risk_score = skip_weight * skipped_ratio + compression_weight * bounded_ratio
    if risk_score < 0.25:
        return "low"
    if risk_score < 0.5:
        return "medium"
    return "high"


def _finalize_entry(
    file_record: "RankedFile.file",  # type: ignore[name-defined]
    tier: "Tier",  # type: ignore[name-defined]
    raw_tokens: int,
    seen_imports: set[str],
    cache: SummaryCacheBackend,
    token_estimator: Callable[[str], int],
) -> CompressedFile:
    """Build a CompressedFile from a chosen tier, apply import dedup and cache."""
    text = _dedup_imports(tier.text, seen_imports) if tier.strategy != "full" else tier.text
    compressed_tokens = token_estimator(text)

    fragment_cache_key_val = _fragment_cache_key(file_record.path, tier.selected_ranges, text)
    fragment_reference = _fragment_reference_id(fragment_cache_key_val)
    cached_reference = cache.get_fragment(fragment_cache_key_val)
    cache_status = ""
    effective_chunk_reason = tier.chunk_reason
    if cached_reference is not None:
        cache_status = "reused"
        effective_chunk_reason = f"reused cached fragment; {tier.chunk_reason}"
    else:
        if cache.put_fragment(fragment_cache_key_val, fragment_reference):
            cache_status = "stored"

    return CompressedFile(
        path=file_record.path,
        strategy=tier.strategy,
        original_tokens=raw_tokens,
        compressed_tokens=compressed_tokens,
        text=text,
        chunk_strategy=tier.chunk_strategy,
        chunk_reason=effective_chunk_reason,
        selected_ranges=tier.selected_ranges,
        symbols=tier.symbols,
        cache_reference=(cached_reference or fragment_reference) if cache_status else "",
        cache_status=cache_status,
        relative_path=file_record.relative_path,
        repo_label=file_record.repo_label,
    )


def compress_ranked_files(
    task: str,
    repo: Path,
    ranked_files: list[RankedFile],
    max_tokens: int,
    cache: SummaryCacheBackend,
    settings: CompressionSettings | None = None,
    summarization_settings: SummarizationSettings | None = None,
    duplicate_hash_cache_enabled: bool = True,
    token_estimator: Callable[[str], int] = estimate_tokens,
    import_graph: ImportGraph | None = None,
) -> CompressionResult:
    """Compress ranked files under a token budget.

    When ``progressive_packer_enabled`` is set in *settings*, files are
    pre-computed at multiple compression tiers and the packer degrades
    representations before dropping files entirely.
    """

    cfg = settings if settings is not None else CompressionSettings()
    keywords = task_keywords(task)
    seen_imports: set[str] = set()

    duplicate_reads_prevented = 0
    seen_hashes: set[str] = set()
    slice_relationships = _build_slice_relationship_contexts(ranked_files, import_graph=import_graph)
    summarizer = SummarizationService(
        backend=(summarization_settings.backend if summarization_settings is not None else "deterministic"),
        adapter_name=(summarization_settings.adapter if summarization_settings is not None else ""),
    )

    # -- Phase 0: Read files and filter duplicates --
    from redcon.compressors.representations import FileTiers, build_tiers

    prepared: list[FileTiers] = []
    files_skipped: list[str] = []
    total_raw = 0

    for ranked in ranked_files:
        file_record = ranked.file
        if duplicate_hash_cache_enabled and file_record.content_hash in seen_hashes:
            duplicate_reads_prevented += 1
            files_skipped.append(file_record.path)
            continue
        if duplicate_hash_cache_enabled:
            seen_hashes.add(file_record.content_hash)

        try:
            full_text = Path(file_record.absolute_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            files_skipped.append(file_record.path)
            continue

        raw_tokens = token_estimator(full_text)
        total_raw += raw_tokens
        relationship_context = slice_relationships.get(file_record.path, SliceRelationshipContext())

        relevance_score = ranked.heuristic_score if ranked.heuristic_score > 0 else ranked.score
        is_test = _is_test_file(file_record.path)

        # Adaptive line budget (same logic as the old greedy path).
        if cfg.adaptive_line_budget and cfg.snippet_score_threshold > 0:
            _score_ratio = relevance_score / cfg.snippet_score_threshold
            _factor = min(cfg.adaptive_line_budget_max_factor, max(0.5, _score_ratio))
            effective_line_budget = max(1, int(cfg.snippet_total_line_limit * _factor))
            effective_max_symbols = max(2, min(8, round(4 * _factor)))
        else:
            effective_line_budget = cfg.snippet_total_line_limit
            effective_max_symbols = 4

        effective_stub_threshold = 7.0 if is_test else _STUB_SCORE_THRESHOLD

        # Pre-compute extractions here so that monkeypatches on this module
        # propagate correctly (tests patch context_compressor.select_*).
        symbol_selection = None
        symbol_failure_reason = ""
        if cfg.symbol_extraction_enabled:
            try:
                symbol_selection = select_symbol_aware_chunks(
                    file_path=file_record.path,
                    text=full_text,
                    keywords=keywords,
                    line_budget=effective_line_budget,
                    max_symbols=effective_max_symbols,
                    stub_score_threshold=effective_stub_threshold,
                )
            except Exception as exc:
                symbol_failure_reason = f"symbol extraction failed: {exc}"
                symbol_selection = None

        slice_selection = select_language_aware_chunks(
            file_path=file_record.path,
            text=full_text,
            keywords=keywords,
            line_budget=effective_line_budget,
            relationship_context=relationship_context,
            surrounding_lines=min(1, max(0, cfg.snippet_context_lines)),
        )

        tiers = build_tiers(
            ranked=ranked,
            full_text=full_text,
            keywords=keywords,
            relationship_context=relationship_context,
            cfg=cfg,
            summarizer=summarizer,
            cache=cache,
            symbol_selection=symbol_selection,
            slice_selection=slice_selection,
            symbol_failure_reason=symbol_failure_reason,
            token_estimator=token_estimator,
        )

        prepared.append(FileTiers(
            path=file_record.path,
            ranked=ranked,
            raw_tokens=raw_tokens,
            full_text=full_text,
            line_count=len(full_text.splitlines()),
            tiers=tiers,
        ))

    if cfg.progressive_packer_enabled:
        result = _compress_progressive(
            prepared=prepared,
            max_tokens=max_tokens,
            cfg=cfg,
            cache=cache,
            seen_imports=seen_imports,
            token_estimator=token_estimator,
            files_skipped=files_skipped,
            total_raw=total_raw,
            duplicate_reads_prevented=duplicate_reads_prevented,
            ranked_files=ranked_files,
            summarizer=summarizer,
        )
    else:
        result = _compress_greedy(
            prepared=prepared,
            max_tokens=max_tokens,
            cfg=cfg,
            cache=cache,
            seen_imports=seen_imports,
            token_estimator=token_estimator,
            files_skipped=files_skipped,
            total_raw=total_raw,
            duplicate_reads_prevented=duplicate_reads_prevented,
            ranked_files=ranked_files,
            summarizer=summarizer,
        )
    return result


def _compress_greedy(
    prepared: list[FileTiers],
    max_tokens: int,
    cfg: CompressionSettings,
    cache: SummaryCacheBackend,
    seen_imports: set[str],
    token_estimator: Callable[[str], int],
    files_skipped: list[str],
    total_raw: int,
    duplicate_reads_prevented: int,
    ranked_files: list[RankedFile],
    summarizer: SummarizationService,
) -> CompressionResult:
    """Original greedy strategy: pick best tier per file, skip if over budget."""

    compressed_files: list[CompressedFile] = []
    files_included: list[str] = []
    total_compressed = 0

    for ft in prepared:
        if not ft.tiers:
            files_skipped.append(ft.path)
            continue
        # Pick the first (most detailed) tier - matches old behavior.
        tier = ft.tiers[0]
        entry = _finalize_entry(
            ft.ranked.file, tier, ft.raw_tokens, seen_imports, cache, token_estimator,
        )
        if total_compressed + entry.compressed_tokens > max_tokens:
            files_skipped.append(ft.path)
            continue
        total_compressed += entry.compressed_tokens
        compressed_files.append(entry)
        files_included.append(ft.path)

    risk = _build_risk_estimate(cfg, files_skipped, ranked_files, total_compressed, total_raw)
    cache_snapshot = cache.snapshot()
    return CompressionResult(
        compressed_files=compressed_files,
        files_included=files_included,
        files_skipped=files_skipped,
        estimated_input_tokens=total_compressed,
        estimated_saved_tokens=max(0, total_raw - total_compressed),
        duplicate_reads_prevented=duplicate_reads_prevented,
        cache=cache_snapshot,
        cache_hits=cache_snapshot.hits,
        quality_risk_estimate=risk,
        summarizer=summarizer.snapshot(),
        degraded_files=[],
        degradation_savings=0,
    )


def _compress_progressive(
    prepared: list[FileTiers],
    max_tokens: int,
    cfg: CompressionSettings,
    cache: SummaryCacheBackend,
    seen_imports: set[str],
    token_estimator: Callable[[str], int],
    files_skipped: list[str],
    total_raw: int,
    duplicate_reads_prevented: int,
    ranked_files: list[RankedFile],
    summarizer: SummarizationService,
) -> CompressionResult:
    """Progressive packer: degrade representations before dropping files.

    Pass 1 (tentative): assign each file its best affordable tier.
    Pass 2 (degradation): downgrade the lowest-scoring included files to
    reclaim budget for files that were skipped.
    """
    # -- Pass 1: tentative assignment --
    # Each entry: (FileTiers, chosen_tier_index)
    assignments: list[tuple[FileTiers, int]] = []
    skipped: list[FileTiers] = []
    budget_remaining = max_tokens

    for ft in prepared:
        if not ft.tiers:
            files_skipped.append(ft.path)
            continue
        assigned = False
        for i, tier in enumerate(ft.tiers):
            if tier.tokens <= budget_remaining:
                assignments.append((ft, i))
                budget_remaining -= tier.tokens
                assigned = True
                break
        if not assigned:
            skipped.append(ft)

    # -- Pass 2: degradation rounds --
    degraded_files: list[str] = []
    degradation_savings = 0

    for _round in range(cfg.max_degradation_rounds):
        if not skipped:
            break

        # Sort assignments by score ascending - degrade lowest-scoring first.
        degradable = [
            (idx, ft, tier_idx)
            for idx, (ft, tier_idx) in enumerate(assignments)
            if tier_idx + 1 < len(ft.tiers)
        ]
        degradable.sort(
            key=lambda x: (
                x[1].ranked.heuristic_score
                if x[1].ranked.heuristic_score > 0
                else x[1].ranked.score
            ),
        )

        still_skipped: list[FileTiers] = []
        for skipped_ft in skipped:
            fitted = False
            # Find cheapest tier that could fit this skipped file.
            cheapest_tier = skipped_ft.tiers[-1]

            # Try freeing budget by degrading included files.
            for deg_idx, deg_ft, deg_tier_idx in degradable:
                if fitted:
                    break
                current_tier = deg_ft.tiers[deg_tier_idx]
                next_tier = deg_ft.tiers[deg_tier_idx + 1]
                freed = current_tier.tokens - next_tier.tokens
                if freed <= 0:
                    continue

                # Check if degrading frees enough for any tier of the skipped file.
                new_budget = budget_remaining + freed
                for s_tier_idx, s_tier in enumerate(skipped_ft.tiers):
                    if s_tier.tokens <= new_budget:
                        # Degrade the included file.
                        assignments[deg_idx] = (deg_ft, deg_tier_idx + 1)
                        budget_remaining += freed
                        degraded_files.append(deg_ft.path)
                        degradation_savings += freed

                        # Include the skipped file.
                        assignments.append((skipped_ft, s_tier_idx))
                        budget_remaining -= s_tier.tokens
                        fitted = True
                        break

            if not fitted:
                still_skipped.append(skipped_ft)

        skipped = still_skipped

    for ft in skipped:
        files_skipped.append(ft.path)

    # -- Pass 3: finalize entries in original order --
    # Rebuild assignments in the order files were prepared so that import
    # deduplication remains deterministic.
    assignment_map: dict[str, int] = {ft.path: tier_idx for ft, tier_idx in assignments}
    compressed_files: list[CompressedFile] = []
    files_included: list[str] = []
    total_compressed = 0

    for ft in prepared:
        if ft.path not in assignment_map:
            continue
        tier_idx = assignment_map[ft.path]
        tier = ft.tiers[tier_idx]
        entry = _finalize_entry(
            ft.ranked.file, tier, ft.raw_tokens, seen_imports, cache, token_estimator,
        )
        total_compressed += entry.compressed_tokens
        compressed_files.append(entry)
        files_included.append(ft.path)

    risk = _build_risk_estimate(cfg, files_skipped, ranked_files, total_compressed, total_raw)
    cache_snapshot = cache.snapshot()
    return CompressionResult(
        compressed_files=compressed_files,
        files_included=files_included,
        files_skipped=files_skipped,
        estimated_input_tokens=total_compressed,
        estimated_saved_tokens=max(0, total_raw - total_compressed),
        duplicate_reads_prevented=duplicate_reads_prevented,
        cache=cache_snapshot,
        cache_hits=cache_snapshot.hits,
        quality_risk_estimate=risk,
        summarizer=summarizer.snapshot(),
        degraded_files=degraded_files,
        degradation_savings=degradation_savings,
    )
