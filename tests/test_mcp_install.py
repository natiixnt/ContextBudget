"""Tests for MCP auto-install into AI IDE configs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from redcon.mcp.install import (
    REDCON_ENTRY,
    _load_config,
    detect_targets,
    install_all,
    install_for_target,
    installed_path,
    uninstall_for_target,
)


def test_install_creates_claude_config(tmp_path: Path):
    """Installing for claude creates .mcp.json with redcon entry."""
    result = install_for_target("claude", tmp_path)
    assert result["status"] == "installed"
    config_path = tmp_path / ".mcp.json"
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["redcon"] == REDCON_ENTRY


def test_install_is_idempotent(tmp_path: Path):
    """Second install returns up_to_date without rewriting."""
    install_for_target("claude", tmp_path)
    result = install_for_target("claude", tmp_path)
    assert result["status"] == "up_to_date"


def test_install_preserves_existing_servers(tmp_path: Path):
    """Install merges into existing mcpServers entries."""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-server", "args": []}}})
    )
    result = install_for_target("claude", tmp_path)
    assert result["status"] == "installed"
    data = json.loads(config_path.read_text())
    assert "other" in data["mcpServers"]
    assert "redcon" in data["mcpServers"]


def test_install_handles_malformed_json(tmp_path: Path):
    """Install overwrites a malformed .mcp.json without crashing."""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text("not json at all")
    result = install_for_target("claude", tmp_path)
    assert result["status"] == "installed"
    data = json.loads(config_path.read_text())
    assert "redcon" in data["mcpServers"]


def test_uninstall_removes_entry(tmp_path: Path):
    """Uninstall removes the redcon entry but preserves others."""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "redcon": REDCON_ENTRY,
                    "other": {"command": "x", "args": []},
                }
            }
        )
    )
    result = uninstall_for_target("claude", tmp_path)
    assert result["status"] == "removed"
    data = json.loads(config_path.read_text())
    assert "redcon" not in data["mcpServers"]
    assert "other" in data["mcpServers"]


def test_uninstall_cleans_empty_servers(tmp_path: Path):
    """Uninstall removes mcpServers entirely if it becomes empty."""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({"mcpServers": {"redcon": REDCON_ENTRY}}))
    result = uninstall_for_target("claude", tmp_path)
    assert result["status"] == "removed"
    data = json.loads(config_path.read_text())
    assert "mcpServers" not in data


def test_uninstall_when_not_installed(tmp_path: Path):
    """Uninstall reports not_installed when no config file exists."""
    result = uninstall_for_target("claude", tmp_path)
    assert result["status"] == "not_installed"


def test_install_all_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """install_all installs into claude, cursor, and windsurf."""
    # Redirect home to tmp_path for cursor/windsurf global configs
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    results = install_all(tmp_path)
    targets = {r["target"]: r["status"] for r in results}
    assert targets["claude"] == "installed"
    assert targets["cursor"] == "installed"
    assert targets["windsurf"] == "installed"


def test_install_selected_targets_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """install_all with specific targets only installs those."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    results = install_all(tmp_path, targets=["claude"])
    assert len(results) == 1
    assert results[0]["target"] == "claude"


def test_install_unknown_target(tmp_path: Path):
    """Installing for an unknown target returns unknown status."""
    result = install_for_target("bogus", tmp_path)
    assert result["status"] == "unknown"


def test_load_config_missing_file(tmp_path: Path):
    """_load_config returns {} when file doesn't exist."""
    assert _load_config(tmp_path / "nope.json") == {}


def test_install_vscode_uses_servers_key_and_stdio_type(tmp_path: Path):
    """VS Code config uses a 'servers' key and marks the transport type."""
    result = install_for_target("vscode", tmp_path)
    assert result["status"] == "installed"
    data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert "mcpServers" not in data
    entry = data["servers"]["redcon"]
    assert entry["type"] == "stdio"
    assert entry["command"] == "redcon"


def test_install_codex_appends_toml_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Codex config.toml gets the redcon section appended, keeping user config."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    config_path = tmp_path / "home" / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('model = "o3"\n', encoding="utf-8")

    result = install_for_target("codex", tmp_path)
    assert result["status"] == "installed"
    text = config_path.read_text()
    assert 'model = "o3"' in text
    assert "[mcp_servers.redcon]" in text

    again = install_for_target("codex", tmp_path)
    assert again["status"] == "up_to_date"


def test_uninstall_codex_removes_only_redcon_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Uninstall strips the redcon TOML section but keeps everything else."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    config_path = tmp_path / "home" / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'model = "o3"\n\n[mcp_servers.redcon]\ncommand = "redcon"\n'
        'args = ["mcp", "serve"]\n\n[other]\nkey = "value"\n',
        encoding="utf-8",
    )

    result = uninstall_for_target("codex", tmp_path)
    assert result["status"] == "removed"
    text = config_path.read_text()
    assert "[mcp_servers.redcon]" not in text
    assert 'command = "redcon"' not in text
    assert 'model = "o3"' in text
    assert "[other]" in text
    assert 'key = "value"' in text


def test_install_gemini_writes_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Gemini CLI settings.json gets a standard mcpServers entry."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    result = install_for_target("gemini", tmp_path)
    assert result["status"] == "installed"
    data = json.loads((tmp_path / "home" / ".gemini" / "settings.json").read_text())
    assert data["mcpServers"]["redcon"] == REDCON_ENTRY


def test_detect_targets_defaults_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Without detected agents only the default trio is targeted."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    assert detect_targets(tmp_path) == ["claude", "cursor", "windsurf"]


def test_detect_targets_finds_installed_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Detected agents join the target list when their config home exists."""
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    (tmp_path / ".vscode").mkdir()
    (home / ".codex").mkdir(parents=True)
    (home / ".gemini").mkdir(parents=True)

    targets = detect_targets(tmp_path)
    assert targets == ["claude", "cursor", "windsurf", "vscode", "codex", "gemini"]

    results = install_all(tmp_path)
    statuses = {r["target"]: r["status"] for r in results}
    assert all(status == "installed" for status in statuses.values())
    assert set(statuses) == set(targets)


def test_installed_path_covers_all_formats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """installed_path reports registration across JSON keys and TOML."""
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    (home / ".codex").mkdir(parents=True)

    assert installed_path("vscode", tmp_path) is None
    assert installed_path("codex", tmp_path) is None

    install_for_target("vscode", tmp_path)
    install_for_target("codex", tmp_path)
    install_for_target("claude", tmp_path)

    assert installed_path("vscode", tmp_path) == tmp_path / ".vscode" / "mcp.json"
    assert installed_path("codex", tmp_path) == home / ".codex" / "config.toml"
    assert installed_path("claude", tmp_path) == tmp_path / ".mcp.json"
