"""
Bundle stats compressor (V63).

Handles webpack `--json` output and esbuild metafiles. Both share the
same agent-relevant facts: per-entry total size, top-N largest modules
inside each entry, total module count, errors and warnings, build time.

The compressor parses JSON, picks the dialect by feature-sniffing, and
emits a tier-appropriate view. ULTRA collapses to a single summary
line; COMPACT lists the top entries with their top-10 modules; VERBOSE
emits every entry with module size context.

Detection: argv head webpack/esbuild/vite/rollup/parcel with a build
verb, OR `cat *stats*.json` / `cat *metafile*.json`.
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
    BundleEntry,
    BundleStatsResult,
    CompressedOutput,
    CompressionLevel,
)
from redcon.cmd._tokens_lite import estimate_tokens


_BUILD_VERBS = frozenset({"build", "compile", "bundle"})
_BUNDLE_TOOLS = frozenset(
    {"webpack", "esbuild", "vite", "rollup", "parcel"}
)
_LOG_PATH_HINTS = (".stats.json", ".metafile.json", "stats.json", "metafile.json")
_MODULE_TOP_K = 10
_ENTRY_TOP_K = 10
_MSG_CLIP = 200


class BundleStatsCompressor:
    schema = "bundle_stats"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        return _argv_is_bundle(argv)

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        if not text.strip() and raw_stderr:
            text = raw_stderr.decode("utf-8", errors="replace")
        result = parse_bundle_stats(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        # Inflation guard: when nothing parsed AND format would be no
        # smaller than raw, fall through. Keeps the contract non-
        # regressive on adversarial non-JSON inputs.
        if (
            level != CompressionLevel.ULTRA
            and not result.entries
            and compressed_tokens >= raw_tokens
            and text.strip()
        ):
            formatted = text.rstrip()
            compressed_tokens = estimate_tokens(formatted)
        # Patterns: top-K entry names + first error/warning, all of which
        # the formatter actually emits at COMPACT/VERBOSE.
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


def _argv_is_bundle(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    head = argv[0].rsplit("/", 1)[-1]
    if head in _BUNDLE_TOOLS:
        # accept tool + (build verb OR --json/--analyze flag)
        if len(argv) >= 2 and argv[1] in _BUILD_VERBS:
            return True
        return any(
            a in {"--json", "--analyze", "--stats"} or a == "--metafile"
            for a in argv[1:]
        )
    if head == "npx" and len(argv) >= 2 and argv[1] in _BUNDLE_TOOLS:
        return True
    if head in {"cat", "tail", "less", "more"}:
        for token in argv[1:]:
            t = token.lower()
            if any(t.endswith(hint) for hint in _LOG_PATH_HINTS):
                return True
    return False


def parse_bundle_stats(text: str) -> BundleStatsResult:
    text = text.strip()
    if not text:
        return BundleStatsResult(
            tool="unknown",
            entries=(),
            total_modules=0,
            duplicate_count=0,
            errors=(),
            warnings=(),
            build_time_ms=None,
        )
    try:
        obj = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return BundleStatsResult(
            tool="unknown",
            entries=(),
            total_modules=0,
            duplicate_count=0,
            errors=(),
            warnings=(),
            build_time_ms=None,
        )
    if not isinstance(obj, dict):
        return BundleStatsResult(
            tool="unknown",
            entries=(),
            total_modules=0,
            duplicate_count=0,
            errors=(),
            warnings=(),
            build_time_ms=None,
        )

    if "outputs" in obj and "inputs" in obj:
        return _parse_esbuild(obj)
    if "assets" in obj or "modules" in obj or "chunks" in obj:
        return _parse_webpack(obj)
    return BundleStatsResult(
        tool="unknown",
        entries=(),
        total_modules=0,
        duplicate_count=0,
        errors=(),
        warnings=(),
        build_time_ms=None,
    )


def _parse_webpack(obj: dict) -> BundleStatsResult:
    entries: list[BundleEntry] = []
    modules_by_name: dict[str, int] = {}

    for module in obj.get("modules") or []:
        if not isinstance(module, dict):
            continue
        name = str(module.get("name") or module.get("identifier") or "")
        size = int(module.get("size") or 0)
        if name:
            modules_by_name[name] = max(modules_by_name.get(name, 0), size)

    asset_modules: dict[str, list[tuple[str, int]]] = {}
    for asset in obj.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        size = int(asset.get("size") or 0)
        if not name:
            continue
        asset_modules.setdefault(name, []).append((name, size))

    if not asset_modules and modules_by_name:
        asset_modules.setdefault("(modules)", list(modules_by_name.items()))

    for asset_name, _ in asset_modules.items():
        # Approximate modules-per-asset from the global module list. Real
        # webpack stats nests modules under chunks; for the cmd path we
        # treat the global module list as the fanout.
        modules_sorted = sorted(
            modules_by_name.items(), key=lambda kv: (-kv[1], kv[0])
        )
        total_bytes = sum(m.get("size", 0) for m in obj.get("assets") or []
                          if isinstance(m, dict) and m.get("name") == asset_name)
        entries.append(
            BundleEntry(
                name=asset_name,
                total_bytes=total_bytes if total_bytes else sum(s for _, s in modules_sorted),
                module_count=len(modules_sorted),
                top_modules=tuple(modules_sorted[:_MODULE_TOP_K]),
            )
        )

    entries.sort(key=lambda e: (-e.total_bytes, e.name))

    errors = tuple(
        _to_message(item) for item in (obj.get("errors") or [])
    )
    warnings = tuple(
        _to_message(item) for item in (obj.get("warnings") or [])
    )
    build_time_ms = obj.get("time") if isinstance(obj.get("time"), (int, float)) else None
    duplicate_count = 0
    return BundleStatsResult(
        tool="webpack",
        entries=tuple(entries),
        total_modules=len(modules_by_name),
        duplicate_count=duplicate_count,
        errors=errors,
        warnings=warnings,
        build_time_ms=float(build_time_ms) if build_time_ms is not None else None,
    )


def _parse_esbuild(obj: dict) -> BundleStatsResult:
    inputs = obj.get("inputs") or {}
    outputs = obj.get("outputs") or {}
    entries: list[BundleEntry] = []
    for out_name, out_meta in outputs.items():
        if not isinstance(out_meta, dict):
            continue
        total = int(out_meta.get("bytes") or 0)
        out_inputs = out_meta.get("inputs") or {}
        modules: list[tuple[str, int]] = []
        if isinstance(out_inputs, dict):
            for in_path, in_meta in out_inputs.items():
                if isinstance(in_meta, dict):
                    sz = int(in_meta.get("bytesInOutput") or 0)
                    modules.append((str(in_path), sz))
        modules.sort(key=lambda kv: (-kv[1], kv[0]))
        entries.append(
            BundleEntry(
                name=str(out_name),
                total_bytes=total,
                module_count=len(modules),
                top_modules=tuple(modules[:_MODULE_TOP_K]),
            )
        )
    entries.sort(key=lambda e: (-e.total_bytes, e.name))
    return BundleStatsResult(
        tool="esbuild",
        entries=tuple(entries),
        total_modules=len(inputs) if isinstance(inputs, dict) else 0,
        duplicate_count=0,
        errors=(),
        warnings=(),
        build_time_ms=None,
    )


def _to_message(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(
            item.get("message")
            or item.get("text")
            or json.dumps(item, default=str)[:_MSG_CLIP]
        )
    return str(item)[:_MSG_CLIP]


def _format(result: BundleStatsResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: BundleStatsResult) -> str:
    parts = [
        f"bundle({result.tool}): "
        f"{len(result.entries)} entries, "
        f"{result.total_modules} modules"
    ]
    if result.errors:
        parts.append(f"{len(result.errors)} errors")
    if result.warnings:
        parts.append(f"{len(result.warnings)} warns")
    if result.entries:
        biggest = result.entries[0]
        parts.append(
            f"biggest={biggest.name}({_humanize_bytes(biggest.total_bytes)})"
        )
    return ", ".join(parts)


def _format_compact(result: BundleStatsResult) -> str:
    head = (
        f"bundle({result.tool}): "
        f"{len(result.entries)} entries, "
        f"{result.total_modules} modules"
    )
    if result.build_time_ms is not None:
        head += f", build_time={result.build_time_ms:.0f}ms"
    if result.errors:
        head += f", {len(result.errors)} errors"
    if result.warnings:
        head += f", {len(result.warnings)} warns"
    lines = [head]
    if not result.entries:
        return head
    for entry in result.entries[:_ENTRY_TOP_K]:
        lines.append(
            f"{entry.name} {_humanize_bytes(entry.total_bytes)} "
            f"({entry.module_count} modules)"
        )
        for path, size in entry.top_modules[:5]:
            lines.append(f"  {path} {_humanize_bytes(size)}")
    if len(result.entries) > _ENTRY_TOP_K:
        lines.append(f"+{len(result.entries) - _ENTRY_TOP_K} more entries")
    if result.errors:
        lines.append("--- errors")
        for err in result.errors[:5]:
            lines.append(_clip(err, _MSG_CLIP))
    return "\n".join(lines)


def _format_verbose(result: BundleStatsResult) -> str:
    lines = [_format_compact(result), "---"]
    for entry in result.entries:
        lines.append(
            f"{entry.name} {_humanize_bytes(entry.total_bytes)} "
            f"({entry.module_count} modules)"
        )
        for path, size in entry.top_modules:
            lines.append(f"  {path} {_humanize_bytes(size)}")
    return "\n".join(lines)


def _must_preserve_for(
    result: BundleStatsResult, level: CompressionLevel
) -> tuple[str, ...]:
    if level == CompressionLevel.ULTRA or not result.entries:
        return ()
    patterns: list[str] = []
    for entry in result.entries[:_ENTRY_TOP_K]:
        patterns.append(re.escape(entry.name))
        if len(patterns) >= 30:
            break
    return tuple(patterns)


_BYTE_UNITS = (
    (1024 ** 4, "TB"),
    (1024 ** 3, "GB"),
    (1024 ** 2, "MB"),
    (1024, "KB"),
)


def _humanize_bytes(n: int) -> str:
    if n <= 0:
        return "0B"
    for divisor, unit in _BYTE_UNITS:
        if n >= divisor:
            value = n / divisor
            if value >= 100:
                return f"{value:.0f}{unit}"
            return f"{value:.1f}{unit}"
    return f"{n}B"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
