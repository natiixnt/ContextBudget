"""Lightweight import/dependency graph extraction for local repository files."""

from __future__ import annotations

import json
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from redcon.schemas.models import FileRecord

PY_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$")
PY_FROM_RE = re.compile(r"^\s*from\s+([\.\w]+)\s+import\s+(.+)$")

JS_IMPORT_FROM_RE = re.compile(
    r"(?:import|export)\s+(?:type\s+)?[^\n;]*?\sfrom\s+[\"']([^\"']+)[\"']"
)
JS_SIDE_EFFECT_IMPORT_RE = re.compile(r"\bimport\s+[\"']([^\"']+)[\"']")
JS_REQUIRE_RE = re.compile(r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)")

JS_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

GO_IMPORT_RE = re.compile(r'^\s*"([^"]+)"\s*$')
GO_IMPORT_BLOCK_START_RE = re.compile(r"^\s*import\s*\(\s*$")
GO_IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+"([^"]+)"\s*$')


@dataclass(slots=True)
class ImportGraph:
    """Directed import graph.

    `outgoing[a]` contains files imported by `a`.
    `incoming[b]` contains files that import `b`.
    """

    outgoing: dict[str, set[str]]
    incoming: dict[str, set[str]]
    entrypoints: set[str]


def _build_python_module_map(files: list[FileRecord]) -> dict[str, str]:
    module_map: dict[str, str] = {}
    basename_candidates: dict[str, set[str]] = defaultdict(set)

    for record in files:
        if record.extension != ".py":
            continue

        pure = PurePosixPath(record.relative_path or record.path)
        stem_parts = list(pure.with_suffix("").parts)
        if not stem_parts:
            continue

        if stem_parts[-1] == "__init__":
            package = ".".join(stem_parts[:-1])
            if package:
                module_map[package] = record.path
        else:
            module = ".".join(stem_parts)
            module_map[module] = record.path
            basename_candidates[stem_parts[-1]].add(record.path)

    for basename, paths in basename_candidates.items():
        if len(paths) == 1:
            module_map[basename] = next(iter(paths))

    return module_map


def _resolve_python_module_spec(
    spec: str, current_path: str, module_map: dict[str, str]
) -> str | None:
    if not spec:
        return None

    candidates: list[str] = []
    if spec.startswith("."):
        level = len(spec) - len(spec.lstrip("."))
        remainder = spec.lstrip(".")
        current = list(PurePosixPath(current_path).with_suffix("").parts)
        # Drop the module/__init__ name to get the containing package; both a
        # regular module and a package __init__ resolve relative imports
        # against their containing package.
        package_parts = current[:-1]

        keep_count = max(0, len(package_parts) - (level - 1))
        resolved_parts = package_parts[:keep_count]
        if remainder:
            resolved_parts.extend(remainder.split("."))
        if resolved_parts:
            candidates.append(".".join(resolved_parts))
    else:
        candidates.append(spec)

    expanded: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        expanded.append(candidate)
        segments = candidate.split(".")
        for idx in range(len(segments) - 1, 0, -1):
            expanded.append(".".join(segments[:idx]))

    for candidate in expanded:
        target = module_map.get(candidate)
        if target is not None:
            return target
    return None


def _python_specs(text: str) -> list[str]:
    """Raw import specs from Python source (module names + expanded from-imports).

    Extraction only - resolution to repo paths happens later against the module
    map. Depends solely on the file's content, so the result is cacheable by
    content hash.
    """
    specs: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        import_match = PY_IMPORT_RE.match(raw_line)
        if import_match:
            for token in import_match.group(1).split(","):
                module = token.strip().split(" as ")[0].strip()
                if module:
                    specs.append(module)
            continue

        from_match = PY_FROM_RE.match(raw_line)
        if not from_match:
            continue

        module_spec = from_match.group(1).strip()
        imported_items = [
            token.strip().split(" as ")[0].strip() for token in from_match.group(2).split(",")
        ]
        specs.append(module_spec)
        for imported in imported_items:
            if imported in {"", "*"}:
                continue
            if module_spec.startswith("."):
                specs.append(f"{module_spec}{imported}")
            else:
                specs.append(f"{module_spec}.{imported}")
    return specs


def _js_ts_specs(text: str) -> list[str]:
    """Raw module specifiers from JS/TS source (import-from, side-effect, require)."""
    specs: set[str] = set(JS_IMPORT_FROM_RE.findall(text))
    specs.update(JS_SIDE_EFFECT_IMPORT_RE.findall(text))
    specs.update(JS_REQUIRE_RE.findall(text))
    return sorted(specs)


def _go_specs(text: str) -> list[str]:
    """Raw import paths from Go source (single and block imports)."""
    specs: list[str] = []
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        single = GO_IMPORT_SINGLE_RE.match(line)
        if single:
            specs.append(single.group(1))
            continue
        if GO_IMPORT_BLOCK_START_RE.match(line):
            in_block = True
            continue
        if in_block:
            if line == ")":
                in_block = False
                continue
            block_match = GO_IMPORT_RE.match(line)
            if block_match:
                specs.append(block_match.group(1))
    return specs


def extract_import_specs(extension: str, text: str) -> list[str]:
    """Extract raw import specs for a file, dispatched by extension.

    Content-only: the scanner calls this while it already has the file text in
    memory, and the result is stored in the scan index so later import-graph
    builds reuse it instead of re-reading unchanged files.
    """
    ext = extension.lower()
    if ext == ".py":
        return _python_specs(text)
    if ext in JS_TS_EXTENSIONS:
        return _js_ts_specs(text)
    if ext == ".go":
        return _go_specs(text)
    return []


def _record_specs(record: FileRecord, extractor) -> list[str] | None:
    """Return a record's import specs, preferring the scan-index cache.

    ``record.import_specs`` is a JSON list populated by the scanner. When it is
    set (including an empty list for a file with no imports) it is used as-is,
    avoiding a disk read. An empty string means "not cached" - fall back to
    reading and extracting from disk so behaviour is never wrong, only slower.
    Returns ``None`` only when the file was meant to be read but could not be.
    """
    if record.import_specs:
        try:
            data = json.loads(record.import_specs)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, list):
            return [str(spec) for spec in data]
    try:
        text = Path(record.absolute_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    return extractor(text)


def _extract_python_import_edges(files: list[FileRecord]) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = defaultdict(set)
    repo_groups: dict[str, list[FileRecord]] = defaultdict(list)

    for record in files:
        repo_groups[record.repo_label].append(record)

    for repo_files in repo_groups.values():
        module_map = _build_python_module_map(repo_files)

        for record in repo_files:
            if record.extension != ".py":
                continue
            specs = _record_specs(record, _python_specs)
            if specs is None:
                continue

            current_path = record.relative_path or record.path
            for spec in specs:
                target = _resolve_python_module_spec(spec, current_path, module_map)
                if target and target != record.path:
                    edges[record.path].add(target)

    return edges


def _normalize_relative_import(current_path: str, spec: str) -> str | None:
    if spec.startswith("/"):
        normalized = posixpath.normpath(spec.lstrip("/"))
    elif spec.startswith("."):
        base_dir = posixpath.dirname(current_path)
        normalized = posixpath.normpath(posixpath.join(base_dir, spec))
    else:
        return None

    if normalized.startswith("../"):
        return None
    return normalized


def _resolve_js_ts_spec(current_path: str, spec: str, existing: set[str]) -> str | None:
    normalized = _normalize_relative_import(current_path, spec)
    if normalized is None:
        return None

    pure = PurePosixPath(normalized)
    suffix = pure.suffix.lower()
    candidates: list[str] = []

    if suffix in JS_TS_EXTENSIONS:
        candidates.append(normalized)
    else:
        for extension in sorted(JS_TS_EXTENSIONS):
            candidates.append(f"{normalized}{extension}")
        for extension in sorted(JS_TS_EXTENSIONS):
            candidates.append(posixpath.join(normalized, f"index{extension}"))

    for candidate in candidates:
        if candidate in existing:
            return candidate
    return None


def _extract_js_ts_import_edges(files: list[FileRecord]) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = defaultdict(set)
    repo_groups: dict[str, list[FileRecord]] = defaultdict(list)

    for record in files:
        repo_groups[record.repo_label].append(record)

    for repo_files in repo_groups.values():
        existing_paths = {
            record.relative_path or record.path: record.path
            for record in repo_files
            if record.extension in JS_TS_EXTENSIONS
        }
        # Constant for the whole repo group; build the lookup set once instead
        # of rebuilding it for every import spec of every file (was O(specs x files)).
        existing_keys = set(existing_paths)

        for record in repo_files:
            if record.extension not in JS_TS_EXTENSIONS:
                continue
            specs = _record_specs(record, _js_ts_specs)
            if specs is None:
                continue

            current_path = record.relative_path or record.path
            for spec in specs:
                target_relative_path = _resolve_js_ts_spec(current_path, spec, existing_keys)
                if target_relative_path is None:
                    continue
                target = existing_paths[target_relative_path]
                if target != record.path:
                    edges[record.path].add(target)

    return edges


def _extract_go_import_edges(files: list[FileRecord]) -> dict[str, set[str]]:
    """Extract import edges for Go files.

    Go imports use full module paths.  We resolve relative paths within
    the repo by matching the last path segments of the import spec against
    known Go file directories.
    """
    edges: dict[str, set[str]] = defaultdict(set)
    repo_groups: dict[str, list[FileRecord]] = defaultdict(list)

    for record in files:
        repo_groups[record.repo_label].append(record)

    for repo_files in repo_groups.values():
        # Map directory paths to their Go files for package resolution.
        go_files: list[FileRecord] = [r for r in repo_files if r.extension == ".go"]
        if not go_files:
            continue

        # Build a reverse index: directory -> set of Go file paths in that dir.
        dir_to_files: dict[str, set[str]] = defaultdict(set)
        for record in go_files:
            rel = record.relative_path or record.path
            parent = posixpath.dirname(rel)
            dir_to_files[parent].add(record.path)

        # Build suffix index for import resolution.
        # Import "github.com/user/repo/pkg/auth" should match dir "pkg/auth".
        suffix_to_dir: dict[str, str] = {}
        for dir_path in dir_to_files:
            if dir_path:
                parts = dir_path.split("/")
                for i in range(len(parts)):
                    suffix = "/".join(parts[i:])
                    if suffix not in suffix_to_dir:
                        suffix_to_dir[suffix] = dir_path

        for record in go_files:
            specs = _record_specs(record, _go_specs)
            if specs is None:
                continue

            current_dir = posixpath.dirname(record.relative_path or record.path)
            for spec in specs:
                # Try matching suffix of the import path against repo directories.
                parts = spec.split("/")
                matched_dir = None
                for i in range(len(parts)):
                    suffix = "/".join(parts[i:])
                    if suffix in suffix_to_dir:
                        matched_dir = suffix_to_dir[suffix]
                        break
                if matched_dir is not None and matched_dir != current_dir:
                    for target in dir_to_files[matched_dir]:
                        if target != record.path:
                            edges[record.path].add(target)

    return edges


# Per-process memoization of the last few graphs. A single pack builds the
# graph twice (scoring stage + compression stage) and plan-agent/simulate
# rebuild it once per workflow step over the same file set; each build reads
# every source file from disk, so on a monorepo that is the dominant cost.
# Keyed on (entrypoints, (path, content_hash)...) - the content hash already
# lives in the scan index, so the key needs no disk reads and invalidates
# automatically when any file changes.
_GRAPH_CACHE: dict[tuple, ImportGraph] = {}
_GRAPH_CACHE_MAX = 4


def _graph_cache_key(files: list[FileRecord], entrypoint_filenames: set[str] | None) -> tuple:
    ep = tuple(sorted(entrypoint_filenames)) if entrypoint_filenames else ()
    return (ep, tuple((f.path, f.content_hash) for f in files))


def build_import_graph(
    files: list[FileRecord], entrypoint_filenames: set[str] | None = None
) -> ImportGraph:
    """Build a deterministic, local-file import graph, memoized per file set."""
    key = _graph_cache_key(files, entrypoint_filenames)
    cached = _GRAPH_CACHE.get(key)
    if cached is not None:
        return cached
    graph = _build_import_graph_uncached(files, entrypoint_filenames)
    if len(_GRAPH_CACHE) >= _GRAPH_CACHE_MAX:
        _GRAPH_CACHE.clear()
    _GRAPH_CACHE[key] = graph
    return graph


def _build_import_graph_uncached(
    files: list[FileRecord], entrypoint_filenames: set[str] | None = None
) -> ImportGraph:
    """Build a deterministic, local-file import graph for Python, JS/TS, and Go files."""

    py_edges = _extract_python_import_edges(files)
    js_ts_edges = _extract_js_ts_import_edges(files)
    go_edges = _extract_go_import_edges(files)

    outgoing: dict[str, set[str]] = defaultdict(set)
    for source, targets in py_edges.items():
        outgoing[source].update(targets)
    for source, targets in js_ts_edges.items():
        outgoing[source].update(targets)
    for source, targets in go_edges.items():
        outgoing[source].update(targets)

    incoming: dict[str, set[str]] = defaultdict(set)
    for source, targets in outgoing.items():
        for target in targets:
            incoming[target].add(source)

    entry_names = {name.lower() for name in (entrypoint_filenames or set())}
    entrypoints: set[str] = set()
    if entry_names:
        for record in files:
            basename = (record.relative_path or record.path).rsplit("/", 1)[-1].lower()
            if basename in entry_names:
                entrypoints.add(record.path)

    return ImportGraph(
        outgoing={key: set(value) for key, value in outgoing.items()},
        incoming={key: set(value) for key, value in incoming.items()},
        entrypoints=entrypoints,
    )
