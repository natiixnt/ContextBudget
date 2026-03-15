from __future__ import annotations

from pathlib import Path

from redcon.config import load_config
from redcon.core.pipeline import as_json_dict, run_pack
from redcon.plugins import resolve_plugins


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_plugin_discovery_loads_explicit_registrations(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[plugins]
scorer = "example.path_glob_bonus"
compressor = "example.leading_summary"

[[plugins.registrations]]
target = "redcon.plugins.examples:path_glob_bonus_scorer"
options = { path_patterns = ["docs/**"], bonus = 8.0 }

[[plugins.registrations]]
target = "redcon.plugins.examples:leading_summary_compressor"
options = { preview_lines = 2 }
""".strip(),
    )

    cfg = load_config(tmp_path)
    resolved = resolve_plugins(cfg)

    assert resolved.scorer.name == "example.path_glob_bonus"
    assert resolved.compressor.name == "example.leading_summary"
    assert resolved.token_estimator.name == "builtin.char4"
    assert resolved.scorer_options["bonus"] == 8.0
    assert resolved.compressor_options["preview_lines"] == 2


def test_plugins_execute_when_selected_in_config(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[plugins]
scorer = "example.path_glob_bonus"
compressor = "example.leading_summary"

[[plugins.registrations]]
target = "redcon.plugins.examples:path_glob_bonus_scorer"
options = { path_patterns = ["docs/**"], bonus = 10.0 }

[[plugins.registrations]]
target = "redcon.plugins.examples:leading_summary_compressor"
options = { preview_lines = 2 }
""".strip(),
    )
    _write(tmp_path / "src" / "middleware.py", "def middleware() -> bool:\n    return True\n" * 20)
    _write(tmp_path / "docs" / "security.md", "security hardening checklist\nrotate keys\nreview sessions\n")

    data = as_json_dict(run_pack("update middleware flow", repo=tmp_path, max_tokens=300))

    assert data["implementations"] == {
        "scorer": "example.path_glob_bonus",
        "compressor": "example.leading_summary",
        "token_estimator": "builtin.char4",
    }
    assert data["token_estimator"]["selected_backend"] == "heuristic"
    assert data["ranked_files"][0]["path"] == "docs/security.md"
    first = data["compressed_context"][0]
    assert first["strategy"] == "plugin-summary"
    assert first["chunk_strategy"] == "plugin-leading-summary"


def test_plugins_fall_back_to_builtins_when_unconfigured(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    data = as_json_dict(run_pack("update auth", repo=tmp_path, max_tokens=200))

    assert data["implementations"] == {
        "scorer": "builtin.relevance",
        "compressor": "builtin.default",
        "token_estimator": "builtin.char4",
    }
    assert data["token_estimator"]["selected_backend"] == "heuristic"
