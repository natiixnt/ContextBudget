"""Tests for the pure-Python PageRank used by repo-map."""

from __future__ import annotations

import math

from redcon.page_rank import page_rank


def test_empty_graph_returns_empty():
    assert page_rank([], {}) == {}


def test_uniform_score_on_isolated_nodes():
    nodes = ["a", "b", "c"]
    scores = page_rank(nodes, {})
    expected = 1.0 / 3
    for v in scores.values():
        assert math.isclose(v, expected, rel_tol=1e-3)


def test_chain_propagates_score_forward():
    """a -> b -> c chain: c should outrank a (terminal node accumulates)."""
    nodes = ["a", "b", "c"]
    edges = {"a": ["b"], "b": ["c"], "c": []}
    scores = page_rank(nodes, edges)
    assert scores["c"] > scores["b"] > scores["a"]


def test_personalisation_skews_toward_target():
    """A surfer that strictly teleports to one node should rank it highest."""
    nodes = ["a", "b", "c"]
    edges = {"a": ["b"], "b": ["c"], "c": []}
    scores = page_rank(
        nodes, edges, personalisation={"a": 1.0, "b": 0.0, "c": 0.0}
    )
    assert scores["a"] > scores["c"]


def test_scores_sum_to_one():
    nodes = ["x", "y", "z", "w"]
    edges = {"x": ["y", "z"], "y": ["z"], "z": ["w"], "w": ["x"]}
    scores = page_rank(nodes, edges)
    assert math.isclose(sum(scores.values()), 1.0, rel_tol=1e-6)


def test_dangling_nodes_handled():
    """Nodes with no outgoing edges shouldn't black-hole probability mass."""
    nodes = ["a", "b"]
    edges = {"a": ["b"]}  # b has no outgoing edges
    scores = page_rank(nodes, edges)
    assert math.isclose(sum(scores.values()), 1.0, rel_tol=1e-6)
    # b receives a's mass plus its own teleport, so it outranks a.
    assert scores["b"] > scores["a"]


def test_unknown_targets_are_ignored():
    """Edges to nodes outside the node list are dropped silently."""
    nodes = ["a"]
    edges = {"a": ["nonexistent"]}  # treated as dangling
    scores = page_rank(nodes, edges)
    assert math.isclose(sum(scores.values()), 1.0, rel_tol=1e-6)


def test_convergence_iterations_argument():
    """Capping iterations shouldn't crash; just exits early."""
    nodes = ["a", "b"]
    edges = {"a": ["b"], "b": ["a"]}
    scores = page_rank(nodes, edges, iterations=1)
    assert math.isclose(sum(scores.values()), 1.0, rel_tol=1e-3)


def test_two_clusters_score_relatively():
    """Two disconnected clusters: scores within each cluster should sum to
    roughly half each (with uniform teleport)."""
    nodes = ["a", "b", "c", "d"]
    edges = {"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"]}
    scores = page_rank(nodes, edges)
    cluster_ab = scores["a"] + scores["b"]
    cluster_cd = scores["c"] + scores["d"]
    assert math.isclose(cluster_ab, cluster_cd, rel_tol=1e-3)
