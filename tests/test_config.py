from __future__ import annotations

from pathlib import Path

from contextbudget.config import load_config
from contextbudget.core.pipeline import as_json_dict, run_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_config_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.budget.max_tokens == 30_000
    assert cfg.budget.top_files is None
    assert cfg.scan.max_file_size_bytes == 2_000_000
    assert cfg.telemetry.enabled is False


def test_load_config_overrides_from_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[budget]
max_tokens = 1234
top_files = 10

[compression]
summary_preview_lines = 5

[cache]
duplicate_hash_cache_enabled = false

[telemetry]
enabled = true
sink = "file"
file_path = ".local/events.jsonl"
""".strip(),
    )

    cfg = load_config(tmp_path)
    assert cfg.budget.max_tokens == 1234
    assert cfg.budget.top_files == 10
    assert cfg.compression.summary_preview_lines == 5
    assert cfg.cache.duplicate_hash_cache_enabled is False
    assert cfg.telemetry.enabled is True
    assert cfg.telemetry.sink == "file"
    assert cfg.telemetry.file_path == ".local/events.jsonl"


def test_run_pack_uses_config_default_budget(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[budget]
max_tokens = 80
""".strip(),
    )
    _write(tmp_path / "src" / "a.py", "def a():\n    return 1\n" * 30)

    report = as_json_dict(run_pack("touch a", repo=tmp_path, max_tokens=None))
    assert report["max_tokens"] == 80


def test_run_pack_max_tokens_argument_overrides_config(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[budget]
max_tokens = 200
""".strip(),
    )
    _write(tmp_path / "src" / "a.py", "def a():\n    return 1\n" * 30)

    report = as_json_dict(run_pack("touch a", repo=tmp_path, max_tokens=55))
    assert report["max_tokens"] == 55
