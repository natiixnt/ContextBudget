"""Tests for redcon.structural_search and the redcon_structural_search MCP tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from redcon.structural_search import (
    StructuralSearchResult,
    is_available,
    render_text,
    reset_availability_for_testing,
    structural_search,
)


def test_is_available_returns_known_backend():
    backend = is_available()
    assert backend in {"binary", "python_wheel", "unavailable"}


def test_unavailable_backend_returns_empty_result(monkeypatch, tmp_path: Path):
    """Force the backend to unavailable and verify graceful degradation."""
    import redcon.structural_search as ss

    reset_availability_for_testing()
    monkeypatch.setattr(ss, "shutil", _no_path_shutil())
    # Also stub ast_grep_py import to fail.
    import builtins

    real_import = builtins.__import__

    def stub(name, *args, **kwargs):
        if name == "ast_grep_py":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", stub)
    reset_availability_for_testing()

    result = structural_search("class $NAME { $$$ }", scope=tmp_path, language="python")
    assert result.backend == "unavailable"
    assert result.match_count == 0
    assert result.matches == ()


def test_render_text_unavailable_includes_install_hint():
    result = StructuralSearchResult(
        pattern="x", language=None, scope=".",
        backend="unavailable", match_count=0, file_count=0, matches=(),
    )
    text = render_text(result)
    assert "install" in text.lower() or "unavailable" in text.lower()


def test_render_text_no_matches():
    result = StructuralSearchResult(
        pattern="x", language="python", scope=".",
        backend="binary", match_count=0, file_count=0, matches=(),
    )
    text = render_text(result)
    assert "no matches" in text


def test_mcp_structural_search_returns_meta():
    from redcon.mcp.tools import tool_structural_search

    result = tool_structural_search(pattern="class $NAME { $$$ }", scope=".", language="python")
    assert "_meta" in result
    assert result["_meta"]["redcon"]["tool"] == "redcon_structural_search"
    assert "backend" in result
    assert result["backend"] in {"binary", "python_wheel", "unavailable"}


def test_mcp_structural_search_rejects_empty_pattern():
    from redcon.mcp.tools import tool_structural_search

    result = tool_structural_search(pattern="")
    assert "error" in result


# --- helpers ---


class _NoPathShutil:
    """A shutil-like stub whose .which always returns None."""

    @staticmethod
    def which(_name: str) -> None:
        return None


def _no_path_shutil():
    return _NoPathShutil()
