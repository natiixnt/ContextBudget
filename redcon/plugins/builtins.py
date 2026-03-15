from __future__ import annotations

"""Built-in plugin registrations that preserve current Redcon behavior."""

from typing import Any, Mapping

from redcon.compressors.context_compressor import compress_ranked_files
from redcon.config import TokenEstimationSettings
from redcon.core.tokens import (
    DEFAULT_MODEL_ALIGNED_MODEL,
    describe_builtin_token_estimator,
    estimate_tokens as builtin_estimate_tokens,
    estimate_with_builtin_backend,
)
from redcon.scorers.relevance import score_files

from redcon.plugins.api import CompressorPlugin, ScorerPlugin, TokenEstimatorPlugin


def _builtin_relevance_score(
    *,
    task: str,
    files,
    settings,
    options: Mapping[str, Any],
    estimate_tokens,
):
    del estimate_tokens
    return score_files(
        task,
        files,
        settings=settings,
        history_entries=options.get("history_entries"),
        similarity=options.get("task_similarity"),
    )


def _builtin_default_compress(
    *,
    task: str,
    repo,
    ranked_files,
    max_tokens: int,
    cache,
    settings,
    summarization_settings,
    options: Mapping[str, Any],
    estimate_tokens,
    duplicate_hash_cache_enabled: bool,
):
    del options
    return compress_ranked_files(
        task=task,
        repo=repo,
        ranked_files=ranked_files,
        max_tokens=max_tokens,
        cache=cache,
        settings=settings,
        summarization_settings=summarization_settings,
        duplicate_hash_cache_enabled=duplicate_hash_cache_enabled,
        token_estimator=estimate_tokens,
    )


def _builtin_char4_estimate(
    *,
    text: str,
    options: Mapping[str, Any],
) -> int:
    del options
    return builtin_estimate_tokens(text)


def _builtin_char4_describe(
    *,
    options: Mapping[str, Any],
):
    del options
    return describe_builtin_token_estimator(backend="heuristic")


def _builtin_model_aligned_estimate(
    *,
    text: str,
    options: Mapping[str, Any],
) -> int:
    return estimate_with_builtin_backend(
        text,
        backend="model_aligned",
        model=str(options.get("model", DEFAULT_MODEL_ALIGNED_MODEL) or DEFAULT_MODEL_ALIGNED_MODEL),
        fallback_backend="heuristic",
    )


def _builtin_model_aligned_describe(
    *,
    options: Mapping[str, Any],
):
    return describe_builtin_token_estimator(
        backend="model_aligned",
        model=str(options.get("model", DEFAULT_MODEL_ALIGNED_MODEL) or DEFAULT_MODEL_ALIGNED_MODEL),
        fallback_backend="heuristic",
    )


def _builtin_exact_tiktoken_estimate(
    *,
    text: str,
    options: Mapping[str, Any],
) -> int:
    return estimate_with_builtin_backend(
        text,
        backend="exact_tiktoken",
        model=str(options.get("model", DEFAULT_MODEL_ALIGNED_MODEL) or DEFAULT_MODEL_ALIGNED_MODEL),
        encoding=str(options.get("encoding", "") or ""),
        fallback_backend=str(options.get("fallback_backend", "heuristic") or "heuristic"),
    )


def _builtin_exact_tiktoken_describe(
    *,
    options: Mapping[str, Any],
):
    return describe_builtin_token_estimator(
        backend="exact_tiktoken",
        model=str(options.get("model", DEFAULT_MODEL_ALIGNED_MODEL) or DEFAULT_MODEL_ALIGNED_MODEL),
        encoding=str(options.get("encoding", "") or ""),
        fallback_backend=str(options.get("fallback_backend", "heuristic") or "heuristic"),
    )


builtin_relevance_scorer = ScorerPlugin(
    name="builtin.relevance",
    score=_builtin_relevance_score,
    description="Current deterministic relevance scorer with import-graph heuristics.",
)

builtin_default_compressor = CompressorPlugin(
    name="builtin.default",
    compress=_builtin_default_compress,
    description="Current deterministic pack/compression pipeline.",
)

builtin_char4_token_estimator = TokenEstimatorPlugin(
    name="builtin.char4",
    estimate=_builtin_char4_estimate,
    description="Current deterministic 1 token ~= 4 chars estimator.",
    describe=_builtin_char4_describe,
)

builtin_heuristic_token_estimator = TokenEstimatorPlugin(
    name="builtin.heuristic",
    estimate=_builtin_char4_estimate,
    description="Alias of the deterministic 1 token ~= 4 chars estimator.",
    describe=_builtin_char4_describe,
)

builtin_model_aligned_token_estimator = TokenEstimatorPlugin(
    name="builtin.model_aligned",
    estimate=_builtin_model_aligned_estimate,
    description="Approximate deterministic estimator tuned to a target model family.",
    describe=_builtin_model_aligned_describe,
)

builtin_exact_tiktoken_token_estimator = TokenEstimatorPlugin(
    name="builtin.exact_tiktoken",
    estimate=_builtin_exact_tiktoken_estimate,
    description='Exact local tokenization via optional "tiktoken", with deterministic fallback.',
    describe=_builtin_exact_tiktoken_describe,
)


def _token_estimator_options(settings: TokenEstimationSettings | None) -> dict[str, Any]:
    if settings is None:
        return {
            "model": DEFAULT_MODEL_ALIGNED_MODEL,
            "encoding": "",
            "fallback_backend": "heuristic",
        }
    return {
        "model": settings.model,
        "encoding": settings.encoding,
        "fallback_backend": settings.fallback_backend,
    }


def register_builtin_plugins(registry, *, token_settings: TokenEstimationSettings | None = None) -> None:
    """Register built-in plugins on a registry instance."""

    registry.register_scorer(builtin_relevance_scorer)
    registry.register_compressor(builtin_default_compressor)
    token_options = _token_estimator_options(token_settings)
    registry.register_token_estimator(builtin_char4_token_estimator, options=token_options)
    registry.register_token_estimator(builtin_heuristic_token_estimator, options=token_options)
    registry.register_token_estimator(builtin_model_aligned_token_estimator, options=token_options)
    registry.register_token_estimator(builtin_exact_tiktoken_token_estimator, options=token_options)
