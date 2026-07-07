"""Tool adapters.

An adapter is a callable ``(task_description, repo_root, files) -> ranking``
where *files* is the selection universe (repo-relative paths) and the
returned ranking is an ordered subset of it, best candidates first. The
runner packs files in ranking order until the token budget is exhausted,
so adapters only decide the order, never the budget.

Adapters must not look at git state newer than the checked-out tree: the
runner hands them a worktree pinned to the task's parent commit.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable
from pathlib import Path

Ranking = list[str]
Adapter = Callable[[str, Path, list[str]], Ranking]

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "that",
    "this",
    "when",
    "add",
    "adds",
    "fix",
    "fixes",
    "make",
    "use",
    "uses",
    "via",
    "per",
    "all",
    "new",
    "now",
    "not",
    "are",
    "was",
}

_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{2,}")


def _read(root: Path, rel: str, cap: int = 200_000) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")[:cap]
    except OSError:
        return ""


def _keywords(task: str) -> list[str]:
    words = [w.lower() for w in _WORD.findall(task)]
    return [w for w in words if w not in _STOPWORDS]


# ---------------------------------------------------------------------------
# redcon
# ---------------------------------------------------------------------------


def redcon_rank(task: str, root: Path, files: list[str]) -> Ranking:
    """redcon's task-aware ranking via the public engine API."""
    from redcon.engine import RedconEngine

    plan = RedconEngine().plan(task=task, repo=str(root), top_files=200)
    universe = set(files)
    ranking = [
        entry["path"] for entry in plan.get("ranked_files", []) if entry.get("path") in universe
    ]
    return ranking


# ---------------------------------------------------------------------------
# aider repo map (the real thing, not a reimplementation)
# ---------------------------------------------------------------------------


def aider_repomap_rank(task: str, root: Path, files: list[str]) -> Ranking:
    """File ranking from aider's RepoMap (PageRank over tree-sitter tags).

    The task's identifiers are passed as ``mentioned_idents`` - the same
    signal aider extracts from a chat conversation - so the comparison is
    fair: both tools see the identical task text.
    """
    from aider.models import Model
    from aider.repomap import RepoMap

    class _SilentIO:
        def tool_output(self, *args, **kwargs):
            pass

        def tool_warning(self, *args, **kwargs):
            pass

        def tool_error(self, *args, **kwargs):
            pass

        def read_text(self, fname):
            try:
                return Path(fname).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""

    repo_map = RepoMap(
        map_tokens=8192,
        root=str(root),
        main_model=Model("gpt-4o"),
        io=_SilentIO(),
        verbose=False,
    )
    abs_files = [str(root / f) for f in files]
    ranked_tags = repo_map.get_ranked_tags(
        chat_fnames=[],
        other_fnames=abs_files,
        mentioned_fnames=set(),
        mentioned_idents=set(_keywords(task)),
    )

    universe = set(files)
    ranking: Ranking = []
    seen: set[str] = set()
    for tag in ranked_tags:
        rel = getattr(tag, "rel_fname", None) or (tag[0] if tag else None)
        if isinstance(rel, str) and rel in universe and rel not in seen:
            seen.add(rel)
            ranking.append(rel)
    return ranking


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def keyword_rank(task: str, root: Path, files: list[str]) -> Ranking:
    """Task-keyword matching only: path hits weigh more than content hits."""
    keywords = _keywords(task)
    scored: list[tuple[float, str]] = []
    for rel in files:
        path_l = rel.lower()
        content_l = _read(root, rel).lower()
        score = 0.0
        for kw in keywords:
            score += 4.0 * path_l.count(kw)
            score += min(content_l.count(kw), 25)
        scored.append((score, rel))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [rel for score, rel in scored if score > 0]


_PY_IMPORT = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.M)
_JS_IMPORT = re.compile(r"""(?:from\s+|require\()\s*['"]([^'"]+)['"]""")


def pagerank_rank(task: str, root: Path, files: list[str]) -> Ranking:
    """Task-agnostic PageRank over a regex-built import graph.

    This is the structural-centrality family of approaches (what a repo map
    gives you with no task signal). Included so the delta between
    "task-aware" and "structure-only" is visible in one table.
    """
    universe = set(files)
    by_suffix: dict[str, list[str]] = {}
    for rel in files:
        parts = rel.split("/")
        for i in range(len(parts)):
            by_suffix.setdefault("/".join(parts[i:]), []).append(rel)

    def resolve_py(module: str) -> Iterable[str]:
        base = module.replace(".", "/")
        for cand in (f"{base}.py", f"{base}/__init__.py"):
            yield from by_suffix.get(cand, [])

    def resolve_js(spec: str, importer: str) -> Iterable[str]:
        if not spec.startswith("."):
            return
        base = (Path(importer).parent / spec).as_posix()
        base = re.sub(r"^\./", "", base).replace("../", "")
        for ext in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
            cand = base + ext
            if cand in universe:
                yield cand

    edges: dict[str, set[str]] = {rel: set() for rel in files}
    for rel in files:
        content = _read(root, rel)
        if rel.endswith(".py"):
            for module in _PY_IMPORT.findall(content):
                edges[rel].update(t for t in resolve_py(module) if t != rel)
        elif rel.endswith((".ts", ".tsx", ".js", ".jsx")):
            for spec in _JS_IMPORT.findall(content):
                edges[rel].update(t for t in resolve_js(spec, rel) if t != rel)

    damping = 0.85
    rank = dict.fromkeys(files, 1.0 / len(files))
    for _ in range(40):
        nxt = dict.fromkeys(files, (1.0 - damping) / len(files))
        for src, targets in edges.items():
            if targets:
                share = damping * rank[src] / len(targets)
                for dst in targets:
                    nxt[dst] += share
            else:
                leak = damping * rank[src] / len(files)
                for dst in files:
                    nxt[dst] += leak
        rank = nxt
    return sorted(files, key=lambda rel: (-rank[rel], rel))


def full_dump_rank(task: str, root: Path, files: list[str]) -> Ranking:
    """Everything in path order until the budget runs out (repomix-style)."""
    return sorted(files)


def random_rank(task: str, root: Path, files: list[str]) -> Ranking:
    """Seeded random order - the floor any real tool must clear."""
    rng = random.Random(hash(task) & 0xFFFFFFFF)
    shuffled = list(files)
    rng.shuffle(shuffled)
    return shuffled


ADAPTERS: dict[str, Adapter] = {
    "redcon": redcon_rank,
    "aider-repomap": aider_repomap_rank,
    "keyword-topk": keyword_rank,
    "pagerank": pagerank_rank,
    "full-dump": full_dump_rank,
    "random": random_rank,
}
