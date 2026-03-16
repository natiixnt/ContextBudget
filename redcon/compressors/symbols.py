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
# Intentionally loose - matches the start of any indented name followed by ( or <.
_TS_METHOD_RE = re.compile(
    r"^\s{2,}"
    r"(?:(?:public|private|protected|static|async|override|abstract|readonly)\s+)*"
    r"(?:get\s+|set\s+|async\s+)?"
    r"(?!(?:if|for|while|switch|return|const|let|var|new|throw|import|export)\b)"
    r"([A-Za-z_$][\w$]*)\s*[(<]"
)


def _strip_py_annotations(sig_line: str) -> str:
    """Strip type annotations from a single-line Python def signature.

    Only applied to single-line signatures (those containing a closing paren
    on the same line as ``def``).  Multi-line signatures are left unchanged.
    Returns the original line on any parse failure.
    """
    stripped = sig_line.strip()
    # Only handle single-line defs (closing paren present on the same line)
    if not (stripped.startswith(("def ", "async def ")) and ")" in stripped):
        return sig_line
    parse_src = stripped if stripped.endswith(":") else stripped + ":"
    try:
        tree = ast.parse(parse_src + "\n    pass", mode="exec")
    except SyntaxError:
        return sig_line
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = node.args
        params: list[str] = []
        # Positional-only args (before /)
        n_posonly = len(a.posonlyargs)
        n_regular = len(a.args)
        total_positional = n_posonly + n_regular
        total_defaults = len(a.defaults)
        default_start = total_positional - total_defaults
        for idx, arg in enumerate(a.posonlyargs):
            if idx >= default_start:
                params.append(f"{arg.arg}={ast.unparse(a.defaults[idx - default_start])}")
            else:
                params.append(arg.arg)
        if n_posonly:
            params.append("/")
        for idx, arg in enumerate(a.args):
            actual = n_posonly + idx
            if actual >= default_start:
                params.append(f"{arg.arg}={ast.unparse(a.defaults[actual - default_start])}")
            else:
                params.append(arg.arg)
        if a.vararg:
            params.append(f"*{a.vararg.arg}")
        elif a.kwonlyargs:
            params.append("*")
        for idx, arg in enumerate(a.kwonlyargs):
            kd = a.kw_defaults[idx]
            params.append(f"{arg.arg}={ast.unparse(kd)}" if kd is not None else arg.arg)
        if a.kwarg:
            params.append(f"**{a.kwarg.arg}")
        indent = " " * (len(sig_line) - len(sig_line.lstrip()))
        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        return indent + prefix + node.name + "(" + ", ".join(params) + "):"
    return sig_line


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of 2+ consecutive blank lines to a single blank line."""
    out: list[str] = []
    blanks = 0
    for line in text.splitlines():
        if not line.strip():
            blanks += 1
            if blanks <= 1:
                out.append(line)
        else:
            blanks = 0
            out.append(line)
    return "\n".join(out)


def _condense_decorators(body_lines: list[str]) -> list[str]:
    """Collapse multi-line Python decorators to their first line + ...)."""
    result: list[str] = []
    i = 0
    while i < len(body_lines):
        line = body_lines[i]
        stripped = line.strip()
        if stripped.startswith("@") and "(" in stripped:
            depth = stripped.count("(") - stripped.count(")")
            if depth > 0:
                j = i + 1
                while j < len(body_lines) and depth > 0:
                    depth += body_lines[j].count("(") - body_lines[j].count(")")
                    j += 1
                result.append(line.rstrip() + " ...)")
                i = j
                continue
        result.append(line)
        i += 1
    return result


def _strip_python_docstring(body_lines: list[str]) -> list[str]:
    """Remove the first docstring (triple-quoted string) from a Python body.

    Handles leading comment/decorator lines and multi-line signatures
    (e.g. ``def foo(\\n    arg,\\n) -> str:``) by scanning for the actual
    ``def``/``class`` header before searching for the docstring.
    """
    if len(body_lines) < 2:
        return body_lines

    # Find the def/class header (may be preceded by comments or decorators).
    header_idx = 0
    for idx, line in enumerate(body_lines):
        stripped = line.strip()
        if stripped.startswith(("def ", "async def ", "class ")):
            header_idx = idx
            break

    # Skip past a multi-line signature by tracking open paren depth.
    i = header_idx
    paren_depth = body_lines[i].count("(") - body_lines[i].count(")")
    i += 1
    while i < len(body_lines) and paren_depth > 0:
        paren_depth += body_lines[i].count("(") - body_lines[i].count(")")
        i += 1

    # Skip blank lines between signature end and body.
    while i < len(body_lines) and not body_lines[i].strip():
        i += 1
    if i >= len(body_lines):
        return body_lines

    first = body_lines[i].strip()
    for delim in ('"""', "'''"):
        if first.startswith(delim):
            rest = first[len(delim):]
            if rest.endswith(delim) and len(rest) >= len(delim):
                # Single-line docstring.
                return body_lines[:i] + body_lines[i + 1:]
            # Multi-line: scan for closing delimiter.
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


def _strip_leading_comments(body_lines: list[str], language: str) -> list[str]:
    """Strip leading doc-comments from a symbol body.

    For TypeScript/JavaScript: removes leading ``//`` lines and ``/** ... */``
    JSDoc blocks.  For Go: removes leading ``//`` comment lines.
    Returns *body_lines* unchanged for other languages.
    """
    if not body_lines:
        return body_lines

    i = 0
    if language in {"typescript", "javascript"}:
        while i < len(body_lines):
            stripped = body_lines[i].strip()
            if not stripped:
                i += 1
                continue
            if stripped.startswith("//"):
                i += 1
                continue
            if stripped.startswith("/*"):
                while i < len(body_lines) and "*/" not in body_lines[i]:
                    i += 1
                i += 1
                continue
            if stripped.startswith("*"):
                i += 1
                continue
            break
    elif language == "go":
        while i < len(body_lines):
            stripped = body_lines[i].strip()
            if not stripped or stripped.startswith("//"):
                i += 1
                continue
            break
    elif language == "python":
        # Skip leading # comments and decorator lines so stubs show the def/class line.
        while i < len(body_lines):
            stripped = body_lines[i].strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("@"):
                i += 1
                continue
            break

    return body_lines[i:] if i < len(body_lines) else body_lines


# Matches lines that open a multi-entry data structure, including:
#   assignments: ``x = {``, ``items = [``, ``task = Task(``, ``cfg = Config(``
#   bare brackets: ``(`` or ``[`` or ``{`` alone on an indented line (function args).
_DATA_OPEN_RE = re.compile(
    r"^\s*(?:[\w.]+\s*=\s*(?:[A-Za-z_][\w.]*\s*)?)?([{[(])\s*$"
)
_DATA_OPEN_MAX_ENTRIES = 7
_DATA_OPEN_KEEP = 3


def _collapse_multiline_py_signatures(body_lines: list[str]) -> list[str]:
    """Collapse multi-line Python ``def``/``class`` signatures to one line.

    Signatures split across lines (e.g. long parameter lists) are joined and
    their type annotations stripped via :func:`_strip_py_annotations`, turning
    9-line signatures into a single compact line.
    """
    result: list[str] = []
    i = 0
    while i < len(body_lines):
        line = body_lines[i]
        stripped = line.strip()
        if stripped.startswith(("def ", "async def ")) and "(" in stripped:
            depth = stripped.count("(") - stripped.count(")")
            if depth > 0:
                # Collect all lines of this multi-line signature.
                parts = [stripped]
                j = i + 1
                while j < len(body_lines) and depth > 0:
                    sl = body_lines[j].strip()
                    depth += sl.count("(") - sl.count(")")
                    parts.append(sl)
                    j += 1
                indent = " " * (len(line) - len(line.lstrip()))
                joined = " ".join(parts)
                # Normalise internal spacing.
                joined = re.sub(r"\(\s+", "(", joined)
                joined = re.sub(r"\s+\)", ")", joined)
                joined = re.sub(r",\s+", ", ", joined)
                result.append(_strip_py_annotations(indent + joined))
                i = j
                continue
        result.append(line)
        i += 1
    return result


def _truncate_data_blocks(body_lines: list[str]) -> list[str]:
    """Collapse large inline data structures to first few entries.

    Detects assignment patterns like ``x = {`` or ``DATA = [`` with more than
    ``_DATA_OPEN_MAX_ENTRIES`` interior lines and replaces the middle with a
    count comment, keeping only the first ``_DATA_OPEN_KEEP`` entries.
    """
    result: list[str] = []
    i = 0
    while i < len(body_lines):
        line = body_lines[i]
        m = _DATA_OPEN_RE.match(line)
        if m:
            open_ch = m.group(1)
            close_ch = {"[": "]", "{": "}", "(": ")"}[open_ch]
            j = i + 1
            depth = 1
            while j < len(body_lines) and depth > 0:
                for ch in body_lines[j]:
                    if ch == open_ch:
                        depth += 1
                    elif ch == close_ch:
                        depth -= 1
                        if depth == 0:
                            break
                j += 1
            # body_lines[i] = open, body_lines[i+1..j-2] = interior, body_lines[j-1] = close
            interior = (j - 1) - (i + 1)
            if interior > _DATA_OPEN_MAX_ENTRIES:
                result.append(line)
                result.extend(body_lines[i + 1 : i + 1 + _DATA_OPEN_KEEP])
                omitted = interior - _DATA_OPEN_KEEP
                inner = body_lines[i + 1] if i + 1 < j else ""
                indent = " " * (len(inner) - len(inner.lstrip())) if inner.strip() else "    "
                result.append(f"{indent}# ... ({omitted} more entries)")
                result.append(body_lines[j - 1])
                i = j
                continue
        result.append(line)
        i += 1
    return result


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
        if symbol.score < _STUB_SCORE_THRESHOLD and symbol.end > symbol.start:
            # Low keyword relevance - minimal header + signature only.
            header = f"## {symbol.name}"
            # Find actual declaration line by skipping leading comments/decorators.
            stub_lines = lines[symbol.start : symbol.end + 1]
            stub_lines = _strip_leading_comments(stub_lines, language)
            sig = stub_lines[0] if stub_lines else lines[symbol.start]
            if is_py:
                sig = _strip_py_annotations(sig)
            body = sig + " ..."
        else:
            export_marker = " exported" if symbol.exported else ""
            header = (
                f"## {symbol.symbol_type} {symbol.name}{export_marker} "
                f"lines {symbol.start + 1}-{symbol.end + 1}"
            )
            body_lines = lines[symbol.start : symbol.end + 1]
            if is_py:
                body_lines = _condense_decorators(body_lines)
                body_lines = _strip_python_docstring(body_lines)
            elif language in {"typescript", "javascript", "go"}:
                body_lines = _strip_leading_comments(body_lines, language)
            body_lines = _truncate_data_blocks(body_lines)
            if symbol.symbol_type == "class" and len(body_lines) > _MAX_CLASS_BODY_LINES:
                body = _condense_class_body(body_lines, _MAX_CLASS_BODY_LINES, method_re)
            elif symbol.symbol_type == "function" and len(body_lines) > _MAX_FUNC_BODY_LINES:
                body = _condense_func_body(body_lines, _MAX_FUNC_BODY_LINES)
            else:
                body = "\n".join(body_lines)
            # Collapse any multi-line Python signatures AFTER body condensation so the
            # size thresholds above still use the original line counts.
            if is_py:
                body = "\n".join(_collapse_multiline_py_signatures(body.splitlines()))
        parts.append(f"{header}\n{body}")
    return _collapse_blank_lines("\n\n".join(parts))


def select_symbol_aware_chunks(
    *,
    file_path: str,
    text: str,
    keywords: list[str],
    line_budget: int,
    max_symbols: int = 4,
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

    selected = _select_symbol_candidates(candidates, line_budget=line_budget, max_symbols=max_symbols)
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
