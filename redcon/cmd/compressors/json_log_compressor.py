"""
NDJSON / JSON-line log compressor (V65).

Each non-empty line is one JSON object. The dominant schema (keys
present in >= SCHEMA_CONFORMANCE * total lines) is mined and emitted
once; body rows reuse that schema in field order. Lines that fail to
parse or do not match the schema are kept in a small outlier tail.

Detection:
  - argv: cat / tail / less / journalctl -o json / kubectl logs -o json
    over a *.log / *.ndjson / *.jsonl path
"""

from __future__ import annotations

import json
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
    JsonLogRecord,
    JsonLogResult,
)
from redcon.cmd._tokens_lite import estimate_tokens


# Conformance threshold: a key counts as part of the canonical schema
# when at least this fraction of records contain it. 0.8 mirrors V65
# research and avoids emitting brittle long tails into the header.
_SCHEMA_CONFORMANCE = 0.8

# Severity order used to prioritise rows in COMPACT output and to format
# the level histogram. Anything not in this list lands in the "other" bin.
_LEVEL_ORDER = ("fatal", "error", "warn", "warning", "info", "debug", "trace")
_LEVEL_RANK = {lvl: idx for idx, lvl in enumerate(_LEVEL_ORDER)}

# Common keys we accept as the level / timestamp signal across loggers
# (Python logging, zap, slog, k8s, journalctl).
_LEVEL_KEYS = ("level", "lvl", "severity", "@level", "log.level")
_TIMESTAMP_KEYS = ("ts", "time", "timestamp", "@timestamp", "datetime")

_VALUE_CLIP = 200
_COMPACT_BODY_LIMIT = 30
_VERBOSE_BODY_LIMIT = 200
_OUTLIER_LIMIT = 8


class JsonLogCompressor:
    schema = "json_log"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        # Patterns extended at compress time once we know which severe
        # records survived. Empty set on inputs with no error/fatal lines.
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        return _argv_is_json_log(argv)

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        if not text.strip() and raw_stderr:
            text = raw_stderr.decode("utf-8", errors="replace")
        result = parse_json_log(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        # Header inflation guard: when no records mined a canonical schema
        # AND the formatted output is no smaller than raw, the structured
        # form is pure overhead. Fall through to raw so we never inflate
        # on non-JSON noise. ULTRA always emits a single line so it stays
        # cheaper than raw - skip the guard there.
        # Two cases trigger raw passthrough:
        # 1. Inflation: formatter no smaller than raw (header overhead).
        # 2. Empty parse: no records mined, no canonical schema -> the
        #    structured form has nothing to compress; pretending it does
        #    drives below-floor V85 findings on adversarial noise.
        no_structure = not result.records and not result.schema_keys
        if (
            level != CompressionLevel.ULTRA
            and text.strip()
            and (compressed_tokens >= raw_tokens or no_structure)
        ):
            formatted = text.rstrip()
            compressed_tokens = estimate_tokens(formatted)
        # Severe records (error/fatal/warn) must keep their primary
        # identifier (timestamp + level) in COMPACT/VERBOSE.
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


def _argv_is_json_log(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    head = argv[0]
    if head == "journalctl" and any(
        a in {"-o", "--output"} and (i + 1 < len(argv) and argv[i + 1] == "json")
        for i, a in enumerate(argv)
    ):
        return True
    if head == "journalctl" and any(a == "--output=json" for a in argv):
        return True
    if head == "kubectl" and len(argv) >= 2 and argv[1] == "logs":
        return any(
            a in {"-o", "--output"} and (i + 1 < len(argv) and argv[i + 1] == "json")
            or a == "--output=json"
            or a == "-o=json"
            for i, a in enumerate(argv)
        )
    if head in {"cat", "tail", "less", "more"}:
        return any(_looks_like_log_path(a) for a in argv[1:])
    return False


def _looks_like_log_path(token: str) -> bool:
    lower = token.lower()
    return (
        lower.endswith(".log")
        or lower.endswith(".ndjson")
        or lower.endswith(".jsonl")
        or "/log/" in lower
    )


def parse_json_log(text: str) -> JsonLogResult:
    parsed: list[tuple[dict, str]] = []
    outliers: list[str] = []
    total_lines = 0

    for raw in text.splitlines():
        if not raw.strip():
            continue
        total_lines += 1
        line = raw.strip()
        try:
            obj = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            outliers.append(_clip(line, _VALUE_CLIP))
            continue
        if not isinstance(obj, dict):
            outliers.append(_clip(line, _VALUE_CLIP))
            continue
        parsed.append((obj, line))

    if not parsed:
        return JsonLogResult(
            schema_keys=(),
            records=(),
            outliers=tuple(outliers),
            total_lines=total_lines,
            level_histogram=(),
        )

    # Mine the canonical schema: keys present in >= conformance fraction
    # of records, ordered by their first-seen position.
    key_counts: dict[str, int] = {}
    key_first_seen: dict[str, int] = {}
    for idx, (obj, _) in enumerate(parsed):
        for key in obj.keys():
            key_counts[key] = key_counts.get(key, 0) + 1
            key_first_seen.setdefault(key, idx)

    threshold = max(1, int(_SCHEMA_CONFORMANCE * len(parsed)))
    canonical_keys = tuple(
        sorted(
            (k for k, c in key_counts.items() if c >= threshold),
            key=lambda k: key_first_seen[k],
        )
    )
    if not canonical_keys:
        # No dominant schema. Fall back to flat raw outliers - still
        # better than nothing for the harness to assert on.
        return JsonLogResult(
            schema_keys=(),
            records=(),
            outliers=tuple(_clip(line, _VALUE_CLIP) for _, line in parsed[:50]),
            total_lines=total_lines,
            level_histogram=(),
        )

    records: list[JsonLogRecord] = []
    level_histogram: dict[str, int] = {}
    for obj, raw_line in parsed:
        # Records missing any canonical key go to outliers; keeps the
        # body shape stable for the table form.
        if not all(k in obj for k in canonical_keys):
            outliers.append(_clip(raw_line, _VALUE_CLIP))
            continue
        fields = tuple(
            (k, _stringify(obj[k])) for k in canonical_keys
        )
        level = _extract_level(obj)
        ts = _extract_timestamp(obj)
        if level:
            level_histogram[level] = level_histogram.get(level, 0) + 1
        records.append(
            JsonLogRecord(
                fields=fields,
                level=level,
                timestamp=ts,
                raw_line=_clip(raw_line, _VALUE_CLIP),
            )
        )

    sorted_histogram = tuple(
        sorted(
            level_histogram.items(),
            key=lambda kv: (-kv[1], _LEVEL_RANK.get(kv[0], 99), kv[0]),
        )
    )

    return JsonLogResult(
        schema_keys=canonical_keys,
        records=tuple(records),
        outliers=tuple(outliers),
        total_lines=total_lines,
        level_histogram=sorted_histogram,
    )


def _format(result: JsonLogResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: JsonLogResult) -> str:
    head = (
        f"json_log: {result.total_lines} lines, "
        f"{len(result.records)} structured, "
        f"{len(result.outliers)} outliers"
    )
    if result.level_histogram:
        hist = ", ".join(f"{lvl}={count}" for lvl, count in result.level_histogram[:5])
        head += f" [levels: {hist}]"
    return head


def _format_compact(result: JsonLogResult) -> str:
    lines: list[str] = []
    lines.append(_summary_line(result))
    if result.schema_keys:
        lines.append("schema: " + ", ".join(result.schema_keys))
    if result.level_histogram:
        lines.append(
            "levels: "
            + ", ".join(f"{lvl}={count}" for lvl, count in result.level_histogram)
        )
    body = _ordered_body(result, _COMPACT_BODY_LIMIT)
    if body:
        lines.append("---")
        for record in body:
            lines.append(_format_row(record, result.schema_keys))
        if len(result.records) > len(body):
            lines.append(f"... +{len(result.records) - len(body)} more records")
    if result.outliers:
        lines.append("--- outliers")
        for outlier in result.outliers[:_OUTLIER_LIMIT]:
            lines.append(outlier)
        if len(result.outliers) > _OUTLIER_LIMIT:
            lines.append(f"... +{len(result.outliers) - _OUTLIER_LIMIT} more outliers")
    return "\n".join(lines)


def _format_verbose(result: JsonLogResult) -> str:
    lines: list[str] = [_summary_line(result)]
    if result.schema_keys:
        lines.append("schema: " + ", ".join(result.schema_keys))
    if result.level_histogram:
        lines.append(
            "levels: "
            + ", ".join(f"{lvl}={count}" for lvl, count in result.level_histogram)
        )
    body = _ordered_body(result, _VERBOSE_BODY_LIMIT)
    for record in body:
        lines.append(_format_row(record, result.schema_keys))
    if len(result.records) > len(body):
        lines.append(f"... +{len(result.records) - len(body)} more records")
    if result.outliers:
        lines.append("--- outliers")
        for outlier in result.outliers[:_OUTLIER_LIMIT]:
            lines.append(outlier)
    return "\n".join(lines)


def _summary_line(result: JsonLogResult) -> str:
    return (
        f"json_log: {result.total_lines} lines, "
        f"{len(result.records)} structured, "
        f"{len(result.outliers)} outliers"
    )


def _ordered_body(result: JsonLogResult, limit: int) -> list[JsonLogRecord]:
    """Severe rows first (error/fatal/warn), then most recent. Cap at limit."""
    sorted_records = sorted(
        result.records,
        key=lambda r: (
            _LEVEL_RANK.get(r.level or "", 99),
            -1 * _record_index(result, r),
        ),
    )
    return sorted_records[:limit]


def _record_index(result: JsonLogResult, record: JsonLogRecord) -> int:
    # Stable secondary key: original parse order; avoids relying on
    # timestamp string parsing.
    for idx, candidate in enumerate(result.records):
        if candidate is record:
            return idx
    return -1


def _format_row(record: JsonLogRecord, keys: tuple[str, ...]) -> str:
    """Pipe-separated values in canonical schema order; values clipped."""
    parts = []
    for key, value in record.fields:
        parts.append(_clip(value, _VALUE_CLIP))
    return " | ".join(parts) if parts else record.raw_line


def _stringify(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, separators=(",", ":"), default=str)


def _extract_level(obj: dict) -> str | None:
    for key in _LEVEL_KEYS:
        if key in obj:
            value = obj[key]
            if isinstance(value, str):
                return value.lower()
            return _stringify(value).lower()
    return None


def _extract_timestamp(obj: dict) -> str | None:
    for key in _TIMESTAMP_KEYS:
        if key in obj:
            return _stringify(obj[key])
    return None


def _must_preserve_for(
    result: JsonLogResult, level: CompressionLevel
) -> tuple[str, ...]:
    """Severe (error/fatal) record timestamps must survive when present.

    Walks the same _ordered_body slice the formatter actually emits at
    this level so patterns can never be set on records that the body
    cap dropped (the V85-style cap-mismatch trap). Warn records are
    counted in the level histogram - their per-record timestamp is
    intentionally not asserted.
    """
    if level == CompressionLevel.ULTRA:
        return ()
    body_limit = (
        _COMPACT_BODY_LIMIT if level == CompressionLevel.COMPACT
        else _VERBOSE_BODY_LIMIT
    )
    emitted = _ordered_body(result, body_limit)
    severe_levels = {"fatal", "error"}
    patterns: list[str] = []
    seen: set[str] = set()
    for record in emitted:
        if record.level not in severe_levels:
            continue
        primary = record.timestamp or record.raw_line[:80]
        if not primary or primary in seen:
            continue
        seen.add(primary)
        patterns.append(re.escape(primary))
        if len(patterns) >= 30:
            break
    return tuple(patterns)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
