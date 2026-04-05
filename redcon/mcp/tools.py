"""
MCP tool handlers - wrap RedconEngine for agent-facing tool calls.

Each tool returns structured data that agents can interpret. Tools share
a rank cache so repeated calls for the same task don't re-scan the repo.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from redcon.engine import RedconEngine

logger = logging.getLogger(__name__)

# Cache ranks by (repo, task) key for 15 minutes so repeat calls are free.
_RANK_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 900.0  # 15 minutes


def _cache_key(repo: str, task: str) -> tuple[str, str]:
    return (str(Path(repo).resolve()), task.strip().lower())


def _get_cached_rank(repo: str, task: str) -> dict[str, Any] | None:
    key = _cache_key(repo, task)
    entry = _RANK_CACHE.get(key)
    if entry is None:
        return None
    timestamp, data = entry
    if time.monotonic() - timestamp > _CACHE_TTL:
        _RANK_CACHE.pop(key, None)
        return None
    return data


def _set_cached_rank(repo: str, task: str, data: dict[str, Any]) -> None:
    key = _cache_key(repo, task)
    _RANK_CACHE[key] = (time.monotonic(), data)


def clear_cache() -> None:
    """Clear the rank cache. Exposed for tests and cache invalidation."""
    _RANK_CACHE.clear()


def _make_engine(config_path: str | None = None) -> RedconEngine:
    return RedconEngine(config_path=config_path) if config_path else RedconEngine()


# --- Tool: rank ---

def tool_rank(
    task: str,
    repo: str = ".",
    top_k: int = 25,
    config_path: str | None = None,
) -> dict[str, Any]:
    """
    Rank files in the repository by relevance to the task.

    Returns the top-K ranked files with scores and explanation of why
    each file was ranked.
    """
    if not task or not task.strip():
        return {"error": "task must be a non-empty string"}
    if top_k <= 0:
        return {"error": "top_k must be positive"}

    cached = _get_cached_rank(repo, task)
    if cached is not None:
        logger.debug("mcp.rank: cache hit for repo=%s task=%s", repo, task)
        ranked = cached.get("ranked_files", [])[:top_k]
        return {
            "task": task,
            "repo": repo,
            "top_k": top_k,
            "from_cache": True,
            "files": _format_rank_entries(ranked),
        }

    engine = _make_engine(config_path)
    try:
        result = engine.plan(task=task, repo=repo, top_files=top_k)
    except Exception as e:
        logger.exception("mcp.rank: engine.plan failed")
        return {"error": f"ranking failed: {e}"}

    _set_cached_rank(repo, task, result)
    ranked = result.get("ranked_files", [])[:top_k]
    return {
        "task": task,
        "repo": repo,
        "top_k": top_k,
        "from_cache": False,
        "files": _format_rank_entries(ranked),
    }


def _format_rank_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for entry in entries:
        out.append({
            "path": entry.get("path", ""),
            "score": round(float(entry.get("score", 0.0)), 2),
            "reasons": entry.get("reasons", [])[:3],
            "line_count": entry.get("line_count", 0),
        })
    return out


# --- Tool: overview ---

def tool_overview(
    task: str,
    repo: str = ".",
    config_path: str | None = None,
) -> dict[str, Any]:
    """
    Return a lightweight repo map showing directories, key files,
    and their roles. Meant as a quick architectural orientation.
    """
    if not task or not task.strip():
        return {"error": "task must be a non-empty string"}

    cached = _get_cached_rank(repo, task)
    if cached is None:
        engine = _make_engine(config_path)
        try:
            cached = engine.plan(task=task, repo=repo, top_files=50)
        except Exception as e:
            logger.exception("mcp.overview: engine.plan failed")
            return {"error": f"overview failed: {e}"}
        _set_cached_rank(repo, task, cached)

    ranked = cached.get("ranked_files", [])[:50]

    # Group by directory
    dirs: dict[str, list[dict[str, Any]]] = {}
    for entry in ranked:
        path = entry.get("path", "")
        parts = path.replace("\\", "/").split("/")
        directory = "/".join(parts[:-1]) if len(parts) > 1 else "."
        dirs.setdefault(directory, []).append({
            "name": parts[-1],
            "score": round(float(entry.get("score", 0.0)), 2),
        })

    # Sort directories by max file score
    dir_summary = []
    for directory, files in dirs.items():
        files_sorted = sorted(files, key=lambda f: f["score"], reverse=True)
        top_file = files_sorted[0] if files_sorted else None
        dir_summary.append({
            "directory": directory,
            "file_count": len(files_sorted),
            "top_file": top_file["name"] if top_file else None,
            "top_score": top_file["score"] if top_file else 0.0,
            "files": [f["name"] for f in files_sorted[:5]],
        })
    dir_summary.sort(key=lambda d: d["top_score"], reverse=True)

    return {
        "task": task,
        "repo": repo,
        "total_ranked": len(ranked),
        "modules": dir_summary[:15],
    }


# --- Tool: compress ---

def tool_compress(
    path: str,
    task: str,
    repo: str = ".",
    max_tokens: int = 2000,
    config_path: str | None = None,
) -> dict[str, Any]:
    """
    Return compressed version of a file scoped to the task.

    Uses the pack pipeline on a single-file budget. Agent can inspect
    multiple files cheaply without reading full contents.
    """
    if not path or not path.strip():
        return {"error": "path must be a non-empty string"}
    if not task or not task.strip():
        return {"error": "task must be a non-empty string"}
    if max_tokens <= 0:
        return {"error": "max_tokens must be positive"}

    engine = _make_engine(config_path)
    try:
        result = engine.pack(
            task=task,
            repo=repo,
            max_tokens=max_tokens,
            top_files=1,
        )
    except Exception as e:
        logger.exception("mcp.compress: engine.pack failed")
        return {"error": f"compression failed: {e}"}

    compressed = result.get("compressed_context", [])
    # Find the matching file (pack may not pick exactly what we asked)
    match = None
    for item in compressed:
        item_path = item.get("path", "").replace("\\", "/")
        target = path.replace("\\", "/")
        if item_path.endswith(target) or target.endswith(item_path):
            match = item
            break

    if match is None:
        return {
            "path": path,
            "error": "file not found in ranked results for this task",
            "available": [c.get("path", "") for c in compressed[:5]],
        }

    return {
        "path": match.get("path", path),
        "strategy": match.get("strategy", "unknown"),
        "original_tokens": match.get("original_tokens", 0),
        "compressed_tokens": match.get("compressed_tokens", 0),
        "content": match.get("text", match.get("content", "")),
    }


# --- Tool: search ---

def tool_search(
    pattern: str,
    task: str,
    repo: str = ".",
    scope: str = "ranked",
    top_k: int = 25,
    max_results: int = 50,
    config_path: str | None = None,
) -> dict[str, Any]:
    """
    Grep for pattern within ranked files (scope='ranked') or all files
    (scope='all'). Returns line matches with paths and line numbers.
    """
    if not pattern or not pattern.strip():
        return {"error": "pattern must be a non-empty string"}
    if scope not in ("ranked", "all"):
        return {"error": "scope must be 'ranked' or 'all'"}

    import re
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {"error": f"invalid regex pattern: {e}"}

    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        return {"error": f"repo is not a directory: {repo}"}

    # Determine search scope
    if scope == "ranked":
        cached = _get_cached_rank(repo, task)
        if cached is None:
            engine = _make_engine(config_path)
            try:
                cached = engine.plan(task=task, repo=repo, top_files=top_k)
            except Exception as e:
                logger.exception("mcp.search: engine.plan failed")
                return {"error": f"ranking failed: {e}"}
            _set_cached_rank(repo, task, cached)
        ranked = cached.get("ranked_files", [])[:top_k]
        paths = [repo_path / entry.get("path", "") for entry in ranked]
    else:
        # Walk repo, skip common ignore directories
        paths = []
        ignore_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__",
                       "dist", "build", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
        for p in repo_path.rglob("*"):
            if p.is_file() and not any(part in ignore_dirs for part in p.parts):
                paths.append(p)

    matches = []
    for file_path in paths:
        if len(matches) >= max_results:
            break
        try:
            if not file_path.is_file():
                continue
            if file_path.stat().st_size > 5 * 1024 * 1024:
                continue  # skip files > 5MB
            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, start=1):
                    if regex.search(line):
                        try:
                            rel = str(file_path.relative_to(repo_path)).replace("\\", "/")
                        except ValueError:
                            rel = str(file_path)
                        matches.append({
                            "path": rel,
                            "line": line_num,
                            "text": line.rstrip()[:200],
                        })
                        if len(matches) >= max_results:
                            break
        except (OSError, UnicodeDecodeError):
            continue

    return {
        "pattern": pattern,
        "scope": scope,
        "searched_files": len(paths),
        "match_count": len(matches),
        "matches": matches,
    }


# --- Tool: budget ---

def tool_budget(
    files: list[str],
    task: str,
    max_tokens: int,
    repo: str = ".",
    config_path: str | None = None,
) -> dict[str, Any]:
    """
    Plan how to fit the requested files within the token budget,
    selecting compression strategies per file.
    """
    if not files:
        return {"error": "files list cannot be empty"}
    if not task or not task.strip():
        return {"error": "task must be a non-empty string"}
    if max_tokens <= 0:
        return {"error": "max_tokens must be positive"}

    engine = _make_engine(config_path)
    # Run pack with the user's max_tokens and enough top_files to cover the request
    try:
        result = engine.pack(
            task=task,
            repo=repo,
            max_tokens=max_tokens,
            top_files=max(len(files) * 2, 25),
        )
    except Exception as e:
        logger.exception("mcp.budget: engine.pack failed")
        return {"error": f"budget planning failed: {e}"}

    compressed = result.get("compressed_context", [])
    requested_set = {f.replace("\\", "/").strip("/") for f in files}

    plan = []
    matched_paths: set[str] = set()
    total_tokens = 0
    for item in compressed:
        item_path = item.get("path", "").replace("\\", "/").strip("/")
        # Match by suffix (handles relative/absolute mismatch)
        matched = False
        for req in requested_set:
            if item_path.endswith(req) or req.endswith(item_path):
                matched = True
                matched_paths.add(req)
                break
        if matched:
            ct = int(item.get("compressed_tokens", 0))
            plan.append({
                "path": item.get("path", ""),
                "strategy": item.get("strategy", "unknown"),
                "tokens": ct,
                "original_tokens": int(item.get("original_tokens", 0)),
            })
            total_tokens += ct

    dropped = sorted(requested_set - matched_paths)
    budget = result.get("budget", {})

    return {
        "task": task,
        "max_tokens": max_tokens,
        "plan": plan,
        "total_tokens": total_tokens,
        "dropped": dropped,
        "quality_risk": budget.get("quality_risk_estimate", "unknown"),
        "saved_tokens": budget.get("estimated_saved_tokens", 0),
    }
