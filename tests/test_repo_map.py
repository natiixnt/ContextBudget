"""Tests for redcon.repo_map and the redcon_repo_map MCP tool."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from redcon.repo_map import RepoMap, build_repo_map


@pytest.fixture
def python_repo(tmp_path: Path) -> Path:
    """Tiny Python repo with two ranked files for testing."""
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True)
    (tmp_path / "auth.py").write_text(
        "class AuthMiddleware:\n"
        "    def authenticate(self, request):\n"
        "        return True\n"
        "\n"
        "def login(req):\n"
        "    return AuthMiddleware().authenticate(req)\n"
    )
    (tmp_path / "handlers.py").write_text(
        "from auth import login\n"
        "\n"
        "class TaskHandler:\n"
        "    def list_tasks(self):\n"
        "        return []\n"
        "    def create_task(self, payload):\n"
        "        return None\n"
    )
    (tmp_path / "unrelated.py").write_text(
        "def coffee_machine():\n    return 'espresso'\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True
    )
    return tmp_path


def test_build_repo_map_returns_structured_result(python_repo: Path):
    repo_map = build_repo_map(
        task="add JWT authentication middleware to login flow",
        repo=python_repo,
        budget=4_000,
        top_files=10,
    )
    assert isinstance(repo_map, RepoMap)
    assert repo_map.budget == 4_000
    assert repo_map.total_tokens <= repo_map.budget
    # The auth/handlers files should land in the map for an auth task.
    paths = [fm.path for fm in repo_map.files]
    assert any("auth.py" in p for p in paths)


def test_repo_map_text_includes_signatures_when_available(python_repo: Path):
    repo_map = build_repo_map(
        task="add authentication", repo=python_repo, budget=4_000, top_files=10
    )
    if repo_map.symbols_available:
        # When symbols are available, render must contain a class signature.
        assert "class AuthMiddleware" in repo_map.text or "AuthMiddleware" in repo_map.text


def test_repo_map_falls_back_gracefully_without_symbols(monkeypatch, python_repo: Path):
    """Force symbols off and verify the map degrades to a path-only listing."""
    import redcon.repo_map as repo_map_mod

    monkeypatch.setattr(repo_map_mod, "symbols_available", lambda: False)
    repo_map = build_repo_map(
        task="add authentication", repo=python_repo, budget=4_000, top_files=10
    )
    assert repo_map.symbols_available is False
    assert "signatures unavailable" in repo_map.text
    # Files still listed by path even without signatures.
    if repo_map.files:
        assert any(fm.path.endswith(".py") for fm in repo_map.files)


def test_repo_map_respects_budget(python_repo: Path):
    """A tight budget must keep total_tokens at or under budget."""
    # With the symbol-less fallback every path costs ~2-3 cl100k-ish
    # tokens, so budget=12 fits all three fixture files. Pick a budget
    # that genuinely cannot fit every file (~5 tokens admits 1-2 of 3)
    # so the truncation contract actually fires.
    tight = build_repo_map(
        task="add authentication", repo=python_repo, budget=5, top_files=10
    )
    assert tight.total_tokens <= tight.budget
    assert tight.truncated is True

    # Roomy budget: contract still holds, no truncation when everything fits.
    roomy = build_repo_map(
        task="add authentication", repo=python_repo, budget=4_000, top_files=10
    )
    assert roomy.total_tokens <= roomy.budget
    assert roomy.truncated is False


def test_repo_map_mcp_tool_returns_meta(python_repo: Path):
    from redcon.mcp.tools import tool_repo_map

    result = tool_repo_map(
        task="add authentication", repo=str(python_repo), budget=4_000
    )
    assert "error" not in result
    assert "_meta" in result
    assert result["_meta"]["redcon"]["tool"] == "redcon_repo_map"
    assert "files" in result
    assert "text" in result
    assert "symbols_available" in result


def test_repo_map_mcp_rejects_empty_task():
    from redcon.mcp.tools import tool_repo_map

    result = tool_repo_map(task="")
    assert "error" in result
