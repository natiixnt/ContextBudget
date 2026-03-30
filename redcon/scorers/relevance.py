from __future__ import annotations

"""Relevance scoring stage for repository files."""

import logging
import re

from redcon.config import ScoreSettings
from redcon.core.text import task_keywords
from redcon.schemas.models import FileRecord, RankedFile
from redcon.scorers.file_roles import classify_file_role
from redcon.scorers.history import TaskSimilarityCallable, compute_historical_adjustments
from redcon.scorers.import_graph import build_import_graph

logger = logging.getLogger(__name__)

_PATH_SEG_RE = re.compile(r"[a-z0-9]+")


def _path_tokens(path_lower: str) -> frozenset[str]:
    """Extract word tokens from a file path (split on separators, min 4 chars)."""
    return frozenset(t for t in _PATH_SEG_RE.findall(path_lower) if len(t) >= 4)


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _format_graph_reason(prefix: str, related: set[str]) -> str:
    sorted_related = sorted(related)
    if len(sorted_related) == 1:
        return f"{prefix}: {sorted_related[0]}"
    return f"{prefix}: {sorted_related[0]} (+{len(sorted_related) - 1} more)"


def score_files(
    task: str,
    files: list[FileRecord],
    settings: ScoreSettings | None = None,
    *,
    history_entries=None,
    similarity: TaskSimilarityCallable | None = None,
    dirty_paths: set[str] | None = None,
) -> list[RankedFile]:
    """Score files for a task using deterministic keyword and import-graph heuristics."""

    # Handle empty task string - return 0 score for all files with a warning
    if not task or not task.strip():
        logger.warning("Empty task string provided - returning zero scores for all files")
        return []

    cfg = settings if settings is not None else ScoreSettings()
    keywords = task_keywords(task)

    heuristic_scores: dict[str, float] = {}
    reasons_by_path: dict[str, list[str]] = {}
    breakdowns: dict[str, dict[str, float]] = {}

    for record in files:
        # Handle None values in file metadata gracefully (use defaults)
        path_lower = (record.path or "").lower()
        preview_lower = (record.content_preview or "").lower()
        score = 0.0
        reasons: list[str] = []
        breakdown: dict[str, float] = {}

        for critical_keyword in cfg.critical_path_keywords:
            if critical_keyword and critical_keyword in path_lower:
                score += cfg.critical_path_bonus
                breakdown["critical_path"] = breakdown.get("critical_path", 0) + cfg.critical_path_bonus
                _add_reason(reasons, f"critical path keyword '{critical_keyword}'")

        path_tokens = _path_tokens(path_lower)
        symbols_lower = (record.symbol_names or "").lower()
        for keyword in keywords:
            path_hits = path_lower.count(keyword)
            preview_hits = preview_lower.count(keyword)
            symbol_hits = symbols_lower.count(keyword) if symbols_lower else 0
            if path_hits:
                delta = cfg.path_keyword_weight * path_hits
                score += delta
                breakdown["path_keyword"] = breakdown.get("path_keyword", 0) + delta
                _add_reason(reasons, f"path contains '{keyword}'")
            elif tokens_matching := [t for t in path_tokens if keyword.startswith(t)]:
                delta = cfg.path_keyword_weight * 0.6
                score += delta
                breakdown["path_keyword"] = breakdown.get("path_keyword", 0) + delta
                _add_reason(reasons, f"path abbreviation '{tokens_matching[0]}' matches '{keyword}'")
            if preview_hits:
                delta = min(cfg.content_keyword_cap, cfg.content_keyword_weight * preview_hits)
                score += delta
                breakdown["content_keyword"] = breakdown.get("content_keyword", 0) + delta
                _add_reason(reasons, f"content mentions '{keyword}'")
            if symbol_hits and not preview_hits:
                delta = min(cfg.content_keyword_cap, cfg.symbol_name_weight * symbol_hits)
                score += delta
                breakdown["symbol_match"] = breakdown.get("symbol_match", 0) + delta
                _add_reason(reasons, f"defines symbol matching '{keyword}'")

        name = record.path.rsplit("/", 1)[-1].lower()
        signals = cfg.signal_files
        if name in signals:
            score += signals[name]
            breakdown["signal_file"] = signals[name]
            _add_reason(reasons, f"signal file {name}")

        if (record.extension or "") in cfg.code_extensions:
            score += cfg.code_extension_bonus
            breakdown["code_extension"] = cfg.code_extension_bonus

        if "test" in path_lower:
            score += cfg.test_path_bonus
            breakdown["test_proximity"] = cfg.test_path_bonus
            _add_reason(reasons, "test proximity")

        if (record.line_count or 0) > cfg.large_file_line_threshold:
            score -= cfg.large_file_penalty
            breakdown["large_file_penalty"] = -cfg.large_file_penalty

        if dirty_paths and cfg.git_dirty_boost > 0 and (record.relative_path or "") in dirty_paths:
            score += cfg.git_dirty_boost
            breakdown["git_dirty"] = cfg.git_dirty_boost
            _add_reason(reasons, "has uncommitted changes")

        heuristic_scores[record.path] = score
        reasons_by_path[record.path] = reasons
        breakdowns[record.path] = breakdown

        logger.debug(
            "Scored %s: %.3f (reasons: %s)",
            record.path,
            score,
            ", ".join(reasons) if reasons else "none",
        )

    # -- File-role multipliers --
    if cfg.role_multipliers:
        keywords_lower = {k.lower() for k in keywords}
        for record in files:
            role = classify_file_role(record.path)
            multiplier = cfg.role_multipliers.get(role, 1.0)
            # Override: when task keywords mention the role's domain, boost
            # instead of penalizing (e.g. "test" keyword boosts test files).
            for override_role, override_keywords in (cfg.role_keyword_overrides or {}).items():
                if role == override_role and any(kw in keywords_lower for kw in override_keywords):
                    multiplier = cfg.role_keyword_override_multiplier
                    break
            if multiplier != 1.0:
                old_score = heuristic_scores[record.path]
                heuristic_scores[record.path] = old_score * multiplier
                reasons = reasons_by_path[record.path]
                _add_reason(reasons, f"role={role} (x{multiplier:.1f})")
                breakdowns[record.path]["role_multiplier"] = multiplier

    if cfg.enable_import_graph_signals and files:
        graph = build_import_graph(files, entrypoint_filenames=cfg.entrypoint_filenames)

        # Graph propagation model:
        # 1) Find seed files from high base scores.
        # 2) Award deterministic bonuses to one-hop neighbors.
        # 3) Keep explanations tied to specific graph relationships.
        seed_paths = {path for path, score in heuristic_scores.items() if score >= cfg.graph_seed_score_threshold}
        if not seed_paths:
            # Fallback seed for sparse tasks: top positive base score only.
            positive = sorted(
                [(path, score) for path, score in heuristic_scores.items() if score > 0],
                key=lambda item: (-item[1], item[0]),
            )
            if positive:
                seed_paths = {positive[0][0]}

        for record in files:
            path = record.path
            score = heuristic_scores[path]
            reasons = reasons_by_path[path]

            bd = breakdowns.get(path, {})
            graph_total = 0.0

            inbound_from_seed = graph.incoming.get(path, set()) & seed_paths
            if inbound_from_seed:
                bonus = min(cfg.graph_bonus_cap, cfg.graph_imported_by_relevant_bonus * len(inbound_from_seed))
                score += bonus
                graph_total += bonus
                _add_reason(reasons, _format_graph_reason("imported by relevant file", inbound_from_seed))

            outbound_to_seed = graph.outgoing.get(path, set()) & seed_paths
            if outbound_to_seed:
                bonus = min(cfg.graph_bonus_cap, cfg.graph_depends_on_relevant_bonus * len(outbound_to_seed))
                score += bonus
                graph_total += bonus
                _add_reason(reasons, _format_graph_reason("depends on relevant module", outbound_to_seed))

            adjacent_entrypoints = (graph.incoming.get(path, set()) | graph.outgoing.get(path, set())) & graph.entrypoints
            if adjacent_entrypoints:
                score += cfg.graph_entrypoint_adjacency_bonus
                graph_total += cfg.graph_entrypoint_adjacency_bonus
                _add_reason(reasons, _format_graph_reason("adjacent to entrypoint", adjacent_entrypoints))

            if graph_total > 0:
                bd["import_graph"] = round(graph_total, 3)
                breakdowns[path] = bd

            heuristic_scores[path] = score
            reasons_by_path[path] = reasons

    historical_adjustments = compute_historical_adjustments(
        task,
        files,
        cfg,
        history_entries=history_entries,
        similarity=similarity,
    )

    ranked: list[RankedFile] = []
    for record in files:
        heuristic_score = round(heuristic_scores[record.path], 3)
        historical_score = historical_adjustments.get(record.path, None)
        historical_value = historical_score.score if historical_score is not None else 0.0
        combined_score = round(heuristic_scores[record.path] + historical_value, 3)
        # Clamp final score to 0.0-10.0 range
        combined_score = max(0.0, min(10.0, combined_score))
        heuristic_score = max(0.0, min(10.0, heuristic_score))
        if combined_score <= 0:
            continue
        reasons = reasons_by_path[record.path]
        if historical_score is not None:
            for reason in historical_score.reasons:
                _add_reason(reasons, reason)
        bd = breakdowns.get(record.path, {})
        if historical_value:
            bd["historical"] = round(historical_value, 3)
        logger.debug(
            "Final ranked score for %s: %.3f (heuristic=%.3f, historical=%.3f)",
            record.path,
            combined_score,
            heuristic_score,
            historical_value,
        )
        ranked.append(
            RankedFile(
                file=record,
                score=combined_score,
                heuristic_score=heuristic_score,
                historical_score=round(historical_value, 3),
                reasons=reasons[:6],
                score_breakdown={k: round(v, 3) for k, v in bd.items()},
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.file.path))
    return ranked
