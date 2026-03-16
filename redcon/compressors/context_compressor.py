from __future__ import annotations

"""Pack/compression stage for budgeted context generation."""

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable

from redcon.cache.summary_cache import SummaryCacheBackend
from redcon.config import CompressionSettings, SummarizationSettings
from redcon.compressors.language_chunks import (
    SliceRelationshipContext,
    select_language_aware_chunks,
)
from redcon.compressors.symbols import select_symbol_aware_chunks
from redcon.compressors.summarizers import SummaryRequest, SummarizationService
from redcon.core.text import task_keywords
from redcon.core.tokens import estimate_tokens
from redcon.schemas.models import CacheReport, CompressedFile, RankedFile, SummarizerReport
from redcon.scorers.import_graph import build_import_graph


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


@dataclass(slots=True)
class _SnippetSelection:
    """Fallback line-window snippet with explicit range metadata."""

    text: str
    selected_ranges: list[dict[str, int | str]]


_TEST_FILE_PATTERNS = ("test_", "_test.", "/test/", "/tests/", "spec_", "_spec.")

_UTILITY_FILE_PATTERNS = (
    "/config.", "/config/", "helpers.", "utils.", "/utils/",
    "validators.", "constants.", "settings.", "/types.", "exceptions.", "errors.",
)


def _is_test_file(path: str) -> bool:
    """Return True if the file path looks like a test file."""
    p = path.lower().replace("\\", "/")
    return any(pat in p for pat in _TEST_FILE_PATTERNS)


def _is_utility_file(path: str) -> bool:
    """Return True if the file is a utility/config/helper module.

    Utility files rarely contain task-specific logic so their bodies are
    stubbed - only imports and signatures are retained, saving 40-60%.
    """
    p = path.lower().replace("\\", "/")
    return any(pat in p for pat in _UTILITY_FILE_PATTERNS)


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


def _build_slice_relationship_contexts(ranked_files: list[RankedFile]) -> dict[str, SliceRelationshipContext]:
    if not ranked_files:
        return {}

    files = [item.file for item in ranked_files]
    graph = build_import_graph(files)
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
) -> CompressionResult:
    """Compress ranked files under a token budget."""

    cfg = settings if settings is not None else CompressionSettings()
    keywords = task_keywords(task)
    compressed_files: list[CompressedFile] = []
    files_included: list[str] = []
    files_skipped: list[str] = []
    seen_imports: set[str] = set()

    total_raw = 0
    total_compressed = 0
    duplicate_reads_prevented = 0
    seen_hashes: set[str] = set()
    slice_relationships = _build_slice_relationship_contexts(ranked_files)
    summarizer = SummarizationService(
        backend=(summarization_settings.backend if summarization_settings is not None else "deterministic"),
        adapter_name=(summarization_settings.adapter if summarization_settings is not None else ""),
    )

    for ranked in ranked_files:
        file_record = ranked.file
        relevance_score = ranked.heuristic_score if ranked.heuristic_score > 0 else ranked.score
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
        cache_key = f"{file_record.path}:{file_record.size_bytes}:{file_record.content_hash}"
        line_count = len(full_text.splitlines())
        relationship_context = slice_relationships.get(file_record.path, SliceRelationshipContext())
        symbols: list[dict[str, int | str | bool]] = []
        symbol_failure_reason = ""

        # Scale extraction line budget and symbol count by relevance:
        # high-scoring files get more lines and more symbols extracted.
        if cfg.adaptive_line_budget and cfg.snippet_score_threshold > 0:
            _score_ratio = relevance_score / cfg.snippet_score_threshold
            _factor = min(cfg.adaptive_line_budget_max_factor, max(0.5, _score_ratio))
            effective_line_budget = max(1, int(cfg.snippet_total_line_limit * _factor))
            effective_max_symbols = max(2, min(8, round(4 * _factor)))
        else:
            effective_line_budget = cfg.snippet_total_line_limit
            effective_max_symbols = 4

        symbol_selection = None
        if cfg.symbol_extraction_enabled:
            try:
                symbol_selection = select_symbol_aware_chunks(
                    file_path=file_record.path,
                    text=full_text,
                    keywords=keywords,
                    line_budget=effective_line_budget,
                    max_symbols=effective_max_symbols,
                )
            except Exception as exc:  # pragma: no cover - exercised via monkeypatch tests
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
        slice_text = f"# {file_record.path}\n{slice_selection.text}" if slice_selection is not None else ""
        slice_tokens = token_estimator(slice_text) if slice_text else 0
        slice_line_count = _selected_line_count(slice_selection.selected_ranges) if slice_selection is not None else 0
        slice_supported = slice_selection is not None and slice_tokens > 0 and slice_line_count > 0
        slice_is_reduction = bool(
            slice_supported
            and slice_line_count < line_count
            and slice_tokens < raw_tokens
        )
        relationship_driven = bool(
            relationship_context.outgoing_related_paths
            or relationship_context.incoming_related_paths
            or relationship_context.incoming_entrypoint_paths
        )
        slice_requested = bool(
            slice_supported
            and (
                relevance_score >= cfg.snippet_score_threshold
                or relationship_driven
            )
        )

        is_test = _is_test_file(file_record.path)
        is_utility = _is_utility_file(file_record.path)
        # Test and utility files always get symbol/slice extraction regardless of
        # size - fixtures and boilerplate bodies waste tokens when sent verbatim.
        force_compress = (is_test or is_utility) and symbol_selection is not None

        if raw_tokens <= cfg.full_file_threshold_tokens and not force_compress:
            strategy = "full"
            compressed = f"# Full: {file_record.path}\n{full_text}"
            chunk_strategy = "full-file"
            chunk_reason = "file fits within full-file threshold"
            selected_ranges = (
                [
                    {
                        "start_line": 1,
                        "end_line": line_count,
                        "kind": "full",
                        "reason": chunk_reason,
                    }
                ]
                if line_count > 0
                else []
            )
        elif symbol_selection is not None and (relevance_score >= cfg.snippet_score_threshold or force_compress):
            strategy = "symbol"
            compressed = f"# {file_record.path}\n{symbol_selection.text}"
            chunk_strategy = symbol_selection.chunk_strategy
            chunk_reason = symbol_selection.chunk_reason
            selected_ranges = _selected_ranges_with_reason(symbol_selection.selected_ranges, chunk_reason)
            symbols = symbol_selection.symbols
        elif slice_supported and (slice_is_reduction or slice_requested):
            strategy = "snippet" if symbol_failure_reason else "slice"
            compressed = slice_text
            chunk_strategy = slice_selection.chunk_strategy
            chunk_reason = slice_selection.chunk_reason
            if symbol_failure_reason:
                chunk_reason = f"{symbol_failure_reason}; {chunk_reason}"
            selected_ranges = slice_selection.selected_ranges
        elif relevance_score >= cfg.snippet_score_threshold:
            strategy = "snippet"
            snippet = _snippet_from_text(file_record.path, full_text, keywords, cfg)
            compressed = snippet.text
            chunk_strategy = "keyword-window"
            chunk_reason = "fallback keyword-window snippet selection"
            if symbol_failure_reason:
                chunk_reason = f"{symbol_failure_reason}; {chunk_reason}"
            selected_ranges = _selected_ranges_with_reason(snippet.selected_ranges, chunk_reason)
        else:
            strategy = "summary"
            summary = summarizer.summarize(
                cache=cache,
                cache_key_prefix=cache_key,
                request=SummaryRequest(
                    task=task,
                    path=file_record.path,
                    text=full_text,
                    line_limit=cfg.summary_preview_lines,
                    score=ranked.score,
                    keywords=keywords,
                ),
            )
            compressed = summary.text
            chunk_reason = summary.chunk_reason
            chunk_strategy = summary.chunk_strategy
            if chunk_strategy == "summary-external":
                selected_ranges = (
                    [
                        {
                            "start_line": 1,
                            "end_line": line_count,
                            "kind": "summary",
                            "reason": chunk_reason,
                        }
                    ]
                    if line_count > 0
                    else []
                )
            else:
                preview_end = min(cfg.summary_preview_lines, line_count)
                selected_ranges = (
                    [
                        {
                            "start_line": 1,
                            "end_line": preview_end,
                            "kind": "summary",
                            "reason": chunk_reason,
                        }
                    ]
                    if preview_end > 0
                    else []
                )

        # Post-compression cleanup: strip docstrings and collapse blank lines.
        # Applied to all non-full strategies so slice/snippet output gets the
        # same cleanup that symbol extraction already applies internally.
        is_py_file = file_record.extension == ".py"
        if strategy != "full":
            if is_py_file:
                compressed = _strip_docstrings_in_text(compressed)
            compressed = _strip_decorative_dividers(compressed)
            compressed = _collapse_blank_lines(compressed)
            compressed = _dedup_imports(compressed, seen_imports)
        else:
            compressed = _collapse_blank_lines(compressed)

        fragment_cache_key = _fragment_cache_key(file_record.path, selected_ranges, compressed)
        fragment_reference = _fragment_reference_id(fragment_cache_key)
        cached_reference = cache.get_fragment(fragment_cache_key)
        cache_status = ""
        effective_chunk_reason = chunk_reason
        candidate_text = compressed
        source_tokens = token_estimator(compressed)
        compressed_tokens = source_tokens
        if cached_reference is not None:
            cache_status = "reused"
            effective_chunk_reason = f"reused cached summary reference; {chunk_reason}"
            candidate_text = _fragment_reference_text(cached_reference)
            compressed_tokens = token_estimator(candidate_text)
        if total_compressed + compressed_tokens > max_tokens:
            files_skipped.append(file_record.path)
            continue

        total_compressed += compressed_tokens
        if cached_reference is not None:
            cache.record_tokens_saved(max(0, source_tokens - compressed_tokens))
        else:
            if cache.put_fragment(fragment_cache_key, fragment_reference):
                cache_status = "stored"
        compressed_files.append(
            CompressedFile(
                path=file_record.path,
                strategy=strategy,
                original_tokens=raw_tokens,
                compressed_tokens=compressed_tokens,
                text=candidate_text,
                chunk_strategy=chunk_strategy,
                chunk_reason=effective_chunk_reason,
                selected_ranges=selected_ranges,
                symbols=symbols,
                cache_reference=(cached_reference or fragment_reference) if cache_status else "",
                cache_status=cache_status,
                relative_path=file_record.relative_path,
                repo_label=file_record.repo_label,
            )
        )
        files_included.append(file_record.path)

    saved = max(0, total_raw - total_compressed)
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
        risk = "low"
    elif risk_score < 0.5:
        risk = "medium"
    else:
        risk = "high"

    cache_snapshot = cache.snapshot()
    return CompressionResult(
        compressed_files=compressed_files,
        files_included=files_included,
        files_skipped=files_skipped,
        estimated_input_tokens=total_compressed,
        estimated_saved_tokens=saved,
        duplicate_reads_prevented=duplicate_reads_prevented,
        cache=cache_snapshot,
        cache_hits=cache_snapshot.hits,
        quality_risk_estimate=risk,
        summarizer=summarizer.snapshot(),
    )
