"""
Coverage report compressor (V69).

Parses the standard `coverage report` text grid (with or without
`--show-missing`) into a CoverageResult and emits a tier-appropriate
view. Today's COMPACT cuts the alphabetical 400-row dump to total +
lowest-coverage top-K; the V47 dispatcher can later register a
schema-aware renderer for the delta-vs-baseline form.

Detection: argv hits `coverage report` or `python -m coverage report`.
`pytest --cov` keeps using PytestCompressor (test results dominate the
output; the trailing coverage table is reachable via a dedicated invocation).
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import (
    Compressor,
    CompressorContext,
    verify_must_preserve,
)
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    CoverageResult,
    CoverageRow,
)
from redcon.cmd._tokens_lite import estimate_tokens

# `Name    Stmts   Miss  Cover[   Missing]`. Header style is fixed by
# coverage.py; use the column header to detect the missing column.
_HEADER_RE = re.compile(
    r"^Name\s+Stmts\s+Miss\s+Cover(?P<missing>\s+Missing)?\s*$"
)
_DASHES = re.compile(r"^-{3,}\s*$")
# Parse a body row. coverage.py right-aligns counts and percentage with
# variable column widths, so we anchor on whitespace-separated fields
# from the right and treat the leading run as the path (which may
# contain spaces in pathological cases - keep simple, split on
# 2+ spaces from the right).
_ROW_RE = re.compile(
    r"^(?P<path>\S(?:.*\S)?)\s{2,}(?P<stmts>\d+)\s+(?P<miss>\d+)\s+(?P<cover>\d+(?:\.\d+)?)%(?:\s+(?P<missing>\S.*))?\s*$"
)
_TOTAL_RE = re.compile(
    r"^TOTAL\s+(?P<stmts>\d+)\s+(?P<miss>\d+)\s+(?P<cover>\d+(?:\.\d+)?)%\s*$"
)
_COMPACT_TOP = 20
_VERBOSE_LIMIT = 100


class CoverageCompressor:
    schema = "coverage"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        # Patterns extended at compress time once the lowest-coverage
        # rows have been chosen so the contract reflects what the
        # formatter actually emits.
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        if argv[0] == "coverage" and len(argv) >= 2 and argv[1] == "report":
            return True
        if (
            argv[0] in {"python", "python3"}
            and "-m" in argv
            and "coverage" in argv
            and "report" in argv
        ):
            return True
        return False

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        result = parse_coverage(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        patterns = _must_preserve_for(result, level)
        preserved = verify_must_preserve(formatted, patterns, text)
        return CompressedOutput(
            text=formatted,
            level=level,
            schema=self.schema,
            original_tokens=raw_tokens,
            compressed_tokens=compressed_tokens,
            must_preserve_ok=preserved,
            truncated=False,
            notes=ctx.notes,
        )


def parse_coverage(text: str) -> CoverageResult:
    rows: list[CoverageRow] = []
    has_missing = False
    total_stmts = 0
    total_miss = 0
    total_cover = 0.0
    seen_header = False
    in_body = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if not seen_header:
            header = _HEADER_RE.match(line)
            if header:
                seen_header = True
                has_missing = header.group("missing") is not None
            continue
        if _DASHES.match(line):
            in_body = not in_body
            continue
        # TOTAL is emitted after the closing separator on coverage.py
        # output, so check it BEFORE the in-body gate.
        if line.startswith("TOTAL"):
            total_match = _TOTAL_RE.match(line)
            if total_match:
                try:
                    total_stmts = int(total_match.group("stmts"))
                    total_miss = int(total_match.group("miss"))
                    total_cover = float(total_match.group("cover"))
                except (TypeError, ValueError):
                    pass
            in_body = False
            continue
        if not in_body:
            # tail blocks (e.g. additional totals or warnings); ignore
            continue
        row = _ROW_RE.match(line)
        if not row:
            continue
        try:
            stmts = int(row.group("stmts"))
            miss = int(row.group("miss"))
            cover = float(row.group("cover"))
        except (TypeError, ValueError):
            continue
        rows.append(
            CoverageRow(
                path=row.group("path").strip(),
                stmts=stmts,
                miss=miss,
                cover_pct=cover,
                missing=row.group("missing") if has_missing else None,
            )
        )

    return CoverageResult(
        rows=tuple(rows),
        total_stmts=total_stmts,
        total_miss=total_miss,
        total_cover_pct=total_cover,
        has_missing_column=has_missing,
    )


def _format(result: CoverageResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: CoverageResult) -> str:
    if not result.rows:
        return f"coverage: {result.total_cover_pct:.1f}% (0 files)"
    lowest = sorted(result.rows, key=lambda r: r.cover_pct)[:3]
    head = (
        f"coverage: {result.total_cover_pct:.1f}% "
        f"({len(result.rows)} files, {result.total_miss} miss/{result.total_stmts})"
    )
    if lowest:
        bullets = "; ".join(
            f"{r.path}={r.cover_pct:.1f}%" for r in lowest
        )
        head += f"; lowest: {bullets}"
    return head


def _format_compact(result: CoverageResult) -> str:
    head = (
        f"coverage: {result.total_cover_pct:.1f}% "
        f"({len(result.rows)} files, {result.total_miss} miss/{result.total_stmts} stmts)"
    )
    lines = [head]
    if not result.rows:
        return head
    lowest = sorted(result.rows, key=lambda r: (r.cover_pct, r.path))[:_COMPACT_TOP]
    if lowest:
        lines.append("---")
        for row in lowest:
            extra = f" ({row.missing})" if row.missing else ""
            lines.append(
                f"{row.path}: {row.cover_pct:.1f}% "
                f"({row.miss}/{row.stmts}){extra}"
            )
        if len(result.rows) > len(lowest):
            lines.append(
                f"... +{len(result.rows) - len(lowest)} more files (higher coverage)"
            )
    return "\n".join(lines)


def _format_verbose(result: CoverageResult) -> str:
    head = (
        f"coverage: {result.total_cover_pct:.1f}% "
        f"({len(result.rows)} files, {result.total_miss} miss/{result.total_stmts} stmts)"
    )
    lines = [head]
    if not result.rows:
        return head
    sorted_rows = sorted(result.rows, key=lambda r: (r.cover_pct, r.path))
    body = sorted_rows[:_VERBOSE_LIMIT]
    lines.append("---")
    for row in body:
        extra = f" ({row.missing})" if row.missing else ""
        lines.append(
            f"{row.path}: {row.cover_pct:.1f}% ({row.miss}/{row.stmts}){extra}"
        )
    if len(sorted_rows) > len(body):
        lines.append(f"... +{len(sorted_rows) - len(body)} more files")
    return "\n".join(lines)


def _must_preserve_for(
    result: CoverageResult, level: CompressionLevel
) -> tuple[str, ...]:
    """Lowest-coverage rows the formatter actually emits must survive."""
    if level == CompressionLevel.ULTRA or not result.rows:
        return ()
    body_limit = (
        _COMPACT_TOP if level == CompressionLevel.COMPACT else _VERBOSE_LIMIT
    )
    sorted_rows = sorted(result.rows, key=lambda r: (r.cover_pct, r.path))
    patterns: list[str] = []
    seen: set[str] = set()
    for row in sorted_rows[:body_limit]:
        if row.path in seen:
            continue
        seen.add(row.path)
        patterns.append(re.escape(row.path))
        if len(patterns) >= 50:
            break
    return tuple(patterns)


# --- V47 schema-aware delta renderer ---


_DELTA_THRESHOLD_PP = 0.5
_DELTA_TOP_K = 12


def render_coverage_delta(baseline_raw: str, current_raw: str) -> str:
    """Per-file coverage move vs baseline (V47 dispatcher hook).

    Wins over generic line-delta because the alphabetical row order means
    a single regression ripples into a noisy line-diff; here we emit only
    files whose coverage moved by >= 0.5pp plus the aggregate move.
    Returns "" sentinel when neither side parses to a non-empty
    CoverageResult; dispatcher falls back to line-delta.
    """
    prior = parse_coverage(baseline_raw)
    curr = parse_coverage(current_raw)
    if not prior.rows and not curr.rows:
        return ""

    prior_map = {row.path: row for row in prior.rows}
    curr_map = {row.path: row for row in curr.rows}

    moved: list[tuple[float, str, float, float]] = []  # (delta, path, before, after)
    for path, c_row in curr_map.items():
        p_row = prior_map.get(path)
        if p_row is None:
            continue  # new file -> reported separately
        delta = c_row.cover_pct - p_row.cover_pct
        if abs(delta) >= _DELTA_THRESHOLD_PP:
            moved.append((delta, path, p_row.cover_pct, c_row.cover_pct))
    moved.sort(key=lambda t: (t[0], t[1]))  # most-dropped first, ties on path

    new_files = sorted(curr_map.keys() - prior_map.keys())
    gone_files = sorted(prior_map.keys() - curr_map.keys())

    aggregate_delta = curr.total_cover_pct - prior.total_cover_pct
    head = (
        f"coverage: {curr.total_cover_pct:.1f}% "
        f"(vs baseline {prior.total_cover_pct:.1f}%, "
        f"{aggregate_delta:+.1f}pp; "
        f"{len(curr.rows)} files, {len(moved)} moved)"
    )
    parts = [head]
    if moved:
        parts.append("---")
        for delta, path, before, after in moved[:_DELTA_TOP_K]:
            parts.append(
                f"{delta:+.1f}pp {path}: {before:.1f} -> {after:.1f}"
            )
        if len(moved) > _DELTA_TOP_K:
            parts.append(f"... +{len(moved) - _DELTA_TOP_K} more files moved")
    if new_files:
        parts.append(
            f"new: {len(new_files)} files ({', '.join(new_files[:5])}"
            + ("..." if len(new_files) > 5 else "")
            + ")"
        )
    if gone_files:
        parts.append(
            f"gone: {len(gone_files)} files ({', '.join(gone_files[:5])}"
            + ("..." if len(gone_files) > 5 else "")
            + ")"
        )
    return "\n".join(parts)


try:
    from redcon.cmd.delta import register_schema_renderer as _register

    _register("coverage", render_coverage_delta)
except ImportError:
    pass
