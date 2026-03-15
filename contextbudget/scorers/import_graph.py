from __future__ import annotations

"""Lightweight import/dependency graph extraction for local repository files."""

from collections import defaultdict
from dataclasses import dataclass
import posixpath
from pathlib import Path, PurePosixPath
import re

from contextbudget.schemas.models import FileRecord

PY_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$")
PY_FROM_RE = re.compile(r"^\s*from\s+([\.\w]+)\s+import\s+(.+)$")

JS_IMPORT_FROM_RE = re.compile(r"(?:import|export)\s+(?:type\s+)?[^\n;]*?\sfrom\s+[\"']([^\"']+)[\"']")
JS_SIDE_EFFECT_IMPORT_RE = re.compile(r"\bimport\s+[\"']([^\"']+)[\"']")
JS_REQUIRE_RE = re.compile(r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)")

JS_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


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


def _resolve_python_module_spec(spec: str, current_path: str, module_map: dict[str, str]) -> str | None:
    if not spec:
        return None

    candidates: list[str] = []
    if spec.startswith("."):
        level = len(spec) - len(spec.lstrip("."))
        remainder = spec.lstrip(".")
        current = list(PurePosixPath(current_path).with_suffix("").parts)
        if current and current[-1] == "__init__":
            package_parts = current[:-1]
        else:
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
            try:
                source = Path(record.absolute_path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            current_path = record.relative_path or record.path

            for raw_line in source.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                import_match = PY_IMPORT_RE.match(raw_line)
                if import_match:
                    modules = [token.strip().split(" as ")[0].strip() for token in import_match.group(1).split(",")]
                    for module in modules:
                        target = _resolve_python_module_spec(module, current_path, module_map)
                        if target and target != record.path:
                            edges[record.path].add(target)
                    continue

                from_match = PY_FROM_RE.match(raw_line)
                if not from_match:
                    continue

                module_spec = from_match.group(1).strip()
                imported_items = [token.strip().split(" as ")[0].strip() for token in from_match.group(2).split(",")]
                specs = [module_spec]
                for imported in imported_items:
                    if imported in {"", "*"}:
                        continue
                    if module_spec.startswith("."):
                        specs.append(f"{module_spec}{imported}")
                    else:
                        specs.append(f"{module_spec}.{imported}")

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

        for record in repo_files:
            if record.extension not in JS_TS_EXTENSIONS:
                continue
            try:
                source = Path(record.absolute_path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            specs: set[str] = set(JS_IMPORT_FROM_RE.findall(source))
            specs.update(JS_SIDE_EFFECT_IMPORT_RE.findall(source))
            specs.update(JS_REQUIRE_RE.findall(source))

            current_path = record.relative_path or record.path
            for spec in specs:
                target_relative_path = _resolve_js_ts_spec(current_path, spec, set(existing_paths))
                if target_relative_path is None:
                    continue
                target = existing_paths[target_relative_path]
                if target != record.path:
                    edges[record.path].add(target)

    return edges


def build_import_graph(files: list[FileRecord], entrypoint_filenames: set[str] | None = None) -> ImportGraph:
    """Build a deterministic, local-file import graph for Python and JS/TS files."""

    py_edges = _extract_python_import_edges(files)
    js_ts_edges = _extract_js_ts_import_edges(files)

    outgoing: dict[str, set[str]] = defaultdict(set)
    for source, targets in py_edges.items():
        outgoing[source].update(targets)
    for source, targets in js_ts_edges.items():
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
