from __future__ import annotations

"""Configuration loading and defaults for Redcon."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from redcon.schemas.models import (
    BINARY_EXTENSIONS,
    CACHE_FILE,
    DEFAULT_TOP_FILES,
    DEFAULT_IGNORE_DIRS,
    DEFAULT_MAX_TOKENS,
    RUN_HISTORY_FILE,
)

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found, assignment]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]


@dataclass(slots=True)
class ScanSettings:
    """Settings for repository scanning."""

    include_globs: list[str] = field(default_factory=lambda: ["*"])
    ignore_globs: list[str] = field(default_factory=list)
    max_file_size_bytes: int = 2_000_000
    preview_chars: int = 2_000
    ignore_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_IGNORE_DIRS))
    binary_extensions: set[str] = field(default_factory=lambda: set(BINARY_EXTENSIONS))


@dataclass(slots=True)
class BudgetSettings:
    """Token and selection budget settings."""

    max_tokens: int = DEFAULT_MAX_TOKENS
    top_files: int | None = None


@dataclass(slots=True)
class ScoreSettings:
    """Settings for deterministic file relevance scoring."""

    path_keyword_weight: float = 2.0
    content_keyword_weight: float = 0.25
    content_keyword_cap: float = 4.0
    code_extension_bonus: float = 0.35
    test_path_bonus: float = 0.25
    large_file_line_threshold: int = 500
    large_file_penalty: float = 0.2
    critical_path_bonus: float = 1.0
    critical_path_keywords: list[str] = field(default_factory=list)
    enable_import_graph_signals: bool = True
    graph_seed_score_threshold: float = 2.0
    graph_imported_by_relevant_bonus: float = 0.9
    graph_depends_on_relevant_bonus: float = 0.7
    graph_entrypoint_adjacency_bonus: float = 0.45
    graph_bonus_cap: float = 2.5
    history_selected_file_boost: float = 1.25
    history_ignored_file_penalty: float = 0.35
    history_score_cap: float = 3.0
    history_task_similarity_threshold: float = 0.2
    history_entry_limit: int = 50
    entrypoint_filenames: set[str] = field(
        default_factory=lambda: {
            "main.py",
            "app.py",
            "server.py",
            "manage.py",
            "index.ts",
            "index.js",
            "main.ts",
            "main.js",
            "cli.py",
        }
    )
    code_extensions: set[str] = field(
        default_factory=lambda: {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java"}
    )
    signal_files: dict[str, float] = field(
        default_factory=lambda: {
            "readme.md": 0.5,
            "contributing.md": 0.4,
            "package.json": 0.3,
            "pyproject.toml": 0.3,
            "requirements.txt": 0.3,
            "dockerfile": 0.2,
        }
    )


@dataclass(slots=True)
class CompressionSettings:
    """Settings for context compression behavior."""

    full_file_threshold_tokens: int = 600
    snippet_score_threshold: float = 2.5
    symbol_extraction_enabled: bool = True
    snippet_hit_limit: int = 8
    snippet_context_lines: int = 2
    snippet_total_line_limit: int = 120
    snippet_fallback_lines: int = 60
    summary_preview_lines: int = 8
    risk_skip_weight: float = 0.55
    risk_compression_weight: float = 0.45


@dataclass(slots=True)
class SummarizationSettings:
    """Settings for deterministic or adapter-backed summarization."""

    backend: str = "deterministic"
    adapter: str = ""


@dataclass(slots=True)
class CacheSettings:
    """Settings for cache and duplicate detection behavior."""

    backend: str = "local_file"
    summary_cache_enabled: bool = True
    cache_file: str = CACHE_FILE
    duplicate_hash_cache_enabled: bool = True
    run_history_enabled: bool = True
    history_file: str = RUN_HISTORY_FILE
    history_db: str = ".redcon/history.db"
    history_max_entries: int = 200
    # Redis backend settings
    redis_url: str = "redis://localhost:6379/0"
    redis_namespace: str = "redcon"
    redis_ttl_seconds: int = 86400


@dataclass(slots=True)
class TokenEstimationSettings:
    """Settings for selecting and configuring token-estimation backends."""

    backend: str = "heuristic"
    model: str = "gpt-4o-mini"
    encoding: str = ""
    fallback_backend: str = "heuristic"


@dataclass(slots=True)
class ModelProfileSettings:
    """Settings for resolved or custom model-profile assumptions."""

    profile: str = ""
    tokenizer: str = ""
    context_window: int = 0
    recommended_compression_strategy: str = ""
    output_reserve_tokens: int = 0


@dataclass(slots=True)
class TelemetrySettings:
    """Settings for optional telemetry emission."""

    enabled: bool = False
    sink: str = "noop"
    file_path: str = ".redcon/telemetry.jsonl"


@dataclass(slots=True)
class PluginRegistrationSettings:
    """Explicit plugin registration entry loaded from configuration."""

    target: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PluginSettings:
    """Configured scorer, compressor, and token-estimator plugin selections."""

    scorer: str = "builtin.relevance"
    compressor: str = "builtin.default"
    token_estimator: str = "builtin.char4"
    registrations: list[PluginRegistrationSettings] = field(default_factory=list)


@dataclass(slots=True)
class ExplicitSettings:
    """Internal record of fields explicitly configured by the user."""

    budget: set[str] = field(default_factory=set)
    compression: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)
    plugins: set[str] = field(default_factory=set)
    model: set[str] = field(default_factory=set)


@dataclass(slots=True)
class WorkspaceRepoSettings:
    """Workspace repository entry with optional scan overrides."""

    label: str
    path: Path
    include_globs: list[str] = field(default_factory=list)
    ignore_globs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkspaceDefinition:
    """Local-only workspace configuration for multi-repo analysis."""

    path: Path
    root: Path
    repos: list[WorkspaceRepoSettings]
    config: "RedconConfig"
    name: str = ""


@dataclass(slots=True)
class RedconConfig:
    """Top-level settings object used by all pipeline stages."""

    scan: ScanSettings = field(default_factory=ScanSettings)
    budget: BudgetSettings = field(default_factory=BudgetSettings)
    score: ScoreSettings = field(default_factory=ScoreSettings)
    compression: CompressionSettings = field(default_factory=CompressionSettings)
    summarization: SummarizationSettings = field(default_factory=SummarizationSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    tokens: TokenEstimationSettings = field(default_factory=TokenEstimationSettings)
    model: ModelProfileSettings = field(default_factory=ModelProfileSettings)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)
    plugins: PluginSettings = field(default_factory=PluginSettings)
    explicit: ExplicitSettings = field(default_factory=ExplicitSettings, repr=False)


def default_config() -> RedconConfig:
    """Return a fresh default configuration object."""

    return RedconConfig()


def _to_set(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return set()


def _to_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return []


def _to_float_map(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    output: dict[str, float] = {}
    for key, raw in value.items():
        try:
            output[str(key).lower()] = float(raw)
        except (TypeError, ValueError):
            continue
    return output


def _to_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): raw for key, raw in value.items()}


def _mark_explicit_fields(explicit: set[str], data: Mapping[str, Any], field_map: Mapping[str, str]) -> None:
    for raw_key, canonical_name in field_map.items():
        if raw_key in data:
            explicit.add(canonical_name)


def _apply_scan_overrides(settings: ScanSettings, data: Mapping[str, Any]) -> None:
    if "include_globs" in data:
        settings.include_globs = _to_list(data["include_globs"])
    if "ignore_globs" in data:
        settings.ignore_globs = _to_list(data["ignore_globs"])
    if "max_file_size_bytes" in data:
        settings.max_file_size_bytes = int(data["max_file_size_bytes"])
    if "preview_chars" in data:
        settings.preview_chars = int(data["preview_chars"])
    # Backward-compatible keys
    if "ignore_dirs" in data:
        settings.ignore_dirs = _to_set(data["ignore_dirs"])
    if "binary_extensions" in data:
        settings.binary_extensions = _to_set(data["binary_extensions"])


def _apply_budget_overrides(settings: BudgetSettings, data: Mapping[str, Any]) -> None:
    if "max_tokens" in data:
        settings.max_tokens = int(data["max_tokens"])
    if "top_files" in data:
        raw_top_files = int(data["top_files"])
        settings.top_files = raw_top_files if raw_top_files > 0 else None
    # Backward-compatible keys
    if "default_max_tokens" in data:
        settings.max_tokens = int(data["default_max_tokens"])
    if "default_top_files" in data:
        raw_top_files = int(data["default_top_files"])
        settings.top_files = raw_top_files if raw_top_files > 0 else None
    if "plan_default_top_n" in data:
        raw_top_files = int(data["plan_default_top_n"])
        settings.top_files = raw_top_files if raw_top_files > 0 else DEFAULT_TOP_FILES


def _apply_score_overrides(settings: ScoreSettings, data: Mapping[str, Any]) -> None:
    if "path_keyword_weight" in data:
        settings.path_keyword_weight = float(data["path_keyword_weight"])
    if "content_keyword_weight" in data:
        settings.content_keyword_weight = float(data["content_keyword_weight"])
    if "content_keyword_cap" in data:
        settings.content_keyword_cap = float(data["content_keyword_cap"])
    if "code_extension_bonus" in data:
        settings.code_extension_bonus = float(data["code_extension_bonus"])
    if "test_path_bonus" in data:
        settings.test_path_bonus = float(data["test_path_bonus"])
    if "large_file_line_threshold" in data:
        settings.large_file_line_threshold = int(data["large_file_line_threshold"])
    if "large_file_penalty" in data:
        settings.large_file_penalty = float(data["large_file_penalty"])
    if "critical_path_bonus" in data:
        settings.critical_path_bonus = float(data["critical_path_bonus"])
    if "critical_path_keywords" in data:
        settings.critical_path_keywords = [item.lower() for item in _to_list(data["critical_path_keywords"])]
    if "enable_import_graph_signals" in data:
        settings.enable_import_graph_signals = bool(data["enable_import_graph_signals"])
    if "graph_seed_score_threshold" in data:
        settings.graph_seed_score_threshold = float(data["graph_seed_score_threshold"])
    if "graph_imported_by_relevant_bonus" in data:
        settings.graph_imported_by_relevant_bonus = float(data["graph_imported_by_relevant_bonus"])
    if "graph_depends_on_relevant_bonus" in data:
        settings.graph_depends_on_relevant_bonus = float(data["graph_depends_on_relevant_bonus"])
    if "graph_entrypoint_adjacency_bonus" in data:
        settings.graph_entrypoint_adjacency_bonus = float(data["graph_entrypoint_adjacency_bonus"])
    if "graph_bonus_cap" in data:
        settings.graph_bonus_cap = float(data["graph_bonus_cap"])
    if "history_selected_file_boost" in data:
        settings.history_selected_file_boost = float(data["history_selected_file_boost"])
    if "history_ignored_file_penalty" in data:
        settings.history_ignored_file_penalty = float(data["history_ignored_file_penalty"])
    if "history_score_cap" in data:
        settings.history_score_cap = float(data["history_score_cap"])
    if "history_task_similarity_threshold" in data:
        settings.history_task_similarity_threshold = float(data["history_task_similarity_threshold"])
    if "history_entry_limit" in data:
        settings.history_entry_limit = int(data["history_entry_limit"])
    if "entrypoint_filenames" in data:
        settings.entrypoint_filenames = _to_set(data["entrypoint_filenames"])
    if "code_extensions" in data:
        settings.code_extensions = _to_set(data["code_extensions"])
    if "signal_files" in data:
        merged = dict(settings.signal_files)
        merged.update(_to_float_map(data["signal_files"]))
        settings.signal_files = merged


def _apply_compression_overrides(settings: CompressionSettings, data: Mapping[str, Any]) -> None:
    if "full_file_threshold_tokens" in data:
        settings.full_file_threshold_tokens = int(data["full_file_threshold_tokens"])
    if "snippet_score_threshold" in data:
        settings.snippet_score_threshold = float(data["snippet_score_threshold"])
    if "symbol_extraction_enabled" in data:
        settings.symbol_extraction_enabled = bool(data["symbol_extraction_enabled"])
    if "snippet_hit_limit" in data:
        settings.snippet_hit_limit = int(data["snippet_hit_limit"])
    if "snippet_context_lines" in data:
        settings.snippet_context_lines = int(data["snippet_context_lines"])
    if "snippet_total_line_limit" in data:
        settings.snippet_total_line_limit = int(data["snippet_total_line_limit"])
    if "snippet_fallback_lines" in data:
        settings.snippet_fallback_lines = int(data["snippet_fallback_lines"])
    if "summary_preview_lines" in data:
        settings.summary_preview_lines = int(data["summary_preview_lines"])
    if "risk_skip_weight" in data:
        settings.risk_skip_weight = float(data["risk_skip_weight"])
    if "risk_compression_weight" in data:
        settings.risk_compression_weight = float(data["risk_compression_weight"])
    # Backward-compatible key
    if "summary_line_limit" in data:
        settings.summary_preview_lines = int(data["summary_line_limit"])


def _apply_cache_overrides(settings: CacheSettings, data: Mapping[str, Any]) -> None:
    if "backend" in data:
        settings.backend = str(data["backend"]).strip().lower()
    if "summary_cache_enabled" in data:
        settings.summary_cache_enabled = bool(data["summary_cache_enabled"])
    if "cache_file" in data:
        settings.cache_file = str(data["cache_file"])
    if "duplicate_hash_cache_enabled" in data:
        settings.duplicate_hash_cache_enabled = bool(data["duplicate_hash_cache_enabled"])
    if "run_history_enabled" in data:
        settings.run_history_enabled = bool(data["run_history_enabled"])
    if "history_file" in data:
        settings.history_file = str(data["history_file"])
    if "history_db" in data:
        settings.history_db = str(data["history_db"])
    if "history_max_entries" in data:
        settings.history_max_entries = int(data["history_max_entries"])
    # Redis backend settings
    if "redis_url" in data:
        settings.redis_url = str(data["redis_url"]).strip()
    if "redis_namespace" in data:
        settings.redis_namespace = str(data["redis_namespace"]).strip()
    if "redis_ttl_seconds" in data:
        settings.redis_ttl_seconds = int(data["redis_ttl_seconds"])
    # Backward-compatible key
    if "enabled" in data:
        settings.summary_cache_enabled = bool(data["enabled"])


def _apply_summarization_overrides(settings: SummarizationSettings, data: Mapping[str, Any]) -> None:
    if "backend" in data:
        settings.backend = str(data["backend"]).strip().lower()
    if "adapter" in data:
        settings.adapter = str(data["adapter"]).strip()
    if "provider" in data:
        settings.backend = str(data["provider"]).strip().lower()


def _apply_token_estimation_overrides(settings: TokenEstimationSettings, data: Mapping[str, Any]) -> None:
    if "backend" in data:
        settings.backend = str(data["backend"]).strip().lower() or settings.backend
    if "model" in data:
        settings.model = str(data["model"]).strip() or settings.model
    if "encoding" in data:
        settings.encoding = str(data["encoding"]).strip()
    if "fallback_backend" in data:
        settings.fallback_backend = str(data["fallback_backend"]).strip().lower() or settings.fallback_backend


def _apply_model_overrides(settings: ModelProfileSettings, data: Mapping[str, Any]) -> None:
    if "profile" in data:
        settings.profile = str(data["profile"]).strip()
    if "tokenizer" in data:
        settings.tokenizer = str(data["tokenizer"]).strip()
    if "context_window" in data:
        settings.context_window = int(data["context_window"])
    if "recommended_compression_strategy" in data:
        settings.recommended_compression_strategy = str(data["recommended_compression_strategy"]).strip().lower()
    if "output_reserve_tokens" in data:
        settings.output_reserve_tokens = int(data["output_reserve_tokens"])


def _apply_telemetry_overrides(settings: TelemetrySettings, data: Mapping[str, Any]) -> None:
    if "enabled" in data:
        settings.enabled = bool(data["enabled"])
    if "sink" in data:
        settings.sink = str(data["sink"])
    if "file_path" in data:
        settings.file_path = str(data["file_path"])


def _apply_plugin_overrides(settings: PluginSettings, data: Mapping[str, Any]) -> None:
    if "scorer" in data:
        settings.scorer = str(data["scorer"]).strip() or settings.scorer
    if "compressor" in data:
        settings.compressor = str(data["compressor"]).strip() or settings.compressor
    if "token_estimator" in data:
        settings.token_estimator = str(data["token_estimator"]).strip() or settings.token_estimator
    raw_registrations = data.get("registrations")
    if isinstance(raw_registrations, list):
        registrations: list[PluginRegistrationSettings] = []
        for raw_item in raw_registrations:
            if not isinstance(raw_item, Mapping):
                continue
            target = str(raw_item.get("target", "")).strip()
            if not target:
                continue
            registrations.append(
                PluginRegistrationSettings(
                    target=target,
                    options=_to_dict(raw_item.get("options")),
                )
            )
        settings.registrations = registrations


def _apply_overrides(config: RedconConfig, data: Mapping[str, Any]) -> RedconConfig:
    scan_data = data.get("scan")
    if isinstance(scan_data, Mapping):
        _apply_scan_overrides(config.scan, scan_data)

    if "model_profile" in data:
        config.model.profile = str(data["model_profile"]).strip()
        config.explicit.model.add("profile")

    model_data = data.get("model")
    if isinstance(model_data, Mapping):
        _mark_explicit_fields(
            config.explicit.model,
            model_data,
            {
                "profile": "profile",
                "tokenizer": "tokenizer",
                "context_window": "context_window",
                "recommended_compression_strategy": "recommended_compression_strategy",
                "output_reserve_tokens": "output_reserve_tokens",
            },
        )
        _apply_model_overrides(config.model, model_data)

    score_data = data.get("score")
    if isinstance(score_data, Mapping):
        _apply_score_overrides(config.score, score_data)

    cache_data = data.get("cache")
    if isinstance(cache_data, Mapping):
        _apply_cache_overrides(config.cache, cache_data)

    summarization_data = data.get("summarization")
    if isinstance(summarization_data, Mapping):
        _apply_summarization_overrides(config.summarization, summarization_data)

    token_data = data.get("tokens")
    if isinstance(token_data, Mapping):
        _mark_explicit_fields(
            config.explicit.tokens,
            token_data,
            {
                "backend": "backend",
                "model": "model",
                "encoding": "encoding",
                "fallback_backend": "fallback_backend",
            },
        )
        _apply_token_estimation_overrides(config.tokens, token_data)

    telemetry_data = data.get("telemetry")
    if isinstance(telemetry_data, Mapping):
        _apply_telemetry_overrides(config.telemetry, telemetry_data)

    plugin_data = data.get("plugins")
    if isinstance(plugin_data, Mapping):
        _mark_explicit_fields(
            config.explicit.plugins,
            plugin_data,
            {
                "scorer": "scorer",
                "compressor": "compressor",
                "token_estimator": "token_estimator",
                "registrations": "registrations",
            },
        )
        _apply_plugin_overrides(config.plugins, plugin_data)

    # Apply legacy sections first for compatibility, then new sections.
    legacy_pack_data = data.get("pack")
    if isinstance(legacy_pack_data, Mapping):
        _mark_explicit_fields(
            config.explicit.budget,
            legacy_pack_data,
            {
                "max_tokens": "max_tokens",
                "default_max_tokens": "max_tokens",
                "top_files": "top_files",
                "default_top_files": "top_files",
                "plan_default_top_n": "top_files",
            },
        )
        _mark_explicit_fields(
            config.explicit.compression,
            legacy_pack_data,
            {
                "full_file_threshold_tokens": "full_file_threshold_tokens",
                "snippet_score_threshold": "snippet_score_threshold",
                "symbol_extraction_enabled": "symbol_extraction_enabled",
                "snippet_hit_limit": "snippet_hit_limit",
                "snippet_context_lines": "snippet_context_lines",
                "snippet_total_line_limit": "snippet_total_line_limit",
                "snippet_fallback_lines": "snippet_fallback_lines",
                "summary_preview_lines": "summary_preview_lines",
                "summary_line_limit": "summary_preview_lines",
                "risk_skip_weight": "risk_skip_weight",
                "risk_compression_weight": "risk_compression_weight",
            },
        )
        _apply_budget_overrides(config.budget, legacy_pack_data)
        _apply_compression_overrides(config.compression, legacy_pack_data)

    legacy_output_data = data.get("output")
    if isinstance(legacy_output_data, Mapping):
        _mark_explicit_fields(
            config.explicit.budget,
            legacy_output_data,
            {
                "max_tokens": "max_tokens",
                "default_max_tokens": "max_tokens",
                "top_files": "top_files",
                "default_top_files": "top_files",
                "plan_default_top_n": "top_files",
            },
        )
        _apply_budget_overrides(config.budget, legacy_output_data)

    budget_data = data.get("budget")
    if isinstance(budget_data, Mapping):
        _mark_explicit_fields(
            config.explicit.budget,
            budget_data,
            {
                "max_tokens": "max_tokens",
                "default_max_tokens": "max_tokens",
                "top_files": "top_files",
                "default_top_files": "top_files",
                "plan_default_top_n": "top_files",
            },
        )
        _apply_budget_overrides(config.budget, budget_data)

    compression_data = data.get("compression")
    if isinstance(compression_data, Mapping):
        _mark_explicit_fields(
            config.explicit.compression,
            compression_data,
            {
                "full_file_threshold_tokens": "full_file_threshold_tokens",
                "snippet_score_threshold": "snippet_score_threshold",
                "symbol_extraction_enabled": "symbol_extraction_enabled",
                "snippet_hit_limit": "snippet_hit_limit",
                "snippet_context_lines": "snippet_context_lines",
                "snippet_total_line_limit": "snippet_total_line_limit",
                "snippet_fallback_lines": "snippet_fallback_lines",
                "summary_preview_lines": "summary_preview_lines",
                "summary_line_limit": "summary_preview_lines",
                "risk_skip_weight": "risk_skip_weight",
                "risk_compression_weight": "risk_compression_weight",
            },
        )
        _apply_compression_overrides(config.compression, compression_data)

    if isinstance(token_data, Mapping) and not (
        isinstance(plugin_data, Mapping) and "token_estimator" in plugin_data
    ):
        backend = config.tokens.backend
        if backend in {"heuristic", "simple"}:
            config.plugins.token_estimator = "builtin.char4"
        elif backend in {"model_aligned", "model-aligned"}:
            config.plugins.token_estimator = "builtin.model_aligned"
        elif backend in {"exact", "exact_tiktoken", "exact-tiktoken", "tiktoken"}:
            config.plugins.token_estimator = "builtin.exact_tiktoken"

    return config


def _discover_config_path(repo: Path, config_path: Path | None = None) -> Path:
    if config_path is not None:
        return config_path
    return repo / "redcon.toml"


def load_config_from_mapping(data: Mapping[str, Any]) -> RedconConfig:
    """Load config overrides from an in-memory mapping."""

    config = default_config()
    return _apply_overrides(config, data)


def load_config(repo: Path, config_path: Path | None = None) -> RedconConfig:
    """Load configuration from ``redcon.toml`` with defaults fallback."""

    path = _discover_config_path(repo, config_path)

    if not path.exists():
        return default_config()

    if tomllib is None:
        raise RuntimeError("TOML parser unavailable. Install 'tomli' for Python < 3.11.")

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return default_config()
    return load_config_from_mapping(data)


def load_workspace(workspace_path: Path, config_path: Path | None = None) -> WorkspaceDefinition:
    """Load a local workspace TOML describing multiple repositories or packages."""

    resolved_workspace_path = workspace_path.resolve()
    if not resolved_workspace_path.exists():
        raise FileNotFoundError(f"Workspace config not found: {resolved_workspace_path}")

    if tomllib is None:
        raise RuntimeError("TOML parser unavailable. Install 'tomli' for Python < 3.11.")

    data = tomllib.loads(resolved_workspace_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"Workspace config must be a TOML table: {resolved_workspace_path}")

    raw_repos = data.get("repos")
    if not isinstance(raw_repos, list) or not raw_repos:
        raise ValueError("Workspace config must define at least one [[repos]] entry.")

    root = resolved_workspace_path.parent
    repos: list[WorkspaceRepoSettings] = []
    seen_labels: set[str] = set()

    for index, raw_repo in enumerate(raw_repos, start=1):
        if not isinstance(raw_repo, Mapping):
            raise ValueError(f"Workspace repo entry #{index} must be a TOML table.")

        raw_path = raw_repo.get("path")
        if raw_path is None:
            raise ValueError(f"Workspace repo entry #{index} is missing 'path'.")

        repo_path = (root / str(raw_path)).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Workspace repo path does not exist: {repo_path}")

        label = str(raw_repo.get("label") or repo_path.name).strip()
        if not label:
            raise ValueError(f"Workspace repo entry #{index} has an empty label.")
        if label in seen_labels:
            raise ValueError(f"Workspace repo label must be unique: {label}")
        seen_labels.add(label)

        repos.append(
            WorkspaceRepoSettings(
                label=label,
                path=repo_path,
                include_globs=_to_list(raw_repo.get("include_globs")),
                ignore_globs=_to_list(raw_repo.get("ignore_globs")),
            )
        )

    if config_path is not None:
        config = load_config(root, config_path=config_path)
    else:
        config = load_config_from_mapping(data)

    return WorkspaceDefinition(
        path=resolved_workspace_path,
        root=root,
        repos=repos,
        config=config,
        name=str(data.get("name", "")).strip(),
    )
