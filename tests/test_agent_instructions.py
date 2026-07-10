"""Tests for the agent instruction blocks written by redcon init / mcp install."""

from __future__ import annotations

from pathlib import Path

from redcon.mcp.instructions import (
    INSTRUCTIONS_BLOCK,
    ensure_agent_instructions,
)


def _statuses(results: list[dict]) -> dict[str, str]:
    return {r["file"]: r["status"] for r in results}


def test_creates_agents_md_but_not_claude_md(tmp_path: Path):
    """AGENTS.md is created; CLAUDE.md belongs to the user and is not."""
    results = ensure_agent_instructions(tmp_path)
    statuses = _statuses(results)

    assert statuses["AGENTS.md"] == "created"
    assert statuses["CLAUDE.md"] == "skipped"
    assert (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()

    text = (tmp_path / "AGENTS.md").read_text()
    assert "redcon_rank" in text
    assert "<!-- redcon:begin -->" in text
    assert "<!-- redcon:end -->" in text


def test_appends_to_existing_files_preserving_content(tmp_path: Path):
    """Existing guidance files keep their content; the block is appended."""
    (tmp_path / "AGENTS.md").write_text("# My rules\n\nAlways run tests.\n")
    (tmp_path / "CLAUDE.md").write_text("# Project notes\n")

    statuses = _statuses(ensure_agent_instructions(tmp_path))
    assert statuses["AGENTS.md"] == "installed"
    assert statuses["CLAUDE.md"] == "installed"

    agents = (tmp_path / "AGENTS.md").read_text()
    assert agents.startswith("# My rules")
    assert "Always run tests." in agents
    assert "redcon_budget" in agents

    claude = (tmp_path / "CLAUDE.md").read_text()
    assert claude.startswith("# Project notes")
    assert "redcon_rank" in claude


def test_repeated_runs_are_idempotent(tmp_path: Path):
    """A second run changes nothing and reports up_to_date."""
    ensure_agent_instructions(tmp_path)
    before = (tmp_path / "AGENTS.md").read_text()

    statuses = _statuses(ensure_agent_instructions(tmp_path))
    assert statuses["AGENTS.md"] == "up_to_date"
    assert (tmp_path / "AGENTS.md").read_text() == before


def test_stale_block_is_replaced_in_place(tmp_path: Path):
    """An outdated block between the markers is rewritten, not duplicated."""
    stale = "# Intro\n\n<!-- redcon:begin -->\nold instructions\n<!-- redcon:end -->\n\n# Outro\n"
    (tmp_path / "AGENTS.md").write_text(stale)

    statuses = _statuses(ensure_agent_instructions(tmp_path))
    assert statuses["AGENTS.md"] == "updated"

    text = (tmp_path / "AGENTS.md").read_text()
    assert "old instructions" not in text
    assert text.count("<!-- redcon:begin -->") == 1
    assert text.startswith("# Intro")
    assert "# Outro" in text
    assert INSTRUCTIONS_BLOCK in text
