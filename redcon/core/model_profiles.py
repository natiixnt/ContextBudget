from __future__ import annotations

"""Built-in model profiles and runtime config adaptation helpers."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from redcon.config import RedconConfig
from redcon.core.tokens import builtin_token_estimator_plugin_name
from redcon.schemas.models import ModelProfileReport


@dataclass(frozen=True, slots=True)
class CompressionStrategyDefaults:
    """Named compression strategy defaults applied when not explicitly configured."""

    name: str
    full_file_threshold_tokens: int
    snippet_score_threshold: float
    snippet_hit_limit: int
    snippet_context_lines: int
    snippet_total_line_limit: int
    snippet_fallback_lines: int
    summary_preview_lines: int
    output_reserve_tokens: int


@dataclass(frozen=True, slots=True)
class BuiltinModelProfile:
    """Built-in model profile used to tune token estimation and packing behavior."""

    name: str
    family: str
    tokenizer: str
    context_window: int
    recommended_compression_strategy: str
    token_backend: str
    token_model: str
    encoding: str = ""
    fallback_backend: str = "heuristic"
    aliases: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


_COMPRESSION_STRATEGIES: dict[str, CompressionStrategyDefaults] = {
    "expanded": CompressionStrategyDefaults(
        name="expanded",
        full_file_threshold_tokens=1800,
        snippet_score_threshold=1.8,
        snippet_hit_limit=12,
        snippet_context_lines=3,
        snippet_total_line_limit=240,
        snippet_fallback_lines=100,
        summary_preview_lines=14,
        output_reserve_tokens=32_768,
    ),
    "balanced": CompressionStrategyDefaults(
        name="balanced",
        full_file_threshold_tokens=600,
        snippet_score_threshold=2.5,
        snippet_hit_limit=8,
        snippet_context_lines=2,
        snippet_total_line_limit=120,
        snippet_fallback_lines=60,
        summary_preview_lines=8,
        output_reserve_tokens=16_384,
    ),
    "aggressive": CompressionStrategyDefaults(
        name="aggressive",
        full_file_threshold_tokens=250,
        snippet_score_threshold=3.25,
        snippet_hit_limit=6,
        snippet_context_lines=1,
        snippet_total_line_limit=80,
        snippet_fallback_lines=30,
        summary_preview_lines=6,
        output_reserve_tokens=4_096,
    ),
}

_BUILTIN_MODEL_PROFILES: tuple[BuiltinModelProfile, ...] = (
    BuiltinModelProfile(
        name="gpt-4.1",
        family="gpt",
        tokenizer="tiktoken",
        context_window=1_047_576,
        recommended_compression_strategy="expanded",
        token_backend="exact_tiktoken",
        token_model="gpt-4.1",
        fallback_backend="model_aligned",
        notes=("OpenAI GPT-4.1 profile with 1,047,576-token context window.",),
    ),
    BuiltinModelProfile(
        name="gpt-4.1-mini",
        family="gpt",
        tokenizer="tiktoken",
        context_window=1_047_576,
        recommended_compression_strategy="expanded",
        token_backend="exact_tiktoken",
        token_model="gpt-4.1-mini",
        fallback_backend="model_aligned",
        notes=("OpenAI GPT-4.1 mini profile with 1,047,576-token context window.",),
    ),
    BuiltinModelProfile(
        name="gpt-4o",
        family="gpt",
        tokenizer="tiktoken",
        context_window=128_000,
        recommended_compression_strategy="balanced",
        token_backend="exact_tiktoken",
        token_model="gpt-4o",
        fallback_backend="model_aligned",
        notes=("OpenAI GPT-4o profile with 128,000-token context window.",),
    ),
    BuiltinModelProfile(
        name="gpt-4o-mini",
        family="gpt",
        tokenizer="tiktoken",
        context_window=128_000,
        recommended_compression_strategy="balanced",
        token_backend="exact_tiktoken",
        token_model="gpt-4o-mini",
        fallback_backend="model_aligned",
        notes=("OpenAI GPT-4o mini profile with 128,000-token context window.",),
    ),
    BuiltinModelProfile(
        name="claude-sonnet-4",
        family="claude",
        tokenizer="anthropic",
        context_window=200_000,
        recommended_compression_strategy="balanced",
        token_backend="model_aligned",
        token_model="claude-sonnet-4",
        fallback_backend="heuristic",
        aliases=("claude-sonnet-4-20250514",),
        notes=("Anthropic Claude Sonnet 4 profile with 200,000-token context window.",),
    ),
    BuiltinModelProfile(
        name="claude-3-7-sonnet",
        family="claude",
        tokenizer="anthropic",
        context_window=200_000,
        recommended_compression_strategy="balanced",
        token_backend="model_aligned",
        token_model="claude-3-7-sonnet",
        fallback_backend="heuristic",
        aliases=("claude-3.7-sonnet", "claude-3-7-sonnet-latest"),
        notes=("Anthropic Claude 3.7 Sonnet profile with 200,000-token context window.",),
    ),
    BuiltinModelProfile(
        name="codestral",
        family="mistral",
        tokenizer="tekken",
        context_window=128_000,
        recommended_compression_strategy="balanced",
        token_backend="model_aligned",
        token_model="codestral",
        fallback_backend="heuristic",
        aliases=("codestral-latest",),
        notes=("Mistral Codestral profile with 128,000-token context window.",),
    ),
    BuiltinModelProfile(
        name="mistral-small",
        family="mistral",
        tokenizer="tekken",
        context_window=128_000,
        recommended_compression_strategy="balanced",
        token_backend="model_aligned",
        token_model="mistral-small",
        fallback_backend="heuristic",
        aliases=("mistral-small-3.2", "mistral-small-latest"),
        notes=("Mistral Small profile with 128,000-token context window.",),
    ),
    BuiltinModelProfile(
        name="gpt-oss-20b",
        family="local",
        tokenizer="tiktoken",
        context_window=131_072,
        recommended_compression_strategy="balanced",
        token_backend="model_aligned",
        token_model="gpt-oss-20b",
        fallback_backend="heuristic",
        notes=("Open-weight local GPT-OSS profile with 131,072-token context window.",),
    ),
    BuiltinModelProfile(
        name="local-llm",
        family="local",
        tokenizer="custom",
        context_window=32_768,
        recommended_compression_strategy="aggressive",
        token_backend="model_aligned",
        token_model="local-llm",
        fallback_backend="heuristic",
        aliases=("local",),
        notes=("Generic local profile. Override [model] fields for exact tokenizer and context window.",),
    ),
)

_FAMILY_FALLBACKS: dict[str, BuiltinModelProfile] = {
    "gpt": BuiltinModelProfile(
        name="gpt",
        family="gpt",
        tokenizer="tiktoken",
        context_window=128_000,
        recommended_compression_strategy="balanced",
        token_backend="exact_tiktoken",
        token_model="gpt-4o",
        fallback_backend="model_aligned",
        notes=("Generic GPT family fallback used because the exact model alias is unknown.",),
    ),
    "claude": BuiltinModelProfile(
        name="claude",
        family="claude",
        tokenizer="anthropic",
        context_window=200_000,
        recommended_compression_strategy="balanced",
        token_backend="model_aligned",
        token_model="claude",
        fallback_backend="heuristic",
        notes=("Generic Claude family fallback used because the exact model alias is unknown.",),
    ),
    "mistral": BuiltinModelProfile(
        name="mistral",
        family="mistral",
        tokenizer="tekken",
        context_window=128_000,
        recommended_compression_strategy="balanced",
        token_backend="model_aligned",
        token_model="mistral",
        fallback_backend="heuristic",
        notes=("Generic Mistral family fallback used because the exact model alias is unknown.",),
    ),
    "local": BuiltinModelProfile(
        name="local-llm",
        family="local",
        tokenizer="custom",
        context_window=32_768,
        recommended_compression_strategy="aggressive",
        token_backend="model_aligned",
        token_model="local-llm",
        fallback_backend="heuristic",
        notes=("Generic local family fallback used because the exact model alias is unknown.",),
    ),
}

_PROFILE_LOOKUP: dict[str, BuiltinModelProfile] = {}
for _profile in _BUILTIN_MODEL_PROFILES:
    _PROFILE_LOOKUP[_profile.name] = _profile
    for _alias in _profile.aliases:
        _PROFILE_LOOKUP[_alias] = _profile


def _normalize_profile_name(value: str) -> str:
    return str(value).strip().lower()


def _normalize_compression_strategy(value: str) -> str:
    normalized = _normalize_profile_name(value)
    aliases = {
        "": "balanced",
        "full": "expanded",
        "fuller": "expanded",
        "wide": "expanded",
        "minimal": "expanded",
        "default": "balanced",
        "tight": "aggressive",
        "strict": "aggressive",
    }
    if normalized in _COMPRESSION_STRATEGIES:
        return normalized
    return aliases.get(normalized, "balanced")


def _infer_family(profile_name: str) -> str:
    normalized = _normalize_profile_name(profile_name)
    if normalized.startswith(("gpt-", "o1", "o3", "o4")):
        return "gpt"
    if normalized.startswith("claude"):
        return "claude"
    if normalized.startswith(("mistral", "codestral", "ministral", "magistral", "devstral")):
        return "mistral"
    if normalized.startswith(("local", "llama", "qwen", "gemma", "phi", "deepseek", "gpt-oss")):
        return "local"
    return ""


def resolve_builtin_model_profile(profile_name: str) -> tuple[BuiltinModelProfile | None, bool]:
    """Resolve a configured model-profile name to a built-in profile."""

    normalized = _normalize_profile_name(profile_name)
    if not normalized:
        return None, True
    exact = _PROFILE_LOOKUP.get(normalized)
    if exact is not None:
        return exact, True
    family = _infer_family(normalized)
    if family:
        return _FAMILY_FALLBACKS[family], False
    return None, False


def _apply_compression_strategy_defaults(config: RedconConfig, strategy: CompressionStrategyDefaults) -> None:
    if "full_file_threshold_tokens" not in config.explicit.compression:
        config.compression.full_file_threshold_tokens = strategy.full_file_threshold_tokens
    if "snippet_score_threshold" not in config.explicit.compression:
        config.compression.snippet_score_threshold = strategy.snippet_score_threshold
    if "snippet_hit_limit" not in config.explicit.compression:
        config.compression.snippet_hit_limit = strategy.snippet_hit_limit
    if "snippet_context_lines" not in config.explicit.compression:
        config.compression.snippet_context_lines = strategy.snippet_context_lines
    if "snippet_total_line_limit" not in config.explicit.compression:
        config.compression.snippet_total_line_limit = strategy.snippet_total_line_limit
    if "snippet_fallback_lines" not in config.explicit.compression:
        config.compression.snippet_fallback_lines = strategy.snippet_fallback_lines
    if "summary_preview_lines" not in config.explicit.compression:
        config.compression.summary_preview_lines = strategy.summary_preview_lines


def _resolve_output_reserve_tokens(
    *,
    context_window: int,
    strategy: CompressionStrategyDefaults,
    configured: int,
) -> int:
    reserve = configured if configured > 0 else strategy.output_reserve_tokens
    if context_window <= 0:
        return max(0, reserve)
    return max(0, min(reserve, max(0, context_window - 1)))


def _report_to_dict(report: ModelProfileReport) -> dict[str, Any]:
    data = {
        "selected_profile": report.selected_profile,
        "resolved_profile": report.resolved_profile,
        "family": report.family,
        "tokenizer": report.tokenizer,
        "context_window": report.context_window,
        "recommended_compression_strategy": report.recommended_compression_strategy,
        "effective_max_tokens": report.effective_max_tokens,
        "reserved_output_tokens": report.reserved_output_tokens,
        "budget_source": report.budget_source,
        "budget_clamped": report.budget_clamped,
        "notes": list(report.notes),
    }
    return data


def prepare_config_for_model_profile(
    config: RedconConfig,
    *,
    requested_max_tokens: int | None = None,
) -> tuple[RedconConfig, dict[str, Any]]:
    """Apply model-profile defaults and return the adjusted config plus report payload."""

    prepared = deepcopy(config)
    selected_profile = _normalize_profile_name(prepared.model.profile)
    has_custom_model_settings = bool(prepared.explicit.model)

    resolved_profile, exact_match = resolve_builtin_model_profile(selected_profile)
    if resolved_profile is None and not has_custom_model_settings:
        if requested_max_tokens is not None:
            prepared.budget.max_tokens = max(1, int(requested_max_tokens))
        return prepared, {}
    if resolved_profile is None:
        resolved_profile = _FAMILY_FALLBACKS["local"]
        exact_match = False

    tokenizer = prepared.model.tokenizer or resolved_profile.tokenizer
    context_window = prepared.model.context_window or resolved_profile.context_window
    strategy_name = _normalize_compression_strategy(
        prepared.model.recommended_compression_strategy or resolved_profile.recommended_compression_strategy
    )
    strategy = _COMPRESSION_STRATEGIES[strategy_name]
    reserve_tokens = _resolve_output_reserve_tokens(
        context_window=context_window,
        strategy=strategy,
        configured=prepared.model.output_reserve_tokens,
    )

    prepared.model.profile = selected_profile or resolved_profile.name
    prepared.model.tokenizer = tokenizer
    prepared.model.context_window = context_window
    prepared.model.recommended_compression_strategy = strategy.name
    prepared.model.output_reserve_tokens = reserve_tokens

    notes = list(resolved_profile.notes)
    if selected_profile and not exact_match:
        notes.append(f'Unknown model alias "{selected_profile}" resolved to the "{resolved_profile.name}" family profile.')
    if "tokenizer" in prepared.explicit.model and prepared.model.tokenizer:
        notes.append(f'Using configured tokenizer override "{prepared.model.tokenizer}".')
    if "context_window" in prepared.explicit.model and prepared.model.context_window:
        notes.append(f"Using configured context-window override of {prepared.model.context_window} tokens.")
    if "recommended_compression_strategy" in prepared.explicit.model and prepared.model.recommended_compression_strategy:
        notes.append(
            "Using configured compression-strategy override "
            f'"{prepared.model.recommended_compression_strategy}".'
        )
    if "output_reserve_tokens" in prepared.explicit.model and prepared.model.output_reserve_tokens:
        notes.append(
            "Using configured output-reserve override "
            f"of {prepared.model.output_reserve_tokens} tokens."
        )

    if "backend" not in prepared.explicit.tokens:
        prepared.tokens.backend = resolved_profile.token_backend
    if "model" not in prepared.explicit.tokens:
        prepared.tokens.model = selected_profile if selected_profile and exact_match else resolved_profile.token_model
    if "encoding" not in prepared.explicit.tokens and resolved_profile.encoding:
        prepared.tokens.encoding = resolved_profile.encoding
    if "fallback_backend" not in prepared.explicit.tokens:
        prepared.tokens.fallback_backend = resolved_profile.fallback_backend
    if "token_estimator" not in prepared.explicit.plugins:
        prepared.plugins.token_estimator = builtin_token_estimator_plugin_name(prepared.tokens.backend)

    _apply_compression_strategy_defaults(prepared, strategy)

    max_input_budget = max(1, context_window - reserve_tokens) if context_window > 0 else prepared.budget.max_tokens
    if requested_max_tokens is not None:
        budget_source = "cli"
        requested_budget = max(1, int(requested_max_tokens))
    elif "max_tokens" in prepared.explicit.budget:
        budget_source = "config"
        requested_budget = max(1, int(prepared.budget.max_tokens))
    else:
        budget_source = "model_profile"
        requested_budget = max_input_budget

    effective_budget = min(requested_budget, max_input_budget)
    budget_clamped = effective_budget != requested_budget
    if budget_clamped:
        notes.append(
            f"Clamped max_tokens from {requested_budget} to {effective_budget} "
            f"to fit the {context_window}-token context window."
        )
    prepared.budget.max_tokens = effective_budget

    report = ModelProfileReport(
        selected_profile=selected_profile or resolved_profile.name,
        resolved_profile=resolved_profile.name,
        family=resolved_profile.family,
        tokenizer=prepared.model.tokenizer,
        context_window=prepared.model.context_window,
        recommended_compression_strategy=prepared.model.recommended_compression_strategy,
        effective_max_tokens=effective_budget,
        reserved_output_tokens=prepared.model.output_reserve_tokens,
        budget_source=budget_source,
        budget_clamped=budget_clamped,
        notes=notes,
    )
    return prepared, _report_to_dict(report)


def normalize_model_profile_report(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize model-profile metadata from current or older artifacts."""

    raw = data.get("model_profile")
    if not isinstance(raw, Mapping):
        return {}
    return {
        "selected_profile": str(raw.get("selected_profile", "")),
        "resolved_profile": str(raw.get("resolved_profile", "")),
        "family": str(raw.get("family", "")),
        "tokenizer": str(raw.get("tokenizer", "")),
        "context_window": int(raw.get("context_window", 0) or 0),
        "recommended_compression_strategy": str(raw.get("recommended_compression_strategy", "")),
        "effective_max_tokens": int(raw.get("effective_max_tokens", 0) or 0),
        "reserved_output_tokens": int(raw.get("reserved_output_tokens", 0) or 0),
        "budget_source": str(raw.get("budget_source", "")),
        "budget_clamped": bool(raw.get("budget_clamped", False)),
        "notes": [str(item) for item in raw.get("notes", [])] if isinstance(raw.get("notes"), list) else [],
    }
