"""
Structural code search via ast-grep.

Wraps the ``ast-grep`` CLI (https://ast-grep.github.io). Patterns are
matched against the AST, not the raw text, so a pattern like
``class $NAME { $$$ }`` only matches actual class declarations - never
the same string appearing in a comment or docstring. Returns a list of
``StructuralMatch`` records ready for MCP/CLI consumption.

Two integration paths:
- The ``ast-grep`` (binary) on PATH (preferred). Fast, official, no deps.
- The ``ast_grep_py`` Python wheel (fallback when ast-grep is missing
  but the wheel is installed via ``redcon[ast_grep]``).

If neither is present, ``is_available()`` returns False and callers can
fall back to the regex-based ``redcon_search`` MCP tool.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StructuralMatch:
    path: str
    line: int
    column: int | None
    end_line: int
    text: str
    captures: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class StructuralSearchResult:
    pattern: str
    language: str | None
    scope: str
    backend: str  # "binary" | "python_wheel" | "unavailable"
    match_count: int
    file_count: int
    matches: tuple[StructuralMatch, ...]


_AVAILABILITY_BACKEND: str | None = None


def is_available() -> str:
    """Return the active backend name or ``"unavailable"``."""
    global _AVAILABILITY_BACKEND
    if _AVAILABILITY_BACKEND is not None:
        return _AVAILABILITY_BACKEND
    if shutil.which("ast-grep"):
        _AVAILABILITY_BACKEND = "binary"
        return _AVAILABILITY_BACKEND
    try:
        import ast_grep_py  # noqa: F401

        _AVAILABILITY_BACKEND = "python_wheel"
        return _AVAILABILITY_BACKEND
    except ImportError:
        _AVAILABILITY_BACKEND = "unavailable"
        return _AVAILABILITY_BACKEND


def reset_availability_for_testing() -> None:
    """Reset the cache so monkeypatched PATH changes take effect in tests."""
    global _AVAILABILITY_BACKEND
    _AVAILABILITY_BACKEND = None


def structural_search(
    pattern: str,
    *,
    scope: str | Path = ".",
    language: str | None = None,
    max_results: int = 200,
) -> StructuralSearchResult:
    """Run a structural search and return a normalised result."""
    backend = is_available()
    scope_str = str(Path(scope).resolve())
    if backend == "binary":
        return _search_binary(pattern, scope_str, language, max_results)
    if backend == "python_wheel":
        return _search_python_wheel(pattern, scope_str, language, max_results)
    return StructuralSearchResult(
        pattern=pattern,
        language=language,
        scope=scope_str,
        backend="unavailable",
        match_count=0,
        file_count=0,
        matches=(),
    )


def _search_binary(
    pattern: str, scope: str, language: str | None, max_results: int
) -> StructuralSearchResult:
    cmd = ["ast-grep", "run", "--pattern", pattern, "--json=compact"]
    if language:
        cmd.extend(["--lang", language])
    cmd.append(scope)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug("ast-grep binary failed: %s", e)
        return StructuralSearchResult(
            pattern=pattern,
            language=language,
            scope=scope,
            backend="binary",
            match_count=0,
            file_count=0,
            matches=(),
        )
    matches = _parse_ast_grep_json(proc.stdout, max_results=max_results)
    paths = {m.path for m in matches}
    return StructuralSearchResult(
        pattern=pattern,
        language=language,
        scope=scope,
        backend="binary",
        match_count=len(matches),
        file_count=len(paths),
        matches=tuple(matches),
    )


def _parse_ast_grep_json(payload: str, *, max_results: int) -> list[StructuralMatch]:
    matches: list[StructuralMatch] = []
    if not payload.strip():
        return matches
    # ast-grep --json=compact emits a JSON array on stdout.
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        # Some versions emit JSON-lines; fall through to per-line parsing.
        for raw in payload.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            match = _coerce_match(event)
            if match is not None:
                matches.append(match)
                if len(matches) >= max_results:
                    return matches
        return matches
    if isinstance(data, list):
        for event in data:
            match = _coerce_match(event)
            if match is not None:
                matches.append(match)
                if len(matches) >= max_results:
                    break
    return matches


def _coerce_match(event: dict) -> StructuralMatch | None:
    """Normalise one ast-grep JSON event into our StructuralMatch."""
    path = event.get("file") or event.get("path") or ""
    if not path:
        return None
    text = event.get("text") or event.get("lines") or ""
    range_info = event.get("range") or {}
    start = range_info.get("start") or {}
    end = range_info.get("end") or {}
    line = int(start.get("line", 0)) + 1 if "line" in start else int(event.get("line", 0))
    column = int(start.get("column", 0)) + 1 if "column" in start else None
    end_line = int(end.get("line", 0)) + 1 if "line" in end else line
    captures: list[tuple[str, str]] = []
    metavars = event.get("metaVariables") or {}
    single = metavars.get("single") or {}
    for name, info in single.items():
        captured_text = ""
        if isinstance(info, dict):
            captured_text = info.get("text", "") or ""
        captures.append((str(name), captured_text))
    return StructuralMatch(
        path=path,
        line=line,
        column=column,
        end_line=end_line,
        text=str(text)[:200],
        captures=tuple(captures),
    )


def _search_python_wheel(
    pattern: str, scope: str, language: str | None, max_results: int
) -> StructuralSearchResult:
    """Best-effort using ast_grep_py. The wheel API moves between releases;
    we wrap it defensively and fall through to ``unavailable`` on failure."""
    try:
        import ast_grep_py as ag
    except ImportError:
        return StructuralSearchResult(
            pattern=pattern,
            language=language,
            scope=scope,
            backend="python_wheel",
            match_count=0,
            file_count=0,
            matches=(),
        )

    matches: list[StructuralMatch] = []
    paths_seen: set[str] = set()
    scope_path = Path(scope)

    candidate_paths = [scope_path] if scope_path.is_file() else _walk_python_files(scope_path)

    for path in candidate_paths:
        if len(matches) >= max_results:
            break
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            sg_root = ag.SgRoot(source, language or "python")
            root = sg_root.root()
            for node in root.find_all(pattern=pattern):
                pos = node.range()
                matches.append(
                    StructuralMatch(
                        path=str(path),
                        line=pos.start.line + 1,
                        column=pos.start.column + 1,
                        end_line=pos.end.line + 1,
                        text=node.text()[:200],
                        captures=(),
                    )
                )
                paths_seen.add(str(path))
                if len(matches) >= max_results:
                    break
        except Exception as e:
            logger.debug("ast_grep_py search failed on %s: %s", path, e)
            continue

    return StructuralSearchResult(
        pattern=pattern,
        language=language,
        scope=scope,
        backend="python_wheel",
        match_count=len(matches),
        file_count=len(paths_seen),
        matches=tuple(matches),
    )


def _walk_python_files(scope: Path):
    if not scope.is_dir():
        return
    skip_dirs = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv"}
    for p in scope.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        yield p


def render_text(result: StructuralSearchResult) -> str:
    """Compact text representation for direct LLM consumption."""
    head = (
        f"structural-search backend={result.backend} "
        f"matches={result.match_count} files={result.file_count}"
    )
    if result.language:
        head += f" lang={result.language}"
    if result.backend == "unavailable":
        return (
            head
            + "\n(install ast-grep on PATH or `pip install redcon[ast_grep]`"
            " to enable structural search)"
        )
    if not result.matches:
        return head + "\n(no matches)"
    lines = [head]
    by_path: dict[str, list[StructuralMatch]] = {}
    for match in result.matches:
        by_path.setdefault(match.path, []).append(match)
    for path, items in by_path.items():
        lines.append(f"{path} ({len(items)})")
        for match in items[:5]:
            text_preview = match.text.split("\n", 1)[0][:120]
            lines.append(f"L{match.line}: {text_preview}")
        if len(items) > 5:
            lines.append(f"+{len(items) - 5} more")
    return "\n".join(lines)
