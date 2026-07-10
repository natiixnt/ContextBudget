"""
Write agent instruction blocks so coding agents actually use redcon.

Registering the MCP server gives an agent the tools; it does not make
the agent prefer them over its own grep and whole-file reads. A short
instruction block in the repo's agent guidance files closes that gap.

AGENTS.md is the cross-agent convention (Codex, Cursor, Gemini CLI and
others read it) and is created when missing. CLAUDE.md belongs to the
user, so redcon only appends to it when the file already exists.

The block is delimited by marker comments and rewritten in place, so
repeated runs are idempotent and user content around it is preserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_BEGIN = "<!-- redcon:begin -->"
_END = "<!-- redcon:end -->"

INSTRUCTIONS_BLOCK = f"""{_BEGIN}
## Repository context via redcon

This repo exposes the "redcon" MCP server for token-budgeted context
selection. When you need repository context:

- Use `redcon_rank` with the task description to find the most relevant
  files instead of guessing paths or searching blindly.
- Use `redcon_compress` to read a file's compressed form before deciding
  whether the full file is worth the tokens.
- Use `redcon_search` for regex search scoped to the ranked files.
- Use `redcon_budget` to plan what fits under a token budget before
  reading many files.

Prefer these tools over broad directory dumps or reading whole large
files.
{_END}"""

# (filename, create when missing)
_TARGET_FILES: list[tuple[str, bool]] = [
    ("AGENTS.md", True),
    ("CLAUDE.md", False),
]


def ensure_agent_instructions(project_root: Path) -> list[dict[str, Any]]:
    """Install or refresh the redcon block in agent guidance files."""
    results: list[dict[str, Any]] = []
    for name, create_missing in _TARGET_FILES:
        path = project_root / name
        if not path.exists() and not create_missing:
            results.append(
                {
                    "file": name,
                    "status": "skipped",
                    "path": str(path),
                    "message": f"{name} does not exist; not creating it",
                }
            )
            continue

        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            text = ""

        if _BEGIN in text and _END in text:
            start = text.index(_BEGIN)
            end = text.index(_END) + len(_END)
            if text[start:end] == INSTRUCTIONS_BLOCK:
                results.append(
                    {
                        "file": name,
                        "status": "up_to_date",
                        "path": str(path),
                        "message": "redcon instructions already present",
                    }
                )
                continue
            new_text = text[:start] + INSTRUCTIONS_BLOCK + text[end:]
            status = "updated"
        elif text.strip():
            new_text = text.rstrip() + "\n\n" + INSTRUCTIONS_BLOCK + "\n"
            status = "installed"
        else:
            new_text = INSTRUCTIONS_BLOCK + "\n"
            status = "created"

        try:
            path.write_text(new_text, encoding="utf-8")
        except OSError as e:
            results.append(
                {
                    "file": name,
                    "status": "error",
                    "path": str(path),
                    "message": f"write failed: {e}",
                }
            )
            continue

        results.append(
            {
                "file": name,
                "status": status,
                "path": str(path),
                "message": "redcon instructions written",
            }
        )
    return results
