"""Tests for MCP auto-install into AI IDE configs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from redcon.mcp.install import (
    install_for_target,
    install_all,
    uninstall_for_target,
    _load_config,
    REDCON_ENTRY,
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
    config_path.write_text(json.dumps({
        "mcpServers": {
            "other": {"command": "other-server", "args": []}
        }
    }))
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
    config_path.write_text(json.dumps({
        "mcpServers": {
            "redcon": REDCON_ENTRY,
            "other": {"command": "x", "args": []},
        }
    }))
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
