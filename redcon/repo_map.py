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
from redcon.page_rank import page_rank
from redcon.symbols import (
    Signature,
    detect_language,
    extract_imports,
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


_DEFAULT_PAGERANK_WEIGHT = 0.4


def build_repo_map(
    task: str,
    *,
    repo: str | Path = ".",
    budget: int = 8_000,
    top_files: int = 60,
    config_path: str | None = None,
    pagerank_weight: float = _DEFAULT_PAGERANK_WEIGHT,
) -> RepoMap:
    """
    Build a token-budgeted repo map for ``task``.

    Algorithm:
      1. Rank files by relevance via ``RedconEngine.plan``.
      2. If the symbols extra is installed, build an import graph by
         extracting imports per file and run PageRank with a personalisation
         vector skewed toward the engine's top files. The two scores are
         blended (default ``pagerank_weight=0.4``); files form is then
         re-sorted by the blended score.
      3. For each top file (in descending blended rank), extract signatures.
      4. Greedily fit files into the token budget. Stop once the next
         file would exceed it.
      5. Render the result as text suitable for direct LLM consumption.

    PageRank pays for itself by promoting structural hubs (e.g. a config
    module that everything imports) on broad tasks, while task-keyword
    matches still anchor the personalisation vector.
    """
    repo_path = Path(repo).resolve()
    ranked = _rank_files(task, repo_path, top_files=top_files, config_path=config_path)
    have_symbols = symbols_available()

    if have_symbols and pagerank_weight > 0.0 and len(ranked) > 1:
        ranked = _blend_with_pagerank(repo_path, ranked, pagerank_weight)

    rendered: list[FileMap] = []
    used = 0
    truncated = False

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


def _blend_with_pagerank(
    repo: Path,
    ranked: list,
    pagerank_weight: float,
) -> list:
    """Build an import graph over the ranked files, run PageRank, and
    return ranked re-sorted by the blended score.

    The personalisation vector is taken from the engine score so the
    surfer is biased toward files the engine already thinks are
    relevant. Non-keyword hubs that those files import then bubble up.
    """
    paths = [entry.get("path", "") for entry in ranked if entry.get("path")]
    edges = _build_import_graph(repo, paths)
    if not edges:
        return ranked
    # Personalisation: normalised engine scores.
    pers_total = sum(max(0.0, float(e.get("score", 0.0))) for e in ranked)
    if pers_total <= 0.0:
        pers = None
    else:
        pers = {
            e.get("path", ""): max(0.0, float(e.get("score", 0.0))) / pers_total
            for e in ranked
            if e.get("path")
        }
    pr_scores = page_rank(paths, edges, personalisation=pers)

    # Blend: convex combination of normalised engine score and PR score.
    engine_max = max(
        (float(e.get("score", 0.0)) for e in ranked), default=1.0
    ) or 1.0
    blended: list[dict] = []
    for entry in ranked:
        path = entry.get("path", "")
        engine_norm = float(entry.get("score", 0.0)) / engine_max
        pr = pr_scores.get(path, 0.0)
        new_score = (1.0 - pagerank_weight) * engine_norm + pagerank_weight * pr
        # Preserve other fields the engine returned.
        merged = dict(entry)
        merged["score"] = new_score
        merged["pagerank"] = pr
        merged["engine_score_normalised"] = engine_norm
        blended.append(merged)

    blended.sort(key=lambda e: -float(e.get("score", 0.0)))
    return blended


def _build_import_graph(repo: Path, paths: list[str]) -> dict[str, list[str]]:
    """Map ``rel_path -> [rel_path of each imported in-repo file]``.

    Imports that don't resolve to a file in ``paths`` are dropped (they
    are dangling references handled correctly by page_rank).
    """
    if not paths:
        return {}
    by_basename: dict[str, list[str]] = {}
    for p in paths:
        base = p.rsplit("/", 1)[-1]
        for stem in (
            base,
            base.rsplit(".", 1)[0] if "." in base else base,
        ):
            by_basename.setdefault(stem, []).append(p)

    edges: dict[str, list[str]] = {p: [] for p in paths}
    for path in paths:
        full_path = repo / path
        if not full_path.is_file():
            continue
        if full_path.stat().st_size > 5 * 1024 * 1024:
            continue
        language = detect_language(full_path)
        if language is None:
            continue
        try:
            source = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        imports = extract_imports(source, language=language)
        for imp in imports:
            target = _resolve_import(imp, by_basename)
            if target and target != path:
                edges[path].append(target)
    return edges


def _resolve_import(imp: str, by_basename: dict[str, list[str]]) -> str | None:
    """Map a parsed import name to a known repo path, or None.

    The matching is intentionally loose - we look up the trailing module
    component against repo basenames. Cross-language matches that
    coincidentally share a basename are unlikely (and acceptable noise).
    """
    if not imp:
        return None
    # For dotted / relative imports, take the trailing component.
    last = imp.replace("/", ".").split(".")[-1].strip("'\"")
    # Direct hit (with extension stripped).
    candidates = by_basename.get(last) or []
    if not candidates:
        return None
    # Deterministic: shortest (most specific) path wins on ties.
    candidates = sorted(candidates, key=len)
    return candidates[0]


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
