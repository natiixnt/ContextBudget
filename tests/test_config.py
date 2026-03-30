from __future__ import annotations

import logging
from pathlib import Path

from redcon.config import load_config, load_workspace, validate_config
from redcon.core.pipeline import as_json_dict, run_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_config_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.budget.max_tokens == 30_000
    assert cfg.budget.top_files is None
    assert cfg.scan.max_file_size_bytes == 2_000_000
    assert cfg.summarization.backend == "deterministic"
    assert cfg.cache.backend == "local_file"
    assert cfg.cache.run_history_enabled is True
    assert cfg.cache.history_file == ".redcon/history.json"
    assert cfg.tokens.backend == "heuristic"
    assert cfg.tokens.model == "gpt-4o-mini"
    assert cfg.tokens.fallback_backend == "heuristic"
    assert cfg.model.profile == ""
    assert cfg.telemetry.enabled is False
    assert cfg.plugins.scorer == "builtin.relevance"
    assert cfg.plugins.compressor == "builtin.default"
    assert cfg.plugins.token_estimator == "builtin.char4"
    assert cfg.compression.symbol_extraction_enabled is True
    assert cfg.score.history_selected_file_boost == 1.25


def test_load_config_overrides_from_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
model_profile = "gpt-4.1"

[budget]
max_tokens = 1234
top_files = 10

[compression]
summary_preview_lines = 5
symbol_extraction_enabled = false

[summarization]
backend = "external"
adapter = "demo"

[model]
tokenizer = "llama-bpe"
context_window = 65536
recommended_compression_strategy = "aggressive"
output_reserve_tokens = 8192

[cache]
backend = "shared_stub"
duplicate_hash_cache_enabled = false
run_history_enabled = false
history_file = ".local/history.json"
history_max_entries = 50

[score]
history_selected_file_boost = 2.0
history_ignored_file_penalty = 0.5
history_task_similarity_threshold = 0.4
history_entry_limit = 8

[tokens]
backend = "model_aligned"
model = "gpt-4.1-mini"

[telemetry]
enabled = true
sink = "file"
file_path = ".local/events.jsonl"

[plugins]
scorer = "example.path_glob_bonus"

[[plugins.registrations]]
target = "redcon.plugins.examples:path_glob_bonus_scorer"
options = { path_patterns = ["docs/**"], bonus = 5.0 }
""".strip(),
    )

    cfg = load_config(tmp_path)
    assert cfg.budget.max_tokens == 1234
    assert cfg.budget.top_files == 10
    assert cfg.compression.summary_preview_lines == 5
    assert cfg.compression.symbol_extraction_enabled is False
    assert cfg.summarization.backend == "external"
    assert cfg.summarization.adapter == "demo"
    assert cfg.model.profile == "gpt-4.1"
    assert cfg.model.tokenizer == "llama-bpe"
    assert cfg.model.context_window == 65536
    assert cfg.model.recommended_compression_strategy == "aggressive"
    assert cfg.model.output_reserve_tokens == 8192
    assert cfg.cache.backend == "shared_stub"
    assert cfg.cache.duplicate_hash_cache_enabled is False
    assert cfg.cache.run_history_enabled is False
    assert cfg.cache.history_file == ".local/history.json"
    assert cfg.cache.history_max_entries == 50
    assert cfg.score.history_selected_file_boost == 2.0
    assert cfg.score.history_ignored_file_penalty == 0.5
    assert cfg.score.history_task_similarity_threshold == 0.4
    assert cfg.score.history_entry_limit == 8
    assert cfg.tokens.backend == "model_aligned"
    assert cfg.tokens.model == "gpt-4.1-mini"
    assert cfg.telemetry.enabled is True
    assert cfg.telemetry.sink == "file"
    assert cfg.telemetry.file_path == ".local/events.jsonl"
    assert cfg.plugins.scorer == "example.path_glob_bonus"
    assert cfg.plugins.token_estimator == "builtin.model_aligned"
    assert cfg.plugins.registrations[0].target == "redcon.plugins.examples:path_glob_bonus_scorer"
    assert cfg.plugins.registrations[0].options["bonus"] == 5.0


def test_run_pack_uses_config_default_budget(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[budget]
max_tokens = 80
""".strip(),
    )
    _write(tmp_path / "src" / "a.py", "def a():\n    return 1\n" * 30)

    report = as_json_dict(run_pack("touch a", repo=tmp_path, max_tokens=None))
    assert report["max_tokens"] == 80


def test_explicit_plugin_token_estimator_overrides_tokens_backend(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[tokens]
backend = "model_aligned"

[plugins]
token_estimator = "builtin.exact_tiktoken"
""".strip(),
    )

    cfg = load_config(tmp_path)
    assert cfg.tokens.backend == "model_aligned"
    assert cfg.plugins.token_estimator == "builtin.exact_tiktoken"


def test_run_pack_max_tokens_argument_overrides_config(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[budget]
max_tokens = 200
""".strip(),
    )
    _write(tmp_path / "src" / "a.py", "def a():\n    return 1\n" * 30)

    report = as_json_dict(run_pack("touch a", repo=tmp_path, max_tokens=55))
    assert report["max_tokens"] == 55


def test_load_workspace_uses_shared_config_and_repo_rules(tmp_path: Path) -> None:
    _write(tmp_path / "service-a" / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(tmp_path / "service-a" / "tests" / "test_auth.py", "def test_login() -> None:\n    assert True\n")
    _write(tmp_path / "service-b" / "src" / "billing.py", "def charge() -> bool:\n    return True\n")
    _write(
        tmp_path / "workspace.toml",
        """
[budget]
max_tokens = 111

[scan]
include_globs = ["**/*.py"]

[[repos]]
label = "service-a"
path = "service-a"
ignore_globs = ["tests/**"]

[[repos]]
label = "service-b"
path = "service-b"
include_globs = ["src/**/*.py"]
""".strip(),
    )

    workspace = load_workspace(tmp_path / "workspace.toml")

    assert workspace.config.budget.max_tokens == 111
    assert [repo.label for repo in workspace.repos] == ["service-a", "service-b"]
    assert workspace.repos[0].ignore_globs == ["tests/**"]
    assert workspace.repos[1].include_globs == ["src/**/*.py"]


def test_config_with_negative_max_tokens_fails_validation(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[budget]
max_tokens = -100
""".strip(),
    )

    cfg = load_config(tmp_path)
    assert cfg.budget.max_tokens == -100
    errors = validate_config(cfg)
    assert any("max_tokens" in err and "> 0" in err for err in errors)


def test_config_missing_budget_section_uses_defaults(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[scan]
max_file_size_bytes = 500000
""".strip(),
    )

    cfg = load_config(tmp_path)
    assert cfg.budget.max_tokens == 30_000
    assert cfg.budget.top_files is None
    assert cfg.scan.max_file_size_bytes == 500000


def test_config_unknown_keys_logs_warning(tmp_path: Path, caplog) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[budget]
max_tokens = 500

[totally_made_up_section]
foo = "bar"
""".strip(),
    )

    with caplog.at_level(logging.WARNING, logger="redcon.config"):
        cfg = load_config(tmp_path)

    assert cfg.budget.max_tokens == 500
    assert any("totally_made_up_section" in record.message for record in caplog.records)
