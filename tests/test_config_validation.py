from __future__ import annotations

from redcon.config import (
    BudgetSettings,
    CacheSettings,
    CompressionSettings,
    RedconConfig,
    SummarizationSettings,
    TokenEstimationSettings,
    validate_config,
)


def test_valid_default_config() -> None:
    cfg = RedconConfig()
    errors = validate_config(cfg)
    assert errors == []


def test_invalid_max_tokens() -> None:
    cfg = RedconConfig()
    cfg.budget.max_tokens = -1
    errors = validate_config(cfg)
    assert any("[budget].max_tokens" in e for e in errors)


def test_invalid_zero_max_tokens() -> None:
    cfg = RedconConfig()
    cfg.budget.max_tokens = 0
    errors = validate_config(cfg)
    assert any("[budget].max_tokens" in e for e in errors)


def test_invalid_top_files() -> None:
    cfg = RedconConfig()
    cfg.budget.top_files = -5
    errors = validate_config(cfg)
    assert any("[budget].top_files" in e for e in errors)


def test_none_top_files_is_valid() -> None:
    cfg = RedconConfig()
    cfg.budget.top_files = None
    errors = validate_config(cfg)
    assert not any("[budget].top_files" in e for e in errors)


def test_invalid_max_file_size() -> None:
    cfg = RedconConfig()
    cfg.scan.max_file_size_bytes = 0
    errors = validate_config(cfg)
    assert any("[scan].max_file_size_bytes" in e for e in errors)


def test_invalid_preview_chars() -> None:
    cfg = RedconConfig()
    cfg.scan.preview_chars = -1
    errors = validate_config(cfg)
    assert any("[scan].preview_chars" in e for e in errors)


def test_invalid_cache_backend() -> None:
    cfg = RedconConfig()
    cfg.cache.backend = "invalid_backend"
    errors = validate_config(cfg)
    assert any("[cache].backend" in e for e in errors)


def test_valid_cache_backends() -> None:
    for backend in ("local_file", "redis", "sqlite", "memory"):
        cfg = RedconConfig()
        cfg.cache.backend = backend
        errors = validate_config(cfg)
        assert not any("[cache].backend" in e for e in errors), f"{backend} should be valid"


def test_invalid_token_backend() -> None:
    cfg = RedconConfig()
    cfg.tokens.backend = "nonexistent"
    errors = validate_config(cfg)
    assert any("[tokens].backend" in e for e in errors)


def test_valid_token_backends() -> None:
    for backend in ("heuristic", "model_aligned", "exact_tiktoken"):
        cfg = RedconConfig()
        cfg.tokens.backend = backend
        errors = validate_config(cfg)
        assert not any("[tokens].backend" in e for e in errors), f"{backend} should be valid"


def test_invalid_summarization_backend() -> None:
    cfg = RedconConfig()
    cfg.summarization.backend = "magic"
    errors = validate_config(cfg)
    assert any("[summarization].backend" in e for e in errors)


def test_negative_degradation_rounds() -> None:
    cfg = RedconConfig()
    cfg.compression.max_degradation_rounds = -1
    errors = validate_config(cfg)
    assert any("[compression].max_degradation_rounds" in e for e in errors)


def test_negative_full_file_threshold() -> None:
    cfg = RedconConfig()
    cfg.compression.full_file_threshold_tokens = -10
    errors = validate_config(cfg)
    assert any("[compression].full_file_threshold_tokens" in e for e in errors)


def test_negative_role_multiplier() -> None:
    cfg = RedconConfig()
    cfg.score.role_multipliers["test"] = -0.5
    errors = validate_config(cfg)
    assert any("role_multipliers.test" in e for e in errors)


def test_invalid_telemetry_sink_when_enabled() -> None:
    cfg = RedconConfig()
    cfg.telemetry.enabled = True
    cfg.telemetry.sink = "kafka"
    errors = validate_config(cfg)
    assert any("[telemetry].sink" in e for e in errors)


def test_telemetry_sink_ignored_when_disabled() -> None:
    cfg = RedconConfig()
    cfg.telemetry.enabled = False
    cfg.telemetry.sink = "kafka"
    errors = validate_config(cfg)
    assert not any("[telemetry].sink" in e for e in errors)


def test_multiple_errors_reported() -> None:
    cfg = RedconConfig()
    cfg.budget.max_tokens = -1
    cfg.cache.backend = "invalid"
    cfg.compression.max_degradation_rounds = -1
    errors = validate_config(cfg)
    assert len(errors) >= 3
