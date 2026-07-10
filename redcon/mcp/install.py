"""
Auto-install Redcon MCP server config into supported AI agents.

Covers Claude Code, Cursor, Windsurf, VS Code, Codex CLI and Gemini CLI.
JSON based agents get the redcon entry merged into their MCP config file
(VS Code uses a "servers" key and a stdio type marker instead of the
"mcpServers" shape the others share). Codex CLI is configured through
TOML, where the redcon section is appended or removed textually so the
rest of the user's config is never rewritten.

All writes are idempotent and preserve existing entries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REDCON_ENTRY: dict[str, Any] = {
    "command": "redcon",
    "args": ["mcp", "serve"],
}

# VS Code's .vscode/mcp.json schema wants an explicit transport type.
_VSCODE_ENTRY: dict[str, Any] = {"type": "stdio", **REDCON_ENTRY}

_CODEX_HEADER = "[mcp_servers.redcon]"
_CODEX_SECTION = '[mcp_servers.redcon]\ncommand = "redcon"\nargs = ["mcp", "serve"]\n'

# Targets registered unconditionally, matching the original behavior.
DEFAULT_TARGETS = ["claude", "cursor", "windsurf"]

# Targets registered only when their config location already exists, so
# `redcon init` does not scatter config for agents the user never
# installed. Explicitly naming the target still forces the install.
DETECTED_TARGETS = ["vscode", "codex", "gemini"]

ALL_TARGETS = DEFAULT_TARGETS + DETECTED_TARGETS

# Key holding the server map inside each JSON config format.
_SERVERS_KEY: dict[str, str] = {
    "claude": "mcpServers",
    "cursor": "mcpServers",
    "windsurf": "mcpServers",
    "gemini": "mcpServers",
    "vscode": "servers",
}


def _entry_for(target: str) -> dict[str, Any]:
    return dict(_VSCODE_ENTRY) if target == "vscode" else dict(REDCON_ENTRY)


def _target_paths(project_root: Path) -> dict[str, list[Path]]:
    """
    Return the candidate MCP config paths per target agent.

    Claude Code uses a project-scoped .mcp.json.
    Cursor uses project .cursor/mcp.json or global ~/.cursor/mcp.json.
    Windsurf uses ~/.codeium/windsurf/mcp_config.json.
    VS Code uses a project-scoped .vscode/mcp.json.
    Codex CLI uses ~/.codex/config.toml.
    Gemini CLI uses ~/.gemini/settings.json.
    """
    home = Path.home()
    return {
        "claude": [project_root / ".mcp.json"],
        "cursor": [
            project_root / ".cursor" / "mcp.json",
            home / ".cursor" / "mcp.json",
        ],
        "windsurf": [home / ".codeium" / "windsurf" / "mcp_config.json"],
        "vscode": [project_root / ".vscode" / "mcp.json"],
        "codex": [home / ".codex" / "config.toml"],
        "gemini": [home / ".gemini" / "settings.json"],
    }


def detect_targets(project_root: Path) -> list[str]:
    """Default targets plus any detected agent whose config home exists."""
    home = Path.home()
    targets = list(DEFAULT_TARGETS)
    if (project_root / ".vscode").is_dir():
        targets.append("vscode")
    if (home / ".codex").is_dir():
        targets.append("codex")
    if (home / ".gemini").is_dir():
        targets.append("gemini")
    return targets


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _merge_redcon_entry(
    config: dict[str, Any],
    servers_key: str = "mcpServers",
    entry: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """
    Merge the Redcon MCP entry into the config dict.
    Returns (new_config, changed).
    """
    wanted = entry if entry is not None else REDCON_ENTRY
    servers = config.setdefault(servers_key, {})
    existing = servers.get("redcon")
    if existing == wanted:
        return config, False
    servers["redcon"] = dict(wanted)
    return config, True


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )


def _install_codex(path: Path) -> dict[str, Any]:
    """Append the redcon section to Codex's config.toml if missing."""
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        text = ""
    if _CODEX_HEADER in text:
        return {
            "target": "codex",
            "status": "up_to_date",
            "path": str(path),
            "message": "redcon already configured",
        }
    new_text = text
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    if new_text.strip():
        new_text += "\n"
    new_text += _CODEX_SECTION
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return {
            "target": "codex",
            "status": "error",
            "path": str(path),
            "message": f"write failed: {e}",
        }
    return {
        "target": "codex",
        "status": "installed",
        "path": str(path),
        "message": "redcon MCP server registered",
    }


def _uninstall_codex(path: Path) -> dict[str, Any] | None:
    """Remove the redcon section from Codex's config.toml. None if absent."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if _CODEX_HEADER not in text:
        return None
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == _CODEX_HEADER:
            in_section = True
            continue
        if in_section and stripped.startswith("["):
            in_section = False
        if not in_section:
            out.append(line)
    try:
        path.write_text("".join(out).rstrip() + "\n", encoding="utf-8")
    except OSError as e:
        return {
            "target": "codex",
            "status": "error",
            "path": str(path),
            "message": f"write failed: {e}",
        }
    return {
        "target": "codex",
        "status": "removed",
        "path": str(path),
        "message": "redcon MCP entry removed",
    }


def installed_path(target: str, project_root: Path) -> Path | None:
    """Return the config path where redcon is registered, if any."""
    paths = _target_paths(project_root).get(target, [])
    for path in paths:
        if not path.exists():
            continue
        if target == "codex":
            try:
                if _CODEX_HEADER in path.read_text(encoding="utf-8"):
                    return path
            except OSError:
                continue
        else:
            servers_key = _SERVERS_KEY.get(target, "mcpServers")
            if "redcon" in _load_config(path).get(servers_key, {}):
                return path
    return None


def install_for_target(target: str, project_root: Path) -> dict[str, Any]:
    """
    Install Redcon into one target agent. Uses the first writable path.
    """
    paths = _target_paths(project_root).get(target)
    if not paths:
        return {"target": target, "status": "unknown", "path": None, "message": "unknown target"}

    if target == "codex":
        return _install_codex(paths[0])

    # Prefer an existing config file if one exists; otherwise use the first path.
    chosen: Path | None = None
    for p in paths:
        if p.exists():
            chosen = p
            break
    if chosen is None:
        chosen = paths[0]

    servers_key = _SERVERS_KEY.get(target, "mcpServers")
    config = _load_config(chosen)
    new_config, changed = _merge_redcon_entry(config, servers_key, _entry_for(target))
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
    Install Redcon into all detected (or explicitly selected) target agents.
    """
    if targets is None:
        targets = detect_targets(project_root)

    results = []
    for target in targets:
        results.append(install_for_target(target, project_root))
    return results


def uninstall_for_target(target: str, project_root: Path) -> dict[str, Any]:
    """Remove the Redcon MCP entry from a target agent's config."""
    paths = _target_paths(project_root).get(target)
    if not paths:
        return {"target": target, "status": "unknown", "path": None, "message": "unknown target"}

    if target == "codex":
        result = _uninstall_codex(paths[0])
        if result is not None:
            return result
        return {
            "target": target,
            "status": "not_installed",
            "path": None,
            "message": "no redcon entry found",
        }

    servers_key = _SERVERS_KEY.get(target, "mcpServers")
    for path in paths:
        if not path.exists():
            continue
        config = _load_config(path)
        servers = config.get(servers_key, {})
        if "redcon" not in servers:
            continue
        del servers["redcon"]
        # Clean up empty server dict
        if not servers:
            config.pop(servers_key, None)
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
