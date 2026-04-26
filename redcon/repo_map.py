"""
Aider-style repository map.

Combines the existing relevance ranker (``RedconEngine.plan``) with
tree-sitter signature extraction (``redcon.symbols``) to render a
compact, signature-level view of the files most relevant to a task.
The result fits within a token budget that the caller controls.

Comparison with siblings:
- ``redcon plan`` returns a ranked file list with reasons (no code).
- ``redcon_overview`` returns a directory tree with relevance scores.
- ``redcon repo-map`` (this module) returns directory tree + per-file
  class/function signatures - grounded code structure the agent can
  reason about without reading full files.

Signatures come from ``redcon.symbols.extract_signatures`` when the
``redcon[symbols]`` extra is installed; otherwise the map gracefully
falls back to a path-only listing (still useful, just less grounded).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from redcon.cmd._tokens_lite import estimate_tokens
from redcon.symbols import (
    Signature,
    detect_language,
    extract_signatures,
    is_available as symbols_available,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileMap:
    path: str
    score: float
    signatures: tuple[Signature, ...]


@dataclass(frozen=True, slots=True)
class RepoMap:
    task: str
    repo: str
    files: tuple[FileMap, ...]
    total_tokens: int
    budget: int
    text: str
    symbols_available: bool
    truncated: bool


def build_repo_map(
    task: str,
    *,
    repo: str | Path = ".",
    budget: int = 8_000,
    top_files: int = 60,
    config_path: str | None = None,
) -> RepoMap:
    """
    Build a token-budgeted repo map for ``task``.

    Algorithm:
      1. Rank files by relevance via ``RedconEngine.plan``.
      2. For each top file (in descending rank), extract signatures.
      3. Greedily fit files into the token budget. Stop once the next
         file would exceed it.
      4. Render the result as text suitable for direct LLM consumption.
    """
    repo_path = Path(repo).resolve()
    ranked = _rank_files(task, repo_path, top_files=top_files, config_path=config_path)

    rendered: list[FileMap] = []
    used = 0
    truncated = False
    body_lines: list[str] = []
    have_symbols = symbols_available()

    for entry in ranked:
        rel_path = entry.get("path", "")
        if not rel_path:
            continue
        score = float(entry.get("score", 0.0))
        signatures = (
            _extract_for(repo_path, rel_path) if have_symbols else ()
        )
        block = _render_file(rel_path, signatures)
        block_tokens = estimate_tokens(block)
        if used + block_tokens > budget:
            truncated = True
            break
        rendered.append(
            FileMap(path=rel_path, score=score, signatures=signatures)
        )
        body_lines.append(block)
        used += block_tokens

    text = _render(task, repo_path, rendered, used, budget, have_symbols, truncated)

    return RepoMap(
        task=task,
        repo=str(repo_path),
        files=tuple(rendered),
        total_tokens=used,
        budget=budget,
        text=text,
        symbols_available=have_symbols,
        truncated=truncated,
    )


def _rank_files(
    task: str, repo: Path, *, top_files: int, config_path: str | None
):
    """Lazy-load the engine to keep cold start fast for callers that
    only import this module to introspect.
    """
    from redcon.engine import RedconEngine

    engine = (
        RedconEngine(config_path=config_path) if config_path else RedconEngine()
    )
    plan = engine.plan(task=task, repo=str(repo), top_files=top_files)
    return plan.get("ranked_files", [])


def _extract_for(repo: Path, rel_path: str) -> tuple[Signature, ...]:
    file_path = repo / rel_path
    if not file_path.is_file():
        return ()
    if file_path.stat().st_size > 5 * 1024 * 1024:
        return ()
    language = detect_language(file_path)
    if language is None:
        return ()
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    return tuple(extract_signatures(source, language=language))


def _render_file(rel_path: str, signatures: tuple[Signature, ...]) -> str:
    if not signatures:
        return rel_path
    lines = [rel_path]
    for sig in signatures:
        # Snippet may span multiple lines; collapse to first line for the
        # map view. Agents that want full bodies can fetch via redcon_compress.
        head = sig.snippet.splitlines()[0] if sig.snippet else ""
        kind = sig.kind
        line = sig.line
        if head:
            lines.append(f"L{line} {kind}: {head}")
        else:
            lines.append(f"L{line} {kind}: {sig.name}")
    return "\n".join(lines)


def _render(
    task: str,
    repo: Path,
    rendered: list[FileMap],
    used: int,
    budget: int,
    have_symbols: bool,
    truncated: bool,
) -> str:
    head = (
        f"repo-map task={task!r} repo={repo} "
        f"files={len(rendered)} tokens={used}/{budget}"
    )
    if not have_symbols:
        head += " (signatures unavailable: install redcon[symbols])"
    if truncated:
        head += " (truncated to fit budget)"

    body: list[str] = []
    for fm in rendered:
        body.append(_render_file(fm.path, fm.signatures))
    return head + "\n\n" + "\n\n".join(body)
