"""Tests for the Redcon MCP server."""

from __future__ import annotations

import pytest

from redcon.mcp import tools


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the rank cache before each test."""
    tools.clear_cache()
    yield
    tools.clear_cache()


def test_rank_returns_files(tmp_path):
    """rank should return ranked files for a given task."""
    # Create a minimal repo
    (tmp_path / "auth.py").write_text("def login(user, password): return True\n")
    (tmp_path / "db.py").write_text("def connect(): pass\n")

    result = tools.tool_rank(task="user login", repo=str(tmp_path), top_k=5)
    assert "error" not in result
    assert "files" in result
    assert result["top_k"] == 5
    assert result["from_cache"] is False


def test_rank_uses_cache_on_second_call(tmp_path):
    """Second rank call with same task should hit the cache."""
    (tmp_path / "a.py").write_text("x = 1\n")

    first = tools.tool_rank(task="test", repo=str(tmp_path), top_k=5)
    second = tools.tool_rank(task="test", repo=str(tmp_path), top_k=5)
    assert first["from_cache"] is False
    assert second["from_cache"] is True


def test_rank_rejects_empty_task():
    result = tools.tool_rank(task="", repo=".", top_k=5)
    assert "error" in result


def test_rank_rejects_negative_top_k():
    result = tools.tool_rank(task="x", repo=".", top_k=0)
    assert "error" in result


def test_overview_returns_modules(tmp_path):
    """overview should group ranked files by directory."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login(): pass\n")
    (tmp_path / "src" / "db.py").write_text("def connect(): pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_auth.py").write_text("def test_login(): pass\n")

    result = tools.tool_overview(task="authentication", repo=str(tmp_path))
    assert "error" not in result
    assert "modules" in result
    assert isinstance(result["modules"], list)


def test_overview_rejects_empty_task():
    result = tools.tool_overview(task="  ", repo=".")
    assert "error" in result


def test_search_rejects_empty_pattern():
    result = tools.tool_search(pattern="", task="x", repo=".", scope="ranked")
    assert "error" in result


def test_search_rejects_invalid_scope():
    result = tools.tool_search(pattern="x", task="x", repo=".", scope="bogus")
    assert "error" in result


def test_search_rejects_invalid_regex():
    result = tools.tool_search(pattern="[unclosed", task="x", repo=".", scope="ranked")
    assert "error" in result
    assert "invalid regex" in result["error"]


def test_search_finds_pattern_in_ranked(tmp_path):
    """search scope='ranked' should find patterns in the ranked files."""
    (tmp_path / "auth.py").write_text(
        "def authenticate(user, password):\n    return True\n"
    )
    (tmp_path / "db.py").write_text("def connect(): pass\n")

    result = tools.tool_search(
        pattern="def authenticate",
        task="user authentication",
        repo=str(tmp_path),
        scope="ranked",
        top_k=10,
    )
    assert "error" not in result
    assert result["match_count"] >= 1
    assert any("auth.py" in m["path"] for m in result["matches"])


def test_search_all_scope(tmp_path):
    """search scope='all' walks the full repo."""
    (tmp_path / "foo.py").write_text("BANANA = 42\n")
    (tmp_path / "bar.py").write_text("APPLE = 1\n")

    result = tools.tool_search(
        pattern="BANANA",
        task="fruit",
        repo=str(tmp_path),
        scope="all",
        max_results=10,
    )
    assert "error" not in result
    assert result["match_count"] >= 1


def test_compress_rejects_empty_path():
    result = tools.tool_compress(path="", task="x", repo=".")
    assert "error" in result


def test_compress_rejects_zero_max_tokens():
    result = tools.tool_compress(path="file.py", task="x", repo=".", max_tokens=0)
    assert "error" in result


def test_budget_rejects_empty_files():
    result = tools.tool_budget(files=[], task="x", max_tokens=1000, repo=".")
    assert "error" in result


def test_budget_rejects_zero_max_tokens():
    result = tools.tool_budget(files=["a.py"], task="x", max_tokens=0, repo=".")
    assert "error" in result


def test_budget_returns_plan(tmp_path):
    """budget should return a plan for fitting files within a token budget."""
    (tmp_path / "auth.py").write_text(
        "def login(user, password):\n    return True\n" * 5
    )

    result = tools.tool_budget(
        files=["auth.py"],
        task="login",
        max_tokens=5000,
        repo=str(tmp_path),
    )
    assert "error" not in result
    assert "plan" in result
    assert "total_tokens" in result


def test_server_creation():
    """The MCP server should be constructible when mcp is installed."""
    from redcon.mcp.server import create_server, _MCP_AVAILABLE, _TOOL_SCHEMAS

    if not _MCP_AVAILABLE:
        pytest.skip("mcp package not installed")

    assert len(_TOOL_SCHEMAS) == 5
    names = [s["name"] for s in _TOOL_SCHEMAS]
    assert "redcon_rank" in names
    assert "redcon_overview" in names
    assert "redcon_compress" in names
    assert "redcon_search" in names
    assert "redcon_budget" in names

    server = create_server()
    assert server.name == "redcon"


def test_dispatch_unknown_tool():
    """Dispatching an unknown tool name returns an error."""
    from redcon.mcp.server import _dispatch_tool

    result = _dispatch_tool("bogus_tool", {})
    assert "error" in result
    assert "unknown tool" in result["error"]


def test_dispatch_rank(tmp_path):
    """Dispatcher routes to tool_rank correctly."""
    from redcon.mcp.server import _dispatch_tool

    (tmp_path / "x.py").write_text("x = 1\n")
    result = _dispatch_tool("redcon_rank", {
        "task": "test", "repo": str(tmp_path), "top_k": 5,
    })
    assert "error" not in result
    assert "files" in result
