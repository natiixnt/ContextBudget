from __future__ import annotations

"""Deterministic language-aware chunk selection for code files."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable


PY_DEF_RE = re.compile(r"^(async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
PY_CLASS_RE = re.compile(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)\b")
PY_ALL_RE = re.compile(r"^__all__\s*=")

TS_FUNC_RE = re.compile(r"^(export\s+)?(async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
TS_CLASS_RE = re.compile(r"^(export\s+)?class\s+([A-Za-z_$][\w$]*)\b")
TS_ARROW_RE = re.compile(
    r"^(export\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
TS_EXPORT_RE = re.compile(r"^export\s+(default\s+)?(const|let|var|function|class|interface|type|\{)")

GO_FUNC_RE = re.compile(r"^func\s+(\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
GO_TYPE_RE = re.compile(r"^type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\b")
GO_VAR_CONST_RE = re.compile(r"^(var|const)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


@dataclass(slots=True)
class ChunkSelection:
    """Selected code chunks for one file."""

    chunk_strategy: str
    chunk_reason: str
    selected_ranges: list[dict[str, int | str]]
    text: str


@dataclass(slots=True)
class _Candidate:
    start: int
    end: int
    kind: str
    score: float
    has_keyword: bool


_KIND_WEIGHTS = {
    "import": 1.0,
    "function": 2.0,
    "class": 1.9,
    "type": 1.8,
    "export": 2.2,
}


def _indent_level(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _is_js_comment(stripped: str) -> bool:
    return (
        stripped.startswith("//")
        or stripped.startswith("/*")
        or stripped.startswith("*")
        or stripped.endswith("*/")
    )


def _is_go_comment(stripped: str) -> bool:
    return stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*")


def _include_leading_comments(lines: list[str], start: int, comment_fn: Callable[[str], bool]) -> int:
    i = start - 1
    while i >= 0:
        stripped = lines[i].strip()
        if not stripped:
            if i == start - 1:
                i -= 1
                continue
            break
        if comment_fn(stripped):
            i -= 1
            continue
        break
    return i + 1


def _expand_python_block(lines: list[str], start: int) -> int:
    base_indent = _indent_level(lines[start])
    end = start
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        indent = _indent_level(lines[i])
        if indent <= base_indent and not stripped.startswith(("#", "@")):
            break
        end = i
    if end == start:
        end = min(len(lines) - 1, start + 12)
    return end


def _brace_delta(line: str) -> int:
    return line.count("{") - line.count("}")


def _expand_brace_block(lines: list[str], start: int, max_lines: int = 200) -> int:
    end = start
    balance = _brace_delta(lines[start])
    saw_open = "{" in lines[start]

    for i in range(start + 1, min(len(lines), start + max_lines + 1)):
        line = lines[i]
        if saw_open:
            balance += _brace_delta(line)
            end = i
            if balance <= 0:
                break
        else:
            end = i
            if "{" in line:
                saw_open = True
                balance += _brace_delta(line)
                if balance <= 0:
                    break
            elif i - start >= 8:
                break

    if end == start and not saw_open:
        end = min(len(lines) - 1, start + 8)
    return end


def _candidate_score(kind: str, header: str, keywords: list[str]) -> tuple[float, bool]:
    base = _KIND_WEIGHTS.get(kind, 1.0)
    lower = header.lower()
    hits = sum(1 for keyword in keywords if keyword and keyword in lower)
    return base + (2.0 * hits), hits > 0


def _make_candidate(start: int, end: int, kind: str, header: str, keywords: list[str]) -> _Candidate:
    score, has_keyword = _candidate_score(kind, header, keywords)
    return _Candidate(start=start, end=end, kind=kind, score=score, has_keyword=has_keyword)


def _python_candidates(lines: list[str], keywords: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith(("import ", "from ")):
            start = _include_leading_comments(lines, idx, lambda s: s.startswith("#"))
            candidates.append(_make_candidate(start, idx, "import", stripped, keywords))
            continue

        if PY_ALL_RE.match(stripped):
            start = _include_leading_comments(lines, idx, lambda s: s.startswith("#"))
            candidates.append(_make_candidate(start, idx, "export", stripped, keywords))
            continue

        if PY_DEF_RE.match(stripped):
            start = idx
            while start > 0 and lines[start - 1].lstrip().startswith("@"):
                start -= 1
            start = _include_leading_comments(lines, start, lambda s: s.startswith("#"))
            end = _expand_python_block(lines, idx)
            candidates.append(_make_candidate(start, end, "function", stripped, keywords))
            continue

        if PY_CLASS_RE.match(stripped):
            start = _include_leading_comments(lines, idx, lambda s: s.startswith("#"))
            end = _expand_python_block(lines, idx)
            candidates.append(_make_candidate(start, end, "class", stripped, keywords))

    return candidates


def _ts_js_candidates(lines: list[str], keywords: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("import ") or "require(" in stripped:
            start = _include_leading_comments(lines, idx, _is_js_comment)
            end = idx
            if not stripped.endswith(";"):
                end = min(len(lines) - 1, idx + 5)
                for j in range(idx, min(len(lines), idx + 6)):
                    if lines[j].strip().endswith(";"):
                        end = j
                        break
            candidates.append(_make_candidate(start, end, "import", stripped, keywords))
            continue

        if TS_FUNC_RE.match(stripped):
            start = _include_leading_comments(lines, idx, _is_js_comment)
            end = _expand_brace_block(lines, idx)
            kind = "export" if stripped.startswith("export ") else "function"
            candidates.append(_make_candidate(start, end, kind, stripped, keywords))
            continue

        if TS_CLASS_RE.match(stripped):
            start = _include_leading_comments(lines, idx, _is_js_comment)
            end = _expand_brace_block(lines, idx)
            kind = "export" if stripped.startswith("export ") else "class"
            candidates.append(_make_candidate(start, end, kind, stripped, keywords))
            continue

        if TS_ARROW_RE.match(stripped):
            start = _include_leading_comments(lines, idx, _is_js_comment)
            end = _expand_brace_block(lines, idx)
            kind = "export" if stripped.startswith("export ") else "function"
            candidates.append(_make_candidate(start, end, kind, stripped, keywords))
            continue

        if TS_EXPORT_RE.match(stripped):
            start = _include_leading_comments(lines, idx, _is_js_comment)
            end = idx
            candidates.append(_make_candidate(start, end, "export", stripped, keywords))

    return candidates


def _go_candidates(lines: list[str], keywords: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("import "):
            start = _include_leading_comments(lines, idx, _is_go_comment)
            if stripped == "import (":
                end = idx
                for j in range(idx + 1, len(lines)):
                    end = j
                    if lines[j].strip() == ")":
                        break
            else:
                end = idx
            candidates.append(_make_candidate(start, end, "import", stripped, keywords))
            continue

        func_match = GO_FUNC_RE.match(stripped)
        if func_match:
            name = func_match.group(2)
            start = _include_leading_comments(lines, idx, _is_go_comment)
            end = _expand_brace_block(lines, idx)
            kind = "export" if name and name[:1].isupper() else "function"
            candidates.append(_make_candidate(start, end, kind, stripped, keywords))
            continue

        type_match = GO_TYPE_RE.match(stripped)
        if type_match:
            name = type_match.group(1)
            start = _include_leading_comments(lines, idx, _is_go_comment)
            end = _expand_brace_block(lines, idx)
            kind = "export" if name and name[:1].isupper() else "type"
            candidates.append(_make_candidate(start, end, kind, stripped, keywords))
            continue

        var_match = GO_VAR_CONST_RE.match(stripped)
        if var_match:
            name = var_match.group(2)
            start = _include_leading_comments(lines, idx, _is_go_comment)
            kind = "export" if name and name[:1].isupper() else "type"
            candidates.append(_make_candidate(start, idx, kind, stripped, keywords))

    return candidates


def _overlaps(a: _Candidate, b: _Candidate) -> bool:
    return not (a.end < b.start or b.end < a.start)


def _select_candidates(candidates: list[_Candidate], line_budget: int) -> list[_Candidate]:
    if not candidates:
        return []

    budget = max(1, line_budget)
    selected: list[_Candidate] = []
    used = 0

    imports = sorted([candidate for candidate in candidates if candidate.kind == "import"], key=lambda item: item.start)
    if imports:
        first_import = imports[0]
        length = first_import.end - first_import.start + 1
        if length <= budget:
            selected.append(first_import)
            used += length

    ordered = sorted(candidates, key=lambda item: (-item.score, item.start, item.end))
    for candidate in ordered:
        if any(_overlaps(candidate, existing) for existing in selected):
            continue

        length = candidate.end - candidate.start + 1
        if used + length > budget:
            remaining = budget - used
            if remaining < 4:
                continue
            candidate = _Candidate(
                start=candidate.start,
                end=candidate.start + remaining - 1,
                kind=candidate.kind,
                score=candidate.score,
                has_keyword=candidate.has_keyword,
            )
            length = remaining

        selected.append(candidate)
        used += length
        if used >= budget:
            break

    if not selected:
        best = ordered[0]
        capped_end = min(best.end, best.start + budget - 1)
        selected.append(
            _Candidate(
                start=best.start,
                end=capped_end,
                kind=best.kind,
                score=best.score,
                has_keyword=best.has_keyword,
            )
        )

    selected.sort(key=lambda item: item.start)
    return selected


def _render_selected_chunks(lines: list[str], selected: list[_Candidate]) -> str:
    parts: list[str] = []
    for item in selected:
        header = f"## {item.kind} lines {item.start + 1}-{item.end + 1}"
        body = "\n".join(lines[item.start : item.end + 1])
        parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)


def select_language_aware_chunks(
    file_path: str,
    text: str,
    keywords: list[str],
    line_budget: int,
) -> ChunkSelection | None:
    """Select deterministic language-aware chunks for supported source files."""

    extension = Path(file_path).suffix.lower()
    lines = text.splitlines()
    if not lines:
        return None

    language = None
    candidates: list[_Candidate] = []
    if extension == ".py":
        language = "python"
        candidates = _python_candidates(lines, keywords)
    elif extension in {".ts", ".tsx"}:
        language = "typescript"
        candidates = _ts_js_candidates(lines, keywords)
    elif extension in {".js", ".jsx", ".mjs", ".cjs"}:
        language = "javascript"
        candidates = _ts_js_candidates(lines, keywords)
    elif extension == ".go":
        language = "go"
        candidates = _go_candidates(lines, keywords)

    if language is None or not candidates:
        return None

    selected = _select_candidates(candidates, line_budget=line_budget)
    if not selected:
        return None

    has_keyword_match = any(item.has_keyword for item in selected)
    reason_suffix = "matched task keywords" if has_keyword_match else "structural symbol extraction"
    chunk_reason = f"language-aware {language} chunking ({reason_suffix})"

    return ChunkSelection(
        chunk_strategy=f"language-aware-{language}",
        chunk_reason=chunk_reason,
        selected_ranges=[
            {
                "start_line": item.start + 1,
                "end_line": item.end + 1,
                "kind": item.kind,
            }
            for item in selected
        ],
        text=_render_selected_chunks(lines, selected),
    )
