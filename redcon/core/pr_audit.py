from __future__ import annotations

"""Pull-request diff analysis for context-growth auditing."""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath, Path
import json
import re
import sys

from redcon.config import RedconConfig
from redcon.plugins.registry import ResolvedPlugins
from redcon.scanners.git_diff import collect_pull_request_diff
from redcon.scanners.repository import scan_repository
from redcon.schemas.models import (
    PrAuditDependency,
    PrAuditFile,
    PrAuditReport,
    PrAuditSnapshot,
    PrAuditSummary,
    TokenEstimatorReport,
)

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found, assignment]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]


_PY_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$")
_PY_FROM_RE = re.compile(r"^\s*from\s+([\.\w]+)\s+import\s+(.+)$")
_JS_IMPORT_FROM_RE = re.compile(r"(?:import|export)\s+(?:type\s+)?[^\n;]*?\sfrom\s+[\"']([^\"']+)[\"']")
_JS_SIDE_EFFECT_IMPORT_RE = re.compile(r"\bimport\s+[\"']([^\"']+)[\"']")
_JS_REQUIRE_RE = re.compile(r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)")
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
_PY_SYMBOL_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+\w+", re.MULTILINE)
_JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+function|function|class|interface|type\s+\w+\s*=|const\s+\w+\s*=\s*(?:async\s*)?\()",
    re.MULTILINE,
)
_GENERIC_SYMBOL_RE = re.compile(r"^\s*(?:def|class|function|interface|type|struct|enum)\s+\w+", re.MULTILINE)
_BRANCH_RE = re.compile(r"\b(?:if|elif|else|for|while|try|except|catch|switch|case|match|finally)\b")
_JS_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_PYTHON_STDLIB = set(getattr(sys, "stdlib_module_names", set())).union(set(sys.builtin_module_names))
_NODE_BUILTINS = {
    "assert",
    "buffer",
    "child_process",
    "crypto",
    "events",
    "fs",
    "http",
    "https",
    "module",
    "net",
    "os",
    "path",
    "stream",
    "timers",
    "tls",
    "url",
    "util",
    "zlib",
}


def _count_lines(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _count_symbols(path: str, text: str) -> int:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".py":
        return len(_PY_SYMBOL_RE.findall(text))
    if suffix in _JS_TS_EXTENSIONS:
        return len(_JS_SYMBOL_RE.findall(text))
    return len(_GENERIC_SYMBOL_RE.findall(text))


def _extract_python_imports(text: str) -> set[str]:
    specs: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        import_match = _PY_IMPORT_RE.match(raw_line)
        if import_match:
            for token in import_match.group(1).split(","):
                module = token.strip().split(" as ")[0].strip()
                if module:
                    specs.add(module)
            continue
        from_match = _PY_FROM_RE.match(raw_line)
        if from_match:
            module_spec = from_match.group(1).strip()
            if module_spec:
                specs.add(module_spec)
    return specs


def _extract_js_ts_imports(text: str) -> set[str]:
    specs: set[str] = set(_JS_IMPORT_FROM_RE.findall(text))
    specs.update(_JS_SIDE_EFFECT_IMPORT_RE.findall(text))
    specs.update(_JS_REQUIRE_RE.findall(text))
    return {item.strip() for item in specs if item.strip()}


def _normalize_dependency_name(raw: str) -> str:
    candidate = raw.strip()
    if not candidate or candidate.startswith(("-", "#")):
        return ""
    if " @ " in candidate:
        candidate = candidate.split(" @ ", 1)[0].strip()
    match = _REQUIREMENT_NAME_RE.match(candidate)
    if not match:
        return ""
    return match.group(1).lower()


def _extract_requirements_dependencies(text: str) -> set[str]:
    dependencies: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        name = _normalize_dependency_name(line)
        if name:
            dependencies.add(name)
    return dependencies


def _extract_package_json_dependencies(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, dict):
        return set()
    dependencies: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = data.get(section, {})
        if not isinstance(value, dict):
            continue
        dependencies.update(str(name).strip().lower() for name in value if str(name).strip())
    return dependencies


def _parse_pyproject_dependency_entry(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    return _normalize_dependency_name(raw)


def _extract_pyproject_dependencies(text: str) -> set[str]:
    if tomllib is None:
        return set()
    try:
        data = tomllib.loads(text)
    except Exception:  # pragma: no cover - invalid TOML input
        return set()
    if not isinstance(data, dict):
        return set()

    dependencies: set[str] = set()

    project = data.get("project", {})
    if isinstance(project, dict):
        project_deps = project.get("dependencies", [])
        if isinstance(project_deps, list):
            dependencies.update(
                dep for dep in (_parse_pyproject_dependency_entry(item) for item in project_deps) if dep
            )
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group in optional.values():
                if not isinstance(group, list):
                    continue
                dependencies.update(
                    dep for dep in (_parse_pyproject_dependency_entry(item) for item in group) if dep
                )

    tool = data.get("tool", {})
    if isinstance(tool, dict):
        poetry = tool.get("poetry", {})
        if isinstance(poetry, dict):
            poetry_deps = poetry.get("dependencies", {})
            if isinstance(poetry_deps, dict):
                for key in poetry_deps:
                    name = str(key).strip().lower()
                    if name and name != "python":
                        dependencies.add(name)
            poetry_groups = poetry.get("group", {})
            if isinstance(poetry_groups, dict):
                for group in poetry_groups.values():
                    if not isinstance(group, dict):
                        continue
                    group_deps = group.get("dependencies", {})
                    if not isinstance(group_deps, dict):
                        continue
                    for key in group_deps:
                        name = str(key).strip().lower()
                        if name and name != "python":
                            dependencies.add(name)
    return dependencies


def _extract_manifest_dependencies(path: str, text: str) -> set[str]:
    name = PurePosixPath(path).name.lower()
    if name == "package.json":
        return _extract_package_json_dependencies(text)
    if name == "pyproject.toml":
        return _extract_pyproject_dependencies(text)
    if name.startswith("requirements") and name.endswith(".txt"):
        return _extract_requirements_dependencies(text)
    return set()


def _extract_source_imports(path: str, text: str) -> set[str]:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".py":
        return _extract_python_imports(text)
    if suffix in _JS_TS_EXTENSIONS:
        return _extract_js_ts_imports(text)
    return set()


def _build_local_python_modules(repo: Path, config: RedconConfig) -> set[str]:
    files = scan_repository(
        repo,
        max_file_size_bytes=config.scan.max_file_size_bytes,
        preview_chars=config.scan.preview_chars,
        include_globs=config.scan.include_globs,
        ignore_globs=config.scan.ignore_globs,
        ignore_dirs=config.scan.ignore_dirs,
        binary_extensions=config.scan.binary_extensions,
    )
    modules: set[str] = set()
    basename_candidates: dict[str, int] = {}
    for record in files:
        if record.extension != ".py":
            continue
        pure = PurePosixPath(record.relative_path or record.path)
        module_parts = list(pure.with_suffix("").parts)
        if not module_parts:
            continue
        if module_parts[-1] == "__init__":
            package = ".".join(module_parts[:-1])
            if package:
                modules.add(package)
            continue
        module = ".".join(module_parts)
        modules.add(module)
        basename = module_parts[-1]
        basename_candidates[basename] = basename_candidates.get(basename, 0) + 1
    for module in list(modules):
        basename = module.rsplit(".", 1)[-1]
        if basename_candidates.get(basename) == 1:
            modules.add(basename)
    return modules


def _is_external_python_dependency(spec: str, local_modules: set[str]) -> bool:
    if not spec or spec.startswith("."):
        return False
    root = spec.split(".", 1)[0]
    return spec not in local_modules and root not in local_modules and root not in _PYTHON_STDLIB


def _normalize_js_dependency(spec: str) -> str:
    if not spec or spec.startswith((".", "/", "~/", "@/")):
        return ""
    if spec.startswith("node:"):
        return ""
    if spec.startswith("@"):
        parts = spec.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return spec
    name = spec.split("/", 1)[0]
    if name in _NODE_BUILTINS:
        return ""
    return name


def _extract_external_source_dependencies(path: str, text: str, local_python_modules: set[str]) -> set[str]:
    suffix = PurePosixPath(path).suffix.lower()
    imports = _extract_source_imports(path, text)
    if suffix == ".py":
        return {
            spec.split(".", 1)[0].lower()
            for spec in imports
            if _is_external_python_dependency(spec, local_python_modules)
        }
    if suffix in _JS_TS_EXTENSIONS:
        return {
            dependency.lower()
            for dependency in (_normalize_js_dependency(spec) for spec in imports)
            if dependency
        }
    return set()


def _build_snapshot(path: str, text: str, estimate_tokens) -> PrAuditSnapshot:
    if not text:
        return PrAuditSnapshot()
    manifest_dependencies = _extract_manifest_dependencies(path, text)
    import_specs = _extract_source_imports(path, text)
    line_count = _count_lines(text)
    symbol_count = _count_symbols(path, text)
    branch_count = len(_BRANCH_RE.findall(text))
    import_count = len(import_specs) + len(manifest_dependencies)
    complexity_score = round((line_count / 60.0) + (symbol_count * 1.2) + (branch_count * 0.75) + (import_count * 0.6), 2)
    return PrAuditSnapshot(
        size_bytes=len(text.encode("utf-8")),
        line_count=line_count,
        token_count=int(estimate_tokens(text)),
        symbol_count=symbol_count,
        branch_count=branch_count,
        import_count=import_count,
        complexity_score=complexity_score,
    )


def _is_complexity_increase(file_audit: PrAuditFile) -> bool:
    if file_audit.complexity_delta >= 1.0:
        return True
    if file_audit.complexity_delta < 0.5:
        return False
    return (
        file_audit.after.import_count > file_audit.before.import_count
        or file_audit.after.branch_count > file_audit.before.branch_count
        or file_audit.after.symbol_count > file_audit.before.symbol_count
    )


def _build_suggestions(
    *,
    files_causing_increase: list[PrAuditFile],
    new_dependencies: list[PrAuditDependency],
    increased_complexity: list[str],
) -> list[str]:
    suggestions: list[str] = []
    if files_causing_increase:
        suggestions.append(
            f"Extract helper logic or constants from `{files_causing_increase[0].path}` so it does not keep growing as a default context anchor."
        )
    if new_dependencies:
        suggestions.append(
            f"Wrap new dependency usage behind a narrow adapter so common agent context does not need the full `{new_dependencies[0].name}` surface."
        )
    if increased_complexity:
        suggestions.append(
            f"Split routing or service branches in `{increased_complexity[0]}` into smaller focused units to reduce context fan-out."
        )
    if len(files_causing_increase) >= 4:
        suggestions.append("Consider splitting the PR into narrower changes so agent context stays focused on one subsystem.")
    if not suggestions:
        suggestions.append("No material context-growth drivers were detected in the analyzed diff.")
    return suggestions[:4]


def analyze_pull_request(
    repo: Path,
    *,
    base_ref: str | None,
    head_ref: str | None,
    config: RedconConfig,
    plugins: ResolvedPlugins,
) -> PrAuditReport:
    """Analyze a pull-request diff and produce a deterministic audit artifact."""

    resolved_refs, diff_files = collect_pull_request_diff(
        repo,
        base_ref=base_ref,
        head_ref=head_ref,
        include_globs=config.scan.include_globs,
        ignore_globs=config.scan.ignore_globs,
        ignore_dirs=config.scan.ignore_dirs,
        binary_extensions=config.scan.binary_extensions,
    )
    local_python_modules = _build_local_python_modules(repo, config)

    analyzed_files: list[PrAuditFile] = []
    dependency_index: dict[str, PrAuditDependency] = {}

    for item in diff_files:
        before_path = item.previous_path or item.path
        after_path = item.path
        before_snapshot = _build_snapshot(before_path, item.before_text, plugins.estimate_tokens)
        after_snapshot = _build_snapshot(after_path, item.after_text, plugins.estimate_tokens)

        before_manifest = _extract_manifest_dependencies(before_path, item.before_text)
        after_manifest = _extract_manifest_dependencies(after_path, item.after_text)
        before_source_deps = _extract_external_source_dependencies(before_path, item.before_text, local_python_modules)
        after_source_deps = _extract_external_source_dependencies(after_path, item.after_text, local_python_modules)

        new_manifest_deps = sorted(after_manifest - before_manifest)
        removed_manifest_deps = sorted(before_manifest - after_manifest)
        new_source_deps = sorted(after_source_deps - before_source_deps - set(new_manifest_deps))
        removed_source_deps = sorted(before_source_deps - after_source_deps - set(removed_manifest_deps))

        file_audit = PrAuditFile(
            path=item.path,
            previous_path=item.previous_path,
            change_type=item.change_type,
            analyzed=item.analyzed,
            binary=item.before_binary or item.after_binary,
            skipped_reason=item.skipped_reason,
            before=before_snapshot,
            after=after_snapshot,
            token_delta=after_snapshot.token_count - before_snapshot.token_count,
            size_delta=after_snapshot.size_bytes - before_snapshot.size_bytes,
            line_delta=after_snapshot.line_count - before_snapshot.line_count,
            complexity_delta=round(after_snapshot.complexity_score - before_snapshot.complexity_score, 2),
            new_dependencies=sorted(set(new_manifest_deps + new_source_deps)),
            removed_dependencies=sorted(set(removed_manifest_deps + removed_source_deps)),
        )

        if not item.analyzed:
            analyzed_files.append(file_audit)
            continue

        if file_audit.token_delta > 0 or file_audit.size_delta > 0 or file_audit.line_delta > 0:
            file_audit.growth_reasons.append("larger_file")
        if file_audit.new_dependencies:
            file_audit.growth_reasons.append("new_dependencies")
        if _is_complexity_increase(file_audit):
            file_audit.growth_reasons.append("increased_context_complexity")

        for name in new_manifest_deps:
            dependency_index.setdefault(name, PrAuditDependency(name=name, source="manifest", file=item.path))
        for name in new_source_deps:
            dependency_index.setdefault(name, PrAuditDependency(name=name, source="source_import", file=item.path))

        analyzed_files.append(file_audit)

    growth_files = [
        item
        for item in analyzed_files
        if item.analyzed and (item.token_delta > 0 or item.growth_reasons)
    ]
    growth_files.sort(
        key=lambda item: (
            -item.token_delta,
            -item.complexity_delta,
            -len(item.new_dependencies),
            item.path,
        )
    )

    larger_files = [item.path for item in analyzed_files if item.analyzed and item.token_delta > 0]
    increased_complexity = [
        item.path
        for item in analyzed_files
        if item.analyzed and "increased_context_complexity" in item.growth_reasons
    ]
    new_dependencies = sorted(
        dependency_index.values(),
        key=lambda item: (item.name, item.file, item.source),
    )

    estimated_tokens_before = sum(item.before.token_count for item in analyzed_files if item.analyzed)
    estimated_tokens_after = sum(item.after.token_count for item in analyzed_files if item.analyzed)
    estimated_token_delta = estimated_tokens_after - estimated_tokens_before
    if estimated_tokens_before > 0:
        estimated_token_delta_pct = round((estimated_token_delta / estimated_tokens_before) * 100.0, 1)
    elif estimated_tokens_after > 0:
        estimated_token_delta_pct = 100.0
    else:
        estimated_token_delta_pct = 0.0

    summary = PrAuditSummary(
        changed_files=len(diff_files),
        analyzed_files=sum(1 for item in analyzed_files if item.analyzed),
        skipped_files=sum(1 for item in analyzed_files if not item.analyzed),
        estimated_tokens_before=estimated_tokens_before,
        estimated_tokens_after=estimated_tokens_after,
        estimated_token_delta=estimated_token_delta,
        estimated_token_delta_pct=estimated_token_delta_pct,
        larger_file_count=len(larger_files),
        new_dependency_count=len(new_dependencies),
        increased_complexity_count=len(increased_complexity),
    )

    suggestions = _build_suggestions(
        files_causing_increase=growth_files,
        new_dependencies=new_dependencies,
        increased_complexity=increased_complexity,
    )

    return PrAuditReport(
        command="pr-audit",
        repo=str(repo),
        base_ref=resolved_refs.base_ref,
        head_ref=resolved_refs.head_ref,
        base_commit=resolved_refs.base_commit,
        head_commit=resolved_refs.head_commit,
        merge_base=resolved_refs.merge_base,
        generated_at=datetime.now(timezone.utc).isoformat(),
        token_estimator=TokenEstimatorReport(**plugins.token_estimator_report),
        summary=summary,
        files=analyzed_files,
        files_causing_increase=[item.path for item in growth_files[:10]],
        larger_files=larger_files,
        new_dependencies=new_dependencies,
        increased_complexity=increased_complexity,
        suggestions=suggestions,
        comment_markdown="",
    )


def pr_audit_as_dict(report: PrAuditReport) -> dict:
    """Convert a PR audit report into a JSON-serializable dict."""

    return asdict(report)
