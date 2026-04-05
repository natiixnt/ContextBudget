"""
Auto-install Redcon MCP server config into supported AI IDEs.

Detects Claude Code, Cursor, and Windsurf based on standard config paths,
then merges the Redcon MCP entry into their mcp configuration files.
All writes are idempotent and preserve existing mcpServers entries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REDCON_ENTRY: dict[str, Any] = {
    "command": "redcon",
    "args": ["mcp", "serve"],
}


def _target_paths(project_root: Path) -> dict[str, list[Path]]:
    """
    Return the candidate MCP config paths per target agent.

    Claude Code uses a project-scoped .mcp.json.
    Cursor uses project .cursor/mcp.json or global ~/.cursor/mcp.json.
    Windsurf uses ~/.codeium/windsurf/mcp_config.json.
    """
    home = Path.home()
    return {
        "claude": [project_root / ".mcp.json"],
        "cursor": [
            project_root / ".cursor" / "mcp.json",
            home / ".cursor" / "mcp.json",
        ],
        "windsurf": [home / ".codeium" / "windsurf" / "mcp_config.json"],
    }


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _merge_redcon_entry(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """
    Merge the Redcon MCP entry into the config dict.
    Returns (new_config, changed).
    """
    servers = config.setdefault("mcpServers", {})
    existing = servers.get("redcon")
    if existing == REDCON_ENTRY:
        return config, False
    servers["redcon"] = dict(REDCON_ENTRY)
    return config, True


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )


def install_for_target(target: str, project_root: Path) -> dict[str, Any]:
    """
    Install Redcon into one target agent. Uses the first writable path.
    """
    paths = _target_paths(project_root).get(target)
    if not paths:
        return {"target": target, "status": "unknown", "path": None, "message": "unknown target"}

    # Prefer an existing config file if one exists; otherwise use the first path.
    chosen: Path | None = None
    for p in paths:
        if p.exists():
            chosen = p
            break
    if chosen is None:
        chosen = paths[0]

    config = _load_config(chosen)
    new_config, changed = _merge_redcon_entry(config)
    if not changed:
        return {
            "target": target,
            "status": "up_to_date",
            "path": str(chosen),
            "message": "redcon already configured",
        }

    try:
        _write_config(chosen, new_config)
    except OSError as e:
        return {
            "target": target,
            "status": "error",
            "path": str(chosen),
            "message": f"write failed: {e}",
        }

    return {
        "target": target,
        "status": "installed",
        "path": str(chosen),
        "message": "redcon MCP server registered",
    }


def install_all(project_root: Path, targets: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Install Redcon into all (or selected) target agents.
    """
    if targets is None:
        targets = ["claude", "cursor", "windsurf"]

    results = []
    for target in targets:
        results.append(install_for_target(target, project_root))
    return results


def uninstall_for_target(target: str, project_root: Path) -> dict[str, Any]:
    """Remove the Redcon MCP entry from a target agent's config."""
    paths = _target_paths(project_root).get(target)
    if not paths:
        return {"target": target, "status": "unknown", "path": None, "message": "unknown target"}

    for path in paths:
        if not path.exists():
            continue
        config = _load_config(path)
        servers = config.get("mcpServers", {})
        if "redcon" not in servers:
            continue
        del servers["redcon"]
        # Clean up empty mcpServers dict
        if not servers:
            config.pop("mcpServers", None)
        try:
            _write_config(path, config)
        except OSError as e:
            return {
                "target": target,
                "status": "error",
                "path": str(path),
                "message": f"write failed: {e}",
            }
        return {
            "target": target,
            "status": "removed",
            "path": str(path),
            "message": "redcon MCP entry removed",
        }

    return {
        "target": target,
        "status": "not_installed",
        "path": None,
        "message": "no redcon entry found",
    }
