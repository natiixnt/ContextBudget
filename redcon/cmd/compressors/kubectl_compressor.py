"""
kubectl get output compressor.

Handles ``kubectl get pods/services/deployments/...`` table output. The
columns vary per resource kind, but the common pattern is a header line
of column names followed by space-separated data rows. AGE / RESTARTS /
TIMESTAMPS dominate the verbose form; we drop them at compact level and
keep status / type / counts.
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    KubeEventGroup,
    KubeEventsResult,
    KubeListResult,
    KubeResource,
)
from redcon.cmd._tokens_lite import estimate_tokens


_HEADER_RE = re.compile(r"^[A-Z][A-Z0-9 \-/()]+$")
_NOISY_COLUMNS = frozenset({"AGE", "RESTARTS", "RESOURCE-VERSION", "TIMESTAMP"})
_EVENT_HEADER_HINTS = frozenset({"REASON", "OBJECT", "MESSAGE"})

# Mask volatile substrings in event messages so semantically-equal
# events collapse into one group: container ids, pod hashes, ip addrs,
# durations, byte counts.
_MSG_MASK_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-f]{12,64}\b"), "<id>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b\d+m?s\b"), "<dur>"),
    (re.compile(r"\b\d+\.\d+(?:KB|MB|GB|MiB|KiB|GiB)\b"), "<size>"),
    (re.compile(r"\b\d{4,}\b"), "<n>"),
)


class KubectlGetCompressor:
    schema = "kubectl_get"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if len(argv) < 2:
            return False
        return argv[0] == "kubectl" and argv[1] == "get"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        if _is_events_argv(ctx.argv) or _looks_like_events_header(text):
            return _compress_events(text, ctx)
        kind = _kind_from_argv(ctx.argv)
        result = parse_kubectl_get(text, kind=kind)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed = estimate_tokens(formatted)
        names = tuple(re.escape(r.name) for r in result.resources[:30])
        preserved = verify_must_preserve(formatted, names, text)
        return CompressedOutput(
            text=formatted,
            level=level,
            schema=self.schema,
            original_tokens=raw_tokens,
            compressed_tokens=compressed,
            must_preserve_ok=preserved,
            truncated=False,
            notes=ctx.notes,
        )


def _kind_from_argv(argv: tuple[str, ...]) -> str:
    if len(argv) >= 3:
        return argv[2]
    return "resource"


def parse_kubectl_get(text: str, *, kind: str = "resource") -> KubeListResult:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return KubeListResult(resources=(), kind=kind)

    header_line = lines[0]
    if not _HEADER_RE.match(header_line):
        return KubeListResult(resources=(), kind=kind)
    columns = _column_names(header_line)
    offsets = _column_offsets(header_line)

    resources: list[KubeResource] = []
    for raw in lines[1:]:
        cells = _split_by_offsets(raw, offsets)
        if len(cells) != len(columns) or not cells:
            continue
        record = dict(zip(columns, cells))
        name = record.get("NAME", "") or cells[0]
        status = (
            record.get("STATUS")
            or record.get("READY")
            or record.get("TYPE")
            or ""
        )
        namespace = record.get("NAMESPACE")
        age = record.get("AGE")
        extra = tuple(
            (k, v)
            for k, v in record.items()
            if k not in {"NAME", "NAMESPACE", "STATUS", "READY", "AGE"}
            and v
        )
        resources.append(
            KubeResource(
                kind=kind,
                name=name,
                namespace=namespace if namespace else None,
                status=status,
                age=age if age else None,
                extra=extra,
            )
        )
    return KubeListResult(resources=tuple(resources), kind=kind)


def _column_names(header: str) -> list[str]:
    """Header column names. Splits on 2+ spaces so multi-word headers stay together."""
    import re

    return [c for c in re.split(r"\s{2,}", header.strip()) if c]


def _column_offsets(header: str) -> list[int]:
    """Return the start offset of each column. Columns are separated by 2+ spaces."""
    offsets: list[int] = []
    i = 0
    n = len(header)
    while i < n:
        if header[i] != " ":
            offsets.append(i)
            while i < n:
                if header[i] == " " and i + 1 < n and header[i + 1] == " ":
                    break
                i += 1
            while i < n and header[i] == " ":
                i += 1
        else:
            i += 1
    return offsets


def _split_by_offsets(line: str, offsets: list[int]) -> list[str]:
    parts: list[str] = []
    for idx, start in enumerate(offsets):
        end = offsets[idx + 1] if idx + 1 < len(offsets) else len(line)
        parts.append(line[start:end].strip())
    return parts


def _format(result: KubeListResult, level: CompressionLevel) -> str:
    by_status = _count_by_status(result)
    head = (
        f"kubectl get {result.kind}: {len(result.resources)} items"
    )
    if by_status:
        head += " " + " ".join(f"{k}:{v}" for k, v in by_status)
    if level == CompressionLevel.ULTRA:
        return head
    lines = [head]
    limit = len(result.resources) if level == CompressionLevel.VERBOSE else 40
    for r in result.resources[:limit]:
        # Pull a couple of useful columns when present.
        readable_extra = " ".join(
            f"{k}={v}" for k, v in r.extra if k not in _NOISY_COLUMNS
        )
        ns_part = f"({r.namespace}) " if r.namespace else ""
        lines.append(
            f"{ns_part}{r.name} {r.status}"
            + (f" {readable_extra}" if readable_extra else "")
        )
    if len(result.resources) > limit:
        lines.append(f"+{len(result.resources) - limit} more")
    return "\n".join(lines)


def _count_by_status(result: KubeListResult) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for r in result.resources:
        key = r.status or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


# --- events branch ---


def _is_events_argv(argv: tuple[str, ...]) -> bool:
    return (
        len(argv) >= 3
        and argv[0] == "kubectl"
        and argv[1] in ("get", "describe")
        and argv[2] in ("events", "ev", "event")
    )


def _looks_like_events_header(text: str) -> bool:
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
    if not _HEADER_RE.match(first_line):
        return False
    cols = set(_column_names(first_line))
    return _EVENT_HEADER_HINTS.issubset(cols)


def _compress_events(text: str, ctx: CompressorContext) -> CompressedOutput:
    result = parse_kubectl_events(text)
    raw_tokens = estimate_tokens(text)
    level = select_level(raw_tokens, ctx.hint)
    formatted = _format_events(result, level)
    compressed = estimate_tokens(formatted)
    # Must-preserve: every Warning reason+object must survive in COMPACT/VERBOSE.
    warnings = tuple(
        re.escape(f"{g.reason} {g.object_kind}/{g.object_name}")
        for g in result.groups
        if g.event_type == "Warning"
    )
    preserved = verify_must_preserve(formatted, warnings, text)
    return CompressedOutput(
        text=formatted,
        level=level,
        schema="kubectl_events",
        original_tokens=raw_tokens,
        compressed_tokens=compressed,
        must_preserve_ok=preserved,
        truncated=False,
        notes=ctx.notes,
    )


def parse_kubectl_events(text: str) -> KubeEventsResult:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return KubeEventsResult(groups=(), total_events=0, warning_count=0)

    header_line = lines[0]
    if not _HEADER_RE.match(header_line):
        return KubeEventsResult(groups=(), total_events=0, warning_count=0)
    columns = _column_names(header_line)
    offsets = _column_offsets(header_line)

    bucket: dict[
        tuple[str, str, str, str, str | None],
        tuple[int, str, str | None],
    ] = {}
    total = 0
    warnings = 0

    for raw in lines[1:]:
        cells = _split_by_offsets(raw, offsets)
        if len(cells) != len(columns) or not cells:
            continue
        record = dict(zip(columns, cells))
        ev_type = record.get("TYPE", "") or "Normal"
        reason = record.get("REASON", "") or ""
        object_field = record.get("OBJECT", "") or ""
        message = record.get("MESSAGE", "") or ""
        namespace = record.get("NAMESPACE")
        last_seen = record.get("LAST SEEN") or record.get("LAST-SEEN") or record.get("AGE")

        object_kind, _, object_name = object_field.partition("/")
        if not object_name:
            object_name = object_kind
            object_kind = "object"
        try:
            count = int(record.get("COUNT", "1") or "1")
        except ValueError:
            count = 1
        total += count
        if ev_type == "Warning":
            warnings += count

        masked = _mask_message(message)
        key = (ev_type, reason, object_kind, object_name, namespace if namespace else None)
        existing = bucket.get(key)
        if existing is None:
            bucket[key] = (count, masked, last_seen if last_seen else None)
        else:
            prev_count, prev_msg, prev_last = existing
            new_last = last_seen if last_seen else prev_last
            bucket[key] = (prev_count + count, prev_msg, new_last)

    groups = tuple(
        KubeEventGroup(
            event_type=k[0],
            reason=k[1],
            object_kind=k[2],
            object_name=k[3],
            namespace=k[4],
            count=v[0],
            sample_message=v[1],
            last_seen=v[2],
        )
        for k, v in sorted(
            bucket.items(),
            key=lambda kv: (
                0 if kv[0][0] == "Warning" else 1,
                -kv[1][0],
                kv[0][1],
                kv[0][2],
                kv[0][3],
            ),
        )
    )
    return KubeEventsResult(
        groups=groups, total_events=total, warning_count=warnings
    )


def _mask_message(msg: str) -> str:
    out = msg.strip()
    for pattern, replacement in _MSG_MASK_RULES:
        out = pattern.sub(replacement, out)
    return out


def _format_events(result: KubeEventsResult, level: CompressionLevel) -> str:
    head = (
        f"kubectl events: {result.total_events} events, "
        f"{result.warning_count} warning, {len(result.groups)} groups"
    )
    if level == CompressionLevel.ULTRA:
        warn_groups = [g for g in result.groups if g.event_type == "Warning"]
        if not warn_groups:
            return head
        top = warn_groups[: min(5, len(warn_groups))]
        return head + "; " + "; ".join(
            f"{g.reason}@{g.object_kind}/{g.object_name}({g.count})" for g in top
        )

    lines = [head]
    limit = len(result.groups) if level == CompressionLevel.VERBOSE else 30
    for g in result.groups[:limit]:
        ns = f"{g.namespace}/" if g.namespace else ""
        last = f" last={g.last_seen}" if g.last_seen else ""
        lines.append(
            f"[{g.event_type[0]}] x{g.count} {g.reason} {ns}{g.object_kind}/{g.object_name}: {g.sample_message}{last}"
        )
    if len(result.groups) > limit:
        lines.append(f"+{len(result.groups) - limit} more groups")
    return "\n".join(lines)
