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
    KubeListResult,
    KubeResource,
)
from redcon.cmd._tokens_lite import estimate_tokens


_HEADER_RE = re.compile(r"^[A-Z][A-Z0-9 \-/()]+$")
_NOISY_COLUMNS = frozenset({"AGE", "RESTARTS", "RESOURCE-VERSION", "TIMESTAMP"})


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
