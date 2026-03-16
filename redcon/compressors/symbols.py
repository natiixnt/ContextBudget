from __future__ import annotations

"""Deterministic symbol-level extraction for supported source files."""

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable


TS_FUNC_RE = re.compile(r"^(export\s+)?(async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
TS_CLASS_RE = re.compile(r"^(export\s+)?class\s+([A-Za-z_$][\w$]*)\b")
TS_INTERFACE_RE = re.compile(r"^(export\s+)?interface\s+([A-Za-z_$][\w$]*)\b")
TS_TYPE_RE = re.compile(r"^(export\s+)?type\s+([A-Za-z_$][\w$]*)\b")
TS_ARROW_RE = re.compile(
    r"^(export\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
TS_EXPORT_VALUE_RE = re.compile(r"^export\s+(const|let|var)\s+([A-Za-z_$][\w$]*)\b")

GO_FUNC_RE = re.compile(r"^func\s+(\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
GO_TYPE_RE = re.compile(r"^type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\b")
GO_VAR_CONST_RE = re.compile(r"^(var|const)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


@dataclass(slots=True)
class SymbolExtraction:
    """Symbol-level extraction output for one file."""

    chunk_strategy: str
    chunk_reason: str
    selected_ranges: list[dict[str, int | str]]
    symbols: list[dict[str, int | str | bool]]
    text: str


@dataclass(slots=True)
class _SymbolCandidate:
    name: str
    symbol_type: str
    start: int
    end: int
    exported: bool
    score: float


_SYMBOL_TYPE_WEIGHTS = {
    "function": 2.2,
    "class": 2.1,
    "interface": 2.0,
    "type": 1.9,
    "export": 1.8,
}


def _is_js_comment(stripped: str) -> bool:
    return (
        stripped.startswith("//")
        or stripped.startswith("/*")
        or stripped.startswith("*")
        or stripped.endswith("*/")
    )


def _is_go_comment(stripped: str) -> bool:
    return stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*")


def _indent_level(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _brace_delta(line: str) -> int:
    return line.count("{") - line.count("}")


def _include_leading_comments(lines: list[str], start: int, *, comment_prefixes: tuple[str, ...]) -> int:
    idx = start - 1
    while idx >= 0:
        stripped = lines[idx].strip()
        if not stripped:
            if idx == start - 1:
                idx -= 1
                continue
            break
        if stripped.startswith(comment_prefixes):
            idx -= 1
            continue
        break
    return idx + 1


def _include_leading_comments_by_predicate(
    lines: list[str],
    start: int,
    *,
    is_comment: Callable[[str], bool],
) -> int:
    idx = start - 1
    while idx >= 0:
        stripped = lines[idx].strip()
        if not stripped:
            if idx == start - 1:
                idx -= 1
                continue
            break
        if is_comment(stripped):
            idx -= 1
            continue
        break
    return idx + 1


def _expand_python_block(lines: list[str], start: int) -> int:
    base_indent = _indent_level(lines[start])
    end = start
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if not stripped:
            continue
        indent = _indent_level(lines[idx])
        if indent <= base_indent and not stripped.startswith(("#", "@")):
            break
        end = idx
    if end == start:
        end = min(len(lines) - 1, start + 12)
    return end


def _expand_brace_block(lines: list[str], start: int, *, max_lines: int = 240) -> int:
    end = start
    balance = _brace_delta(lines[start])
    saw_open = "{" in lines[start]

    for idx in range(start + 1, min(len(lines), start + max_lines + 1)):
        line = lines[idx]
        if saw_open:
            balance += _brace_delta(line)
            end = idx
            if balance <= 0:
                break
        else:
            end = idx
            if "{" in line:
                saw_open = True
                balance += _brace_delta(line)
                if balance <= 0:
                    break
            elif idx - start >= 8:
                break

    if end == start and not saw_open:
        end = min(len(lines) - 1, start + 8)
    return end


def _keyword_hits(text: str, keywords: list[str]) -> int:
    lower = text.lower()
    return sum(1 for keyword in keywords if keyword and keyword in lower)


def _make_candidate(
    *,
    name: str,
    symbol_type: str,
    start: int,
    end: int,
    exported: bool,
    text: str,
    keywords: list[str],
) -> _SymbolCandidate:
    score = _SYMBOL_TYPE_WEIGHTS.get(symbol_type, 1.0)
    score += 1.75 * _keyword_hits(text, keywords)
    if exported:
        score += 0.6
    return _SymbolCandidate(
        name=name,
        symbol_type=symbol_type,
        start=start,
        end=end,
        exported=exported,
        score=score,
    )


def _extract_python_export_names(tree: ast.Module) -> set[str]:
    exports: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue

        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            continue
        for item in value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                exports.add(item.value)
    return exports


def _python_symbol_candidates(file_path: str, text: str, keywords: list[str]) -> list[_SymbolCandidate]:
    lines = text.splitlines()
    if not lines:
        return []

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    exported_names = _extract_python_export_names(tree)
    candidates: list[_SymbolCandidate] = []

    for node in tree.body:
        symbol_name = ""
        symbol_type = ""
        exported = False

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol_name = node.name
            symbol_type = "function"
            exported = node.name in exported_names or not node.name.startswith("_")
        elif isinstance(node, ast.ClassDef):
            symbol_name = node.name
            symbol_type = "class"
            exported = node.name in exported_names or not node.name.startswith("_")
        else:
            continue

        node_start = max(0, int(getattr(node, "lineno", 1)) - 1)
        for decorator in getattr(node, "decorator_list", []):
            decorator_start = max(0, int(getattr(decorator, "lineno", node_start + 1)) - 1)
            node_start = min(node_start, decorator_start)
        start = _include_leading_comments(lines, node_start, comment_prefixes=("#",))
        end = int(getattr(node, "end_lineno", node.lineno)) - 1
        end = min(end, len(lines) - 1)
        if end < start:
            end = _expand_python_block(lines, node_start)

        source_lines = lines[start : end + 1]
        docstring = ast.get_docstring(node, clean=False) or ""
        search_text = "\n".join(source_lines)
        if docstring:
            search_text = f"{search_text}\n{docstring}"

        candidates.append(
            _make_candidate(
                name=symbol_name,
                symbol_type=symbol_type,
                start=start,
                end=end,
                exported=exported,
                text=search_text,
                keywords=keywords,
            )
        )

    return candidates


def _ts_js_symbol_candidates(file_path: str, text: str, keywords: list[str]) -> list[_SymbolCandidate]:
    del file_path
    lines = text.splitlines()
    candidates: list[_SymbolCandidate] = []

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        match = TS_FUNC_RE.match(stripped)
        if match:
            exported = bool(match.group(1))
            name = match.group(3)
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_js_comment)
            end = _expand_brace_block(lines, idx)
            source = "\n".join(lines[start : end + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="function",
                    start=start,
                    end=end,
                    exported=exported,
                    text=source,
                    keywords=keywords,
                )
            )
            continue

        match = TS_CLASS_RE.match(stripped)
        if match:
            exported = bool(match.group(1))
            name = match.group(2)
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_js_comment)
            end = _expand_brace_block(lines, idx)
            source = "\n".join(lines[start : end + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="class",
                    start=start,
                    end=end,
                    exported=exported,
                    text=source,
                    keywords=keywords,
                )
            )
            continue

        match = TS_INTERFACE_RE.match(stripped)
        if match:
            exported = bool(match.group(1))
            name = match.group(2)
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_js_comment)
            end = _expand_brace_block(lines, idx)
            source = "\n".join(lines[start : end + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="interface",
                    start=start,
                    end=end,
                    exported=exported,
                    text=source,
                    keywords=keywords,
                )
            )
            continue

        match = TS_TYPE_RE.match(stripped)
        if match:
            exported = bool(match.group(1))
            name = match.group(2)
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_js_comment)
            end = idx
            if "{" in stripped:
                end = _expand_brace_block(lines, idx)
            source = "\n".join(lines[start : end + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="type",
                    start=start,
                    end=end,
                    exported=exported,
                    text=source,
                    keywords=keywords,
                )
            )
            continue

        match = TS_ARROW_RE.match(stripped)
        if match:
            exported = bool(match.group(1))
            name = match.group(3)
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_js_comment)
            end = _expand_brace_block(lines, idx)
            source = "\n".join(lines[start : end + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="function",
                    start=start,
                    end=end,
                    exported=exported,
                    text=source,
                    keywords=keywords,
                )
            )
            continue

        match = TS_EXPORT_VALUE_RE.match(stripped)
        if match:
            name = match.group(2)
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_js_comment)
            source = "\n".join(lines[start : idx + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="export",
                    start=start,
                    end=idx,
                    exported=True,
                    text=source,
                    keywords=keywords,
                )
            )

    return candidates


def _go_symbol_candidates(file_path: str, text: str, keywords: list[str]) -> list[_SymbolCandidate]:
    del file_path
    lines = text.splitlines()
    candidates: list[_SymbolCandidate] = []

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        match = GO_FUNC_RE.match(stripped)
        if match:
            name = match.group(2)
            exported = bool(name and name[:1].isupper())
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_go_comment)
            end = _expand_brace_block(lines, idx)
            source = "\n".join(lines[start : end + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="function",
                    start=start,
                    end=end,
                    exported=exported,
                    text=source,
                    keywords=keywords,
                )
            )
            continue

        match = GO_TYPE_RE.match(stripped)
        if match:
            name = match.group(1)
            kind = match.group(2)
            exported = bool(name and name[:1].isupper())
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_go_comment)
            end = _expand_brace_block(lines, idx)
            source = "\n".join(lines[start : end + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="interface" if kind == "interface" else "type",
                    start=start,
                    end=end,
                    exported=exported,
                    text=source,
                    keywords=keywords,
                )
            )
            continue

        match = GO_VAR_CONST_RE.match(stripped)
        if match:
            name = match.group(2)
            exported = bool(name and name[:1].isupper())
            start = _include_leading_comments_by_predicate(lines, idx, is_comment=_is_go_comment)
            source = "\n".join(lines[start : idx + 1])
            candidates.append(
                _make_candidate(
                    name=name,
                    symbol_type="export" if exported else "type",
                    start=start,
                    end=idx,
                    exported=exported,
                    text=source,
                    keywords=keywords,
                )
            )

    return candidates


def _trim_candidate(candidate: _SymbolCandidate, budget: int) -> _SymbolCandidate:
    length = candidate.end - candidate.start + 1
    if length <= budget:
        return candidate
    return _SymbolCandidate(
        name=candidate.name,
        symbol_type=candidate.symbol_type,
        start=candidate.start,
        end=candidate.start + budget - 1,
        exported=candidate.exported,
        score=candidate.score,
    )


_STUB_SCORE_THRESHOLD = 3.5  # symbols below this get signature-only stubs (no body)
_MAX_CLASS_BODY_LINES = 40   # class bodies beyond this are condensed to method stubs
_MAX_FUNC_BODY_LINES = 60    # standalone functions beyond this get a tail truncation

_PY_METHOD_RE = re.compile(r"^(\s+)(async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# Matches TS/JS method declarations indented at least 2 spaces inside a class body.
# Intentionally loose — matches the start of any indented name followed by ( or <.
_TS_METHOD_RE = re.compile(
    r"^\s{2,}"
    r"(?:(?:public|private|protected|static|async|override|abstract|readonly)\s+)*"
    r"(?:get\s+|set\s+|async\s+)?"
    r"(?!(?:if|for|while|switch|return|const|let|var|new|throw|import|export)\b)"
    r"([A-Za-z_$][\w$]*)\s*[(<]"
)


def _strip_python_docstring(body_lines: list[str]) -> list[str]:
    """Remove the first docstring (triple-quoted string) from a Python body.

    ``body_lines[0]`` is the ``def``/``class`` line.  Modifies nothing in-place;
    returns a new list with the docstring lines removed.
    """
    if len(body_lines) < 2:
        return body_lines
    i = 1
    while i < len(body_lines) and not body_lines[i].strip():
        i += 1
    if i >= len(body_lines):
        return body_lines
    first = body_lines[i].strip()
    for delim in ('"""', "'''"):
        if first.startswith(delim):
            rest = first[len(delim):]
            if rest.endswith(delim) and len(rest) >= len(delim):
                # Single-line docstring
                return body_lines[:i] + body_lines[i + 1:]
            # Multi-line: scan for closing delimiter
            j = i + 1
            while j < len(body_lines):
                if delim in body_lines[j]:
                    return body_lines[:i] + body_lines[j + 1:]
                j += 1
            break
    return body_lines


def _condense_class_body(body_lines: list[str], max_lines: int, method_re: re.Pattern[str]) -> str:
    """Render first *max_lines* of a class, then stub remaining methods.

    Remaining methods beyond the line cap are collapsed to their
    signature line + `` ...`` so the reader knows they exist.
    """
    if len(body_lines) <= max_lines:
        return "\n".join(body_lines)

    kept = "\n".join(body_lines[:max_lines])
    stubs: list[str] = []
    for line in body_lines[max_lines:]:
        if method_re.match(line):
            stubs.append(line.rstrip() + " ...")
    if stubs:
        return kept + "\n    # --- remaining methods (signatures only) ---\n" + "\n".join(stubs)
    omitted = len(body_lines) - max_lines
    return kept + f"\n    # ... ({omitted} lines omitted)"


def _condense_func_body(body_lines: list[str], max_lines: int) -> str:
    """Truncate a long standalone function body and append a line-count note."""
    if len(body_lines) <= max_lines:
        return "\n".join(body_lines)
    omitted = len(body_lines) - max_lines
    return "\n".join(body_lines[:max_lines]) + f"\n    # ... ({omitted} lines omitted)"


def _select_symbol_candidates(candidates: list[_SymbolCandidate], line_budget: int, max_symbols: int = 4) -> list[_SymbolCandidate]:
    if not candidates:
        return []

    ordered = sorted(
        candidates,
        key=lambda item: (-item.score, -int(item.exported), item.start, item.end, item.name),
    )
    remaining = max(1, line_budget)
    selected: list[_SymbolCandidate] = []

    for candidate in ordered:
        if len(selected) >= max_symbols or remaining <= 0:
            break
        length = candidate.end - candidate.start + 1
        if length > remaining and selected:
            continue
        chosen = _trim_candidate(candidate, remaining)
        selected.append(chosen)
        remaining -= chosen.end - chosen.start + 1

    if not selected:
        selected.append(_trim_candidate(ordered[0], remaining))

    selected.sort(key=lambda item: item.start)
    return selected


def _render_selected_symbols(lines: list[str], selected: list[_SymbolCandidate], language: str) -> str:
    parts: list[str] = []
    is_py = language == "python"
    method_re = _TS_METHOD_RE if language in {"typescript", "javascript"} else _PY_METHOD_RE
    for symbol in selected:
        export_marker = " exported" if symbol.exported else ""
        header = (
            f"## {symbol.symbol_type} {symbol.name}{export_marker} "
            f"lines {symbol.start + 1}-{symbol.end + 1}"
        )
        if symbol.score < _STUB_SCORE_THRESHOLD and symbol.end > symbol.start:
            # Low keyword relevance — include only the signature line to save tokens.
            body = lines[symbol.start] + " ..."
        else:
            body_lines = lines[symbol.start : symbol.end + 1]
            if is_py:
                body_lines = _strip_python_docstring(body_lines)
            if symbol.symbol_type == "class" and len(body_lines) > _MAX_CLASS_BODY_LINES:
                body = _condense_class_body(body_lines, _MAX_CLASS_BODY_LINES, method_re)
            elif symbol.symbol_type == "function" and len(body_lines) > _MAX_FUNC_BODY_LINES:
                body = _condense_func_body(body_lines, _MAX_FUNC_BODY_LINES)
            else:
                body = "\n".join(body_lines)
        parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)


def select_symbol_aware_chunks(
    *,
    file_path: str,
    text: str,
    keywords: list[str],
    line_budget: int,
) -> SymbolExtraction | None:
    """Extract relevant symbols from supported source files under a line budget."""

    lines = text.splitlines()
    if not lines:
        return None

    extension = Path(file_path).suffix.lower()
    language = ""
    candidates: list[_SymbolCandidate] = []

    if extension == ".py":
        language = "python"
        candidates = _python_symbol_candidates(file_path, text, keywords)
    elif extension in {".ts", ".tsx"}:
        language = "typescript"
        candidates = _ts_js_symbol_candidates(file_path, text, keywords)
    elif extension in {".js", ".jsx", ".mjs", ".cjs"}:
        language = "javascript"
        candidates = _ts_js_symbol_candidates(file_path, text, keywords)
    elif extension == ".go":
        language = "go"
        candidates = _go_symbol_candidates(file_path, text, keywords)

    if not language or not candidates:
        return None

    selected = _select_symbol_candidates(candidates, line_budget=line_budget)
    if not selected:
        return None

    has_keyword_match = any(_keyword_hits(symbol.name.lower(), keywords) for symbol in selected)
    reason_suffix = "matched task keywords" if has_keyword_match else "structural symbol extraction"

    symbols = [
        {
            "name": symbol.name,
            "symbol_type": symbol.symbol_type,
            "path": file_path,
            "start_line": symbol.start + 1,
            "end_line": symbol.end + 1,
            "exported": symbol.exported,
        }
        for symbol in selected
    ]

    selected_ranges = [
        {
            "start_line": symbol.start + 1,
            "end_line": symbol.end + 1,
            "kind": symbol.symbol_type,
            "symbol": symbol.name,
        }
        for symbol in selected
    ]

    return SymbolExtraction(
        chunk_strategy=f"symbol-extract-{language}",
        chunk_reason=f"symbol-aware {language} extraction ({reason_suffix})",
        selected_ranges=selected_ranges,
        symbols=symbols,
        text=_render_selected_symbols(lines, selected, language),
    )
