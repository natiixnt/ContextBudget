"""
ls / tree / find compressors.

All three produce path lists that share the same Listing canonical type.
Each parser turns its tool-specific format into Listing entries; a single
formatter renders the result at the chosen compression level.

The COMPACT and ULTRA levels emit an extension histogram, which is what
agents typically need from `ls -R` ('what kinds of files live here?')
without walking 5 000 file paths.
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
    Listing,
    ListingResult,
)
from redcon.core.tokens import estimate_tokens

_LS_LONG = re.compile(
    r"^(?P<perms>[\-dlcbps][\-rwxstST]{9,11})\s+\S+\s+\S+\s+\S+\s+(?P<size>\d+)"
    r"\s+\S+\s+\S+\s+\S+\s+(?P<name>.+)$"
)
_LS_DIR_HEADER = re.compile(r"^(?P<dir>[^:]+):$")
_TREE_LINE = re.compile(r"^(?P<prefix>[\s\-_/│├─└]*)(?P<name>\S.*)$")
_TREE_DIR = re.compile(r"/$")


# --- ls ---


class LsCompressor:
    schema = "ls"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        return bool(argv) and argv[0] == "ls"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        result = parse_ls(text)
        return _finalise(text, result, ctx)


def parse_ls(text: str) -> ListingResult:
    """Parse ls output: short form, -l form, and -R recursive form."""
    entries: list[Listing] = []
    current_dir = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            current_dir = ""
            continue
        header = _LS_DIR_HEADER.match(line)
        if header and not _LS_LONG.match(line):
            current_dir = header.group("dir").rstrip("/")
            continue
        if line.startswith("total "):
            continue
        long_match = _LS_LONG.match(line)
        if long_match:
            name = long_match.group("name")
            kind = _ls_kind_from_perms(long_match.group("perms"))
            try:
                size: int | None = int(long_match.group("size"))
            except ValueError:
                size = None
            path = _join(current_dir, name)
            entries.append(
                Listing(
                    path=path,
                    kind=kind,
                    size=size,
                    depth=path.count("/"),
                )
            )
            continue
        # Short form: just names. Treat trailing slash as dir indicator.
        for name in line.split():
            kind = "dir" if name.endswith("/") else "file"
            clean = name.rstrip("/")
            path = _join(current_dir, clean)
            entries.append(
                Listing(path=path, kind=kind, size=None, depth=path.count("/"))
            )
    return ListingResult(source="ls", entries=tuple(entries), truncated=False)


def _ls_kind_from_perms(perms: str) -> str:
    if not perms:
        return "file"
    head = perms[0]
    return {"-": "file", "d": "dir", "l": "symlink"}.get(head, "other")


# --- tree ---


class TreeCompressor:
    schema = "tree"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        return bool(argv) and argv[0] == "tree"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        result = parse_tree(text)
        return _finalise(text, result, ctx)


def parse_tree(text: str) -> ListingResult:
    """Parse `tree` output. Depth derived from leading characters."""
    entries: list[Listing] = []
    stack: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("."):
            stack = ["."]
            continue
        if "directories" in line and "files" in line:
            break
        m = _TREE_LINE.match(line)
        if not m:
            continue
        prefix = m.group("prefix")
        name = m.group("name").rstrip("/")
        depth = max(0, len(prefix) // 4)
        kind = "dir" if _TREE_DIR.search(m.group("name")) else "file"
        # Maintain a directory stack to reconstruct full paths.
        while len(stack) > depth + 1:
            stack.pop()
        if len(stack) <= depth:
            stack.append(name)
        else:
            stack[depth] = name
        full = "/".join(part for part in stack if part and part != ".") or name
        entries.append(Listing(path=full, kind=kind, size=None, depth=depth))
    return ListingResult(source="tree", entries=tuple(entries), truncated=False)


# --- find ---


class FindCompressor:
    schema = "find"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        return bool(argv) and argv[0] == "find"

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        result = parse_find(text)
        return _finalise(text, result, ctx)


def parse_find(text: str) -> ListingResult:
    """Each line is a path. We don't know if it's a file or dir, default file."""
    entries: list[Listing] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        kind = "dir" if line.endswith("/") else "file"
        clean = line.rstrip("/")
        entries.append(
            Listing(path=clean, kind=kind, size=None, depth=clean.count("/"))
        )
    return ListingResult(source="find", entries=tuple(entries), truncated=False)


# --- shared formatting ---


def _finalise(
    raw_text: str, result: ListingResult, ctx: CompressorContext
) -> CompressedOutput:
    raw_tokens = estimate_tokens(raw_text)
    level = select_level(raw_tokens, ctx.hint)
    formatted = _format(result, level)
    compressed_tokens = estimate_tokens(formatted)
    # Top-level entries (depth 0..1) must survive; deeper entries are summarised.
    patterns = tuple(
        re.escape(e.path) for e in result.entries if e.depth <= 1
    )[:50]
    preserved = verify_must_preserve(formatted, patterns, raw_text)
    return CompressedOutput(
        text=formatted,
        level=level,
        schema=result.source,
        original_tokens=raw_tokens,
        compressed_tokens=compressed_tokens,
        must_preserve_ok=preserved,
        truncated=False,
        notes=ctx.notes,
    )


def _format(result: ListingResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: ListingResult) -> str:
    files = sum(1 for e in result.entries if e.kind == "file")
    dirs = sum(1 for e in result.entries if e.kind == "dir")
    histogram = _extension_histogram(result.entries)
    head = f"{result.source}: {files} files, {dirs} dirs"
    if histogram:
        top = ", ".join(f"{ext}:{count}" for ext, count in histogram[:5])
        head += f" [{top}]"
    return head


def _format_compact(result: ListingResult) -> str:
    by_dir = _group_by_dir(result.entries)
    files = sum(1 for e in result.entries if e.kind == "file")
    dirs = sum(1 for e in result.entries if e.kind == "dir")
    histogram = _extension_histogram(result.entries)

    lines = [f"{result.source}: {files} files, {dirs} dirs across {len(by_dir)} dirs"]
    if histogram:
        lines.append(
            "extensions: "
            + ", ".join(f"{ext}={count}" for ext, count in histogram[:8])
        )
    for directory, items in list(by_dir.items())[:30]:
        names = [e.path.rsplit("/", 1)[-1] for e in items[:8]]
        suffix = f", +{len(items) - 8} more" if len(items) > 8 else ""
        lines.append(f"{directory or '.'}/  ({len(items)}): {', '.join(names)}{suffix}")
    if len(by_dir) > 30:
        lines.append(f"... +{len(by_dir) - 30} more directories")
    return "\n".join(lines)


def _format_verbose(result: ListingResult) -> str:
    """
    Verbose for listings mirrors the raw form: dir header followed by basenames
    of entries in that directory. Avoids per-entry full-path repetition which
    would inflate the output beyond the raw `ls -R`.
    """
    by_dir = _group_by_dir(result.entries)
    files = sum(1 for e in result.entries if e.kind == "file")
    dirs = sum(1 for e in result.entries if e.kind == "dir")
    lines = [f"{result.source}: {files} files, {dirs} dirs"]
    for directory, items in by_dir.items():
        if directory:
            lines.append(f"{directory}/:")
        for entry in items:
            base = entry.path.rsplit("/", 1)[-1]
            mark = "/" if entry.kind == "dir" else ""
            if entry.size is not None:
                lines.append(f"{base}{mark} ({entry.size}B)")
            else:
                lines.append(f"{base}{mark}")
    return "\n".join(lines)


def _group_by_dir(entries) -> dict[str, list[Listing]]:
    groups: dict[str, list[Listing]] = {}
    for entry in entries:
        if "/" in entry.path:
            directory = entry.path.rsplit("/", 1)[0]
        else:
            directory = ""
        groups.setdefault(directory, []).append(entry)
    return groups


def _extension_histogram(entries) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for entry in entries:
        if entry.kind != "file":
            continue
        name = entry.path.rsplit("/", 1)[-1]
        if "." in name:
            ext = name.rsplit(".", 1)[1].lower()
        else:
            ext = ""
        counts[ext] = counts.get(ext, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _join(directory: str, name: str) -> str:
    if not directory:
        return name
    if directory.endswith("/"):
        return directory + name
    return f"{directory}/{name}"
