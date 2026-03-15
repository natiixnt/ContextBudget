from __future__ import annotations

"""Deterministic language-aware context slicing for supported code files."""

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
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

_PATH_SPLIT_RE = re.compile(r"[:/\\]+")


@dataclass(frozen=True, slots=True)
class SliceRelationshipContext:
    """File-level relationship hints used to bias slice selection."""

    outgoing_related_paths: tuple[str, ...] = ()
    incoming_related_paths: tuple[str, ...] = ()
    incoming_entrypoint_paths: tuple[str, ...] = ()


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
    header: str
    symbol: str = ""
    score: float = 0.0
    reasons: tuple[str, ...] = field(default_factory=tuple)


_KIND_WEIGHTS = {
    "import": 0.45,
    "function": 1.85,
    "class": 1.75,
    "type": 1.65,
    "export": 2.0,
}

_SYMBOL_KINDS = {"function", "class", "type", "export"}


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


def _with_context_bounds(start: int, end: int, *, surrounding_lines: int, max_line_index: int) -> tuple[int, int]:
    return max(0, start - surrounding_lines), min(max_line_index, end + surrounding_lines)


def _make_candidate(
    start: int,
    end: int,
    kind: str,
    header: str,
    *,
    symbol: str = "",
) -> _Candidate:
    return _Candidate(
        start=start,
        end=end,
        kind=kind,
        header=header,
        symbol=symbol,
    )


def _python_candidates(lines: list[str], *, surrounding_lines: int) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    max_line_index = len(lines) - 1

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith(("import ", "from ")):
            start = _include_leading_comments(lines, idx, lambda s: s.startswith("#"))
            start, end = _with_context_bounds(start, idx, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            candidates.append(_make_candidate(start, end, "import", stripped))
            continue

        if PY_ALL_RE.match(stripped):
            start = _include_leading_comments(lines, idx, lambda s: s.startswith("#"))
            start, end = _with_context_bounds(start, idx, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            candidates.append(_make_candidate(start, end, "export", stripped, symbol="__all__"))
            continue

        def_match = PY_DEF_RE.match(stripped)
        if def_match:
            start = idx
            while start > 0 and lines[start - 1].lstrip().startswith("@"):
                start -= 1
            start = _include_leading_comments(lines, start, lambda s: s.startswith("#"))
            end = _expand_python_block(lines, idx)
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            candidates.append(_make_candidate(start, end, "function", stripped, symbol=def_match.group(2)))
            continue

        class_match = PY_CLASS_RE.match(stripped)
        if class_match:
            start = _include_leading_comments(lines, idx, lambda s: s.startswith("#"))
            end = _expand_python_block(lines, idx)
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            candidates.append(_make_candidate(start, end, "class", stripped, symbol=class_match.group(1)))

    return candidates


def _ts_js_candidates(lines: list[str], *, surrounding_lines: int) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    max_line_index = len(lines) - 1

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
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            candidates.append(_make_candidate(start, end, "import", stripped))
            continue

        func_match = TS_FUNC_RE.match(stripped)
        if func_match:
            start = _include_leading_comments(lines, idx, _is_js_comment)
            end = _expand_brace_block(lines, idx)
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            kind = "export" if stripped.startswith("export ") else "function"
            candidates.append(_make_candidate(start, end, kind, stripped, symbol=func_match.group(3)))
            continue

        class_match = TS_CLASS_RE.match(stripped)
        if class_match:
            start = _include_leading_comments(lines, idx, _is_js_comment)
            end = _expand_brace_block(lines, idx)
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            kind = "export" if stripped.startswith("export ") else "class"
            candidates.append(_make_candidate(start, end, kind, stripped, symbol=class_match.group(2)))
            continue

        arrow_match = TS_ARROW_RE.match(stripped)
        if arrow_match:
            start = _include_leading_comments(lines, idx, _is_js_comment)
            end = _expand_brace_block(lines, idx)
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            kind = "export" if stripped.startswith("export ") else "function"
            candidates.append(_make_candidate(start, end, kind, stripped, symbol=arrow_match.group(3)))
            continue

        if TS_EXPORT_RE.match(stripped):
            start = _include_leading_comments(lines, idx, _is_js_comment)
            start, end = _with_context_bounds(start, idx, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            candidates.append(_make_candidate(start, end, "export", stripped))

    return candidates


def _go_candidates(lines: list[str], *, surrounding_lines: int) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    max_line_index = len(lines) - 1

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
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            candidates.append(_make_candidate(start, end, "import", stripped))
            continue

        func_match = GO_FUNC_RE.match(stripped)
        if func_match:
            name = func_match.group(2)
            start = _include_leading_comments(lines, idx, _is_go_comment)
            end = _expand_brace_block(lines, idx)
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            kind = "export" if name and name[:1].isupper() else "function"
            candidates.append(_make_candidate(start, end, kind, stripped, symbol=name))
            continue

        type_match = GO_TYPE_RE.match(stripped)
        if type_match:
            name = type_match.group(1)
            start = _include_leading_comments(lines, idx, _is_go_comment)
            end = _expand_brace_block(lines, idx)
            start, end = _with_context_bounds(start, end, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            kind = "export" if name and name[:1].isupper() else "type"
            candidates.append(_make_candidate(start, end, kind, stripped, symbol=name))
            continue

        var_match = GO_VAR_CONST_RE.match(stripped)
        if var_match:
            name = var_match.group(2)
            start = _include_leading_comments(lines, idx, _is_go_comment)
            start, end = _with_context_bounds(start, idx, surrounding_lines=surrounding_lines, max_line_index=max_line_index)
            kind = "export" if name and name[:1].isupper() else "type"
            candidates.append(_make_candidate(start, end, kind, stripped, symbol=name))

    return candidates


def _overlaps(a: _Candidate, b: _Candidate) -> bool:
    return not (a.end < b.start or b.end < a.start)


def _candidate_text(lines: list[str], candidate: _Candidate) -> str:
    return "\n".join(lines[candidate.start : candidate.end + 1])


def _keyword_hits_for_candidate(lines: list[str], candidate: _Candidate, keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    lower_text = _candidate_text(lines, candidate).lower()
    return [keyword for keyword in keywords if keyword and keyword in lower_text]


def _path_hints(path: str) -> set[str]:
    lowered = path.lower()
    hints: set[str] = set()

    pure = PurePosixPath(lowered.replace(":", "/"))
    stem = pure.stem
    if stem and stem != "__init__":
        hints.add(stem)
    parent_name = pure.parent.name
    if stem and parent_name and parent_name not in {"src", "lib", "app", "tests"}:
        hints.add(f"{parent_name}.{stem}")

    parts = [part for part in _PATH_SPLIT_RE.split(lowered) if part and part not in {"src", "lib", "app"}]
    hints.update(part.rsplit(".", 1)[0] for part in parts if "." in part)
    hints.update(part for part in parts if "." not in part and len(part) > 2)
    return {hint for hint in hints if len(hint) > 2}


def _match_related_paths(header: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    lowered = header.lower()
    matches: list[str] = []
    for path in paths:
        hints = _path_hints(path)
        if any(re.search(rf"(?<![a-z0-9_]){re.escape(hint)}(?![a-z0-9_])", lowered) for hint in hints):
            matches.append(path)
    return tuple(sorted(set(matches)))


def _format_related_paths(paths: tuple[str, ...]) -> str:
    if not paths:
        return ""
    if len(paths) == 1:
        return paths[0]
    return f"{paths[0]} (+{len(paths) - 1} more)"


def _score_candidate(
    lines: list[str],
    candidate: _Candidate,
    keywords: list[str],
    relationship_context: SliceRelationshipContext,
) -> _Candidate:
    score = _KIND_WEIGHTS.get(candidate.kind, 1.0)
    reasons: list[str] = []

    if candidate.symbol:
        reasons.append(f"symbol extraction: {candidate.kind} {candidate.symbol}")
    else:
        reasons.append(f"symbol extraction: {candidate.kind}")

    keyword_hits = _keyword_hits_for_candidate(lines, candidate, keywords)
    if keyword_hits:
        score += 2.1 * len(keyword_hits)
        reasons.append(f"keyword proximity: {', '.join(keyword_hits[:3])}")

    if candidate.kind == "import":
        matched_paths = _match_related_paths(candidate.header, relationship_context.outgoing_related_paths)
        if matched_paths:
            score += 2.4 + min(0.6, 0.2 * (len(matched_paths) - 1))
            reasons.append(f"import relationship: {_format_related_paths(matched_paths)}")
        elif not keyword_hits:
            score -= 0.35
    elif candidate.kind in _SYMBOL_KINDS:
        if relationship_context.incoming_related_paths:
            score += 0.9 if candidate.kind == "export" else 0.7
            reasons.append(
                f"imported by related file: {_format_related_paths(relationship_context.incoming_related_paths)}"
            )
        if relationship_context.incoming_entrypoint_paths:
            score += 0.35
            reasons.append(
                f"reachable from entrypoint: {_format_related_paths(relationship_context.incoming_entrypoint_paths)}"
            )

    return _Candidate(
        start=candidate.start,
        end=candidate.end,
        kind=candidate.kind,
        header=candidate.header,
        symbol=candidate.symbol,
        score=score,
        reasons=tuple(reasons),
    )


def _select_candidates(candidates: list[_Candidate], line_budget: int) -> list[_Candidate]:
    if not candidates:
        return []

    budget = max(1, line_budget)
    selected: list[_Candidate] = []
    used = 0

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
                header=candidate.header,
                symbol=candidate.symbol,
                score=candidate.score,
                reasons=candidate.reasons,
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
                header=best.header,
                symbol=best.symbol,
                score=best.score,
                reasons=best.reasons,
            )
        )

    selected.sort(key=lambda item: item.start)
    return selected


def _render_selected_chunks(lines: list[str], selected: list[_Candidate]) -> str:
    parts: list[str] = []
    for item in selected:
        body = "\n".join(lines[item.start : item.end + 1])
        parts.append(body)
    return "\n\n...\n\n".join(parts)


def _chunk_reason(language: str, selected: list[_Candidate]) -> str:
    signals = ["symbol extraction"]
    if any(any(reason.startswith("keyword proximity:") for reason in item.reasons) for item in selected):
        signals.append("keyword proximity")
    if any(
        any(reason.startswith("import relationship:") or reason.startswith("imported by related file:") for reason in item.reasons)
        for item in selected
    ):
        signals.append("import relationships")
    return f"language-aware {language} slicing ({', '.join(signals)})"


def select_language_aware_chunks(
    file_path: str,
    text: str,
    keywords: list[str],
    line_budget: int,
    *,
    relationship_context: SliceRelationshipContext | None = None,
    surrounding_lines: int = 0,
) -> ChunkSelection | None:
    """Select deterministic language-aware chunks for supported source files."""

    extension = Path(file_path).suffix.lower()
    lines = text.splitlines()
    if not lines:
        return None

    relationship_hints = relationship_context if relationship_context is not None else SliceRelationshipContext()

    language = None
    candidates: list[_Candidate] = []
    if extension == ".py":
        language = "python"
        candidates = _python_candidates(lines, surrounding_lines=surrounding_lines)
    elif extension in {".ts", ".tsx"}:
        language = "typescript"
        candidates = _ts_js_candidates(lines, surrounding_lines=surrounding_lines)
    elif extension in {".js", ".jsx", ".mjs", ".cjs"}:
        language = "javascript"
        candidates = _ts_js_candidates(lines, surrounding_lines=surrounding_lines)
    elif extension == ".go":
        language = "go"
        candidates = _go_candidates(lines, surrounding_lines=surrounding_lines)

    if language is None or not candidates:
        return None

    scored = [_score_candidate(lines, candidate, keywords, relationship_hints) for candidate in candidates]
    selected = _select_candidates(scored, line_budget=line_budget)
    if not selected:
        return None

    return ChunkSelection(
        chunk_strategy=f"language-aware-{language}",
        chunk_reason=_chunk_reason(language, selected),
        selected_ranges=[
            {
                "start_line": item.start + 1,
                "end_line": item.end + 1,
                "kind": item.kind,
                "reason": "; ".join(item.reasons),
                **({"symbol": item.symbol} if item.symbol else {}),
            }
            for item in selected
        ],
        text=_render_selected_chunks(lines, selected),
    )
