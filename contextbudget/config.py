from __future__ import annotations

"""Configuration loading and defaults for ContextBudget."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from contextbudget.schemas.models import (
    BINARY_EXTENSIONS,
    CACHE_FILE,
    DEFAULT_TOP_FILES,
    DEFAULT_IGNORE_DIRS,
    DEFAULT_MAX_TOKENS,
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
    snippet_hit_limit: int = 8
    snippet_context_lines: int = 2
    snippet_total_line_limit: int = 120
    snippet_fallback_lines: int = 60
    summary_preview_lines: int = 8
    risk_skip_weight: float = 0.55
    risk_compression_weight: float = 0.45


@dataclass(slots=True)
class CacheSettings:
    """Settings for cache and duplicate detection behavior."""

    summary_cache_enabled: bool = True
    cache_file: str = CACHE_FILE
    duplicate_hash_cache_enabled: bool = True


@dataclass(slots=True)
class TelemetrySettings:
    """Settings for optional telemetry emission."""

    enabled: bool = False
    sink: str = "noop"
    file_path: str = ".contextbudget/telemetry.jsonl"


@dataclass(slots=True)
class ContextBudgetConfig:
    """Top-level settings object used by all pipeline stages."""

    scan: ScanSettings = field(default_factory=ScanSettings)
    budget: BudgetSettings = field(default_factory=BudgetSettings)
    score: ScoreSettings = field(default_factory=ScoreSettings)
    compression: CompressionSettings = field(default_factory=CompressionSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)


def default_config() -> ContextBudgetConfig:
    """Return a fresh default configuration object."""

    return ContextBudgetConfig()


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
    if "summary_cache_enabled" in data:
        settings.summary_cache_enabled = bool(data["summary_cache_enabled"])
    if "cache_file" in data:
        settings.cache_file = str(data["cache_file"])
    if "duplicate_hash_cache_enabled" in data:
        settings.duplicate_hash_cache_enabled = bool(data["duplicate_hash_cache_enabled"])
    # Backward-compatible key
    if "enabled" in data:
        settings.summary_cache_enabled = bool(data["enabled"])


def _apply_telemetry_overrides(settings: TelemetrySettings, data: Mapping[str, Any]) -> None:
    if "enabled" in data:
        settings.enabled = bool(data["enabled"])
    if "sink" in data:
        settings.sink = str(data["sink"])
    if "file_path" in data:
        settings.file_path = str(data["file_path"])


def _apply_overrides(config: ContextBudgetConfig, data: Mapping[str, Any]) -> ContextBudgetConfig:
    scan_data = data.get("scan")
    if isinstance(scan_data, Mapping):
        _apply_scan_overrides(config.scan, scan_data)

    score_data = data.get("score")
    if isinstance(score_data, Mapping):
        _apply_score_overrides(config.score, score_data)

    cache_data = data.get("cache")
    if isinstance(cache_data, Mapping):
        _apply_cache_overrides(config.cache, cache_data)

    telemetry_data = data.get("telemetry")
    if isinstance(telemetry_data, Mapping):
        _apply_telemetry_overrides(config.telemetry, telemetry_data)

    # Apply legacy sections first for compatibility, then new sections.
    legacy_pack_data = data.get("pack")
    if isinstance(legacy_pack_data, Mapping):
        _apply_budget_overrides(config.budget, legacy_pack_data)
        _apply_compression_overrides(config.compression, legacy_pack_data)

    legacy_output_data = data.get("output")
    if isinstance(legacy_output_data, Mapping):
        _apply_budget_overrides(config.budget, legacy_output_data)

    budget_data = data.get("budget")
    if isinstance(budget_data, Mapping):
        _apply_budget_overrides(config.budget, budget_data)

    compression_data = data.get("compression")
    if isinstance(compression_data, Mapping):
        _apply_compression_overrides(config.compression, compression_data)

    return config


def _discover_config_path(repo: Path, config_path: Path | None = None) -> Path:
    if config_path is not None:
        return config_path
    return repo / "contextbudget.toml"


def load_config(repo: Path, config_path: Path | None = None) -> ContextBudgetConfig:
    """Load configuration from ``contextbudget.toml`` with defaults fallback."""

    config = default_config()
    path = _discover_config_path(repo, config_path)

    if not path.exists():
        return config

    if tomllib is None:
        raise RuntimeError("TOML parser unavailable. Install 'tomli' for Python < 3.11.")

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return config
    return _apply_overrides(config, data)
