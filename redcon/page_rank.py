"""
Pure-Python PageRank for ranking files in an import graph.

Used by ``redcon.repo_map`` to mix structural relevance (transitive
dependency hubs) into the engine's keyword-and-history ranker. The
implementation is a standard power-iteration PageRank with optional
personalisation - you can bias the random surfer toward files matching
the task keywords so the algorithm rewards both the literal matches and
the files those matches depend on.

No numpy / scipy / networkx: a 50-line iterative pass over a Python
dict is fast enough on the corpus sizes we care about (sub-50 ms on a
2000-file repo) and avoids a dep we don't need elsewhere.
"""

from __future__ import annotations

from typing import Iterable


def page_rank(
    nodes: Iterable[str],
    edges: dict[str, list[str]],
    *,
    personalisation: dict[str, float] | None = None,
    damping: float = 0.85,
    iterations: int = 30,
    tolerance: float = 1e-6,
) -> dict[str, float]:
    """
    Run PageRank on a directed graph and return per-node scores.

    Parameters:
        nodes: collection of node ids (every key in `edges` plus their
            targets should be present).
        edges: mapping ``node -> [outgoing edge targets]``. Targets that
            aren't in ``nodes`` are silently ignored (treated as dangling).
        personalisation: optional ``node -> weight`` mapping summing to
            <= 1.0; missing keys default to 0. When omitted or summing
            to 0 the standard uniform teleport is used (each node gets
            ``1 / N``).
        damping: random surfer continuation probability (Brin/Page
            classical default: 0.85).
        iterations: maximum power-method passes.
        tolerance: convergence threshold on L1 score change between
            iterations.

    Returns:
        mapping ``node -> score``. Scores sum to ~1.0 (subject to
        floating point).
    """
    node_list = list(dict.fromkeys(nodes))  # dedup, preserve order
    n = len(node_list)
    if n == 0:
        return {}

    pers = _normalise_personalisation(node_list, personalisation)
    inverse_n = 1.0 / n
    score = {node: inverse_n for node in node_list}

    out_degree = {node: len(edges.get(node, [])) for node in node_list}
    # Pre-compute reverse adjacency for the propagation step.
    incoming: dict[str, list[str]] = {node: [] for node in node_list}
    node_set = set(node_list)
    for src in node_list:
        for tgt in edges.get(src, []):
            if tgt in node_set:
                incoming[tgt].append(src)

    teleport_weight = 1.0 - damping

    for _ in range(iterations):
        new_score: dict[str, float] = {}
        # Distribute mass from dangling nodes uniformly.
        dangling = sum(score[node] for node in node_list if out_degree[node] == 0)
        for node in node_list:
            inflow = 0.0
            for src in incoming[node]:
                if out_degree[src]:
                    inflow += score[src] / out_degree[src]
            new_score[node] = (
                teleport_weight * pers.get(node, 0.0)
                + damping * (inflow + dangling * pers.get(node, 0.0))
            )
        delta = sum(abs(new_score[n_] - score[n_]) for n_ in node_list)
        score = new_score
        if delta < tolerance:
            break

    # Re-normalise in case of floating drift.
    total = sum(score.values())
    if total > 0:
        score = {n_: s / total for n_, s in score.items()}
    return score


def _normalise_personalisation(
    nodes: list[str], personalisation: dict[str, float] | None
) -> dict[str, float]:
    if not personalisation:
        return {n: 1.0 / len(nodes) for n in nodes}
    total = sum(max(0.0, v) for v in personalisation.values())
    if total <= 0.0:
        return {n: 1.0 / len(nodes) for n in nodes}
    return {n: max(0.0, personalisation.get(n, 0.0)) / total for n in nodes}
