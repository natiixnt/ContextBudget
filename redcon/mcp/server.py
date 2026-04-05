"""
Redcon MCP server - stdio transport for integration with Claude Code,
Cursor, Windsurf, and other MCP-compatible agents.

Exposes 5 tools that wrap RedconEngine:
  - redcon_rank: score and rank files by task relevance
  - redcon_overview: lightweight repo map grouped by directory
  - redcon_compress: compressed single-file content for cheap inspection
  - redcon_search: regex search scoped to ranked files or full repo
  - redcon_budget: plan file packing within a token budget
"""

from __future__ import annotations

import json
import logging
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    Server = None  # type: ignore
    stdio_server = None  # type: ignore
    types = None  # type: ignore

from redcon.mcp import tools

logger = logging.getLogger(__name__)


_TOOL_SCHEMAS = [
    {
        "name": "redcon_rank",
        "description": (
            "Rank repository files by relevance to the current task. Returns "
            "top-K files with scores and reasons. Call this FIRST when starting "
            "a new task to understand where to focus."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Description of what you're working on",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository path (default: current directory)",
                    "default": ".",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of top files to return",
                    "default": 25,
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "redcon_overview",
        "description": (
            "Get a lightweight repository map grouped by directory, showing "
            "relevant modules for the task. Much cheaper than ls -R."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task description"},
                "repo": {"type": "string", "default": "."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "redcon_compress",
        "description": (
            "Return compressed version of a file scoped to the task. "
            "Use this to inspect many files cheaply without reading full contents."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file",
                },
                "task": {"type": "string", "description": "Task description"},
                "repo": {"type": "string", "default": "."},
                "max_tokens": {
                    "type": "integer",
                    "description": "Max tokens for compressed output",
                    "default": 2000,
                },
            },
            "required": ["path", "task"],
        },
    },
    {
        "name": "redcon_search",
        "description": (
            "Regex search within ranked files (scope='ranked') or the full "
            "repository (scope='all'). Scoped search is much faster and more "
            "focused than ripgrep."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "task": {
                    "type": "string",
                    "description": "Task description (used for scope='ranked')",
                },
                "repo": {"type": "string", "default": "."},
                "scope": {
                    "type": "string",
                    "enum": ["ranked", "all"],
                    "default": "ranked",
                },
                "top_k": {"type": "integer", "default": 25},
                "max_results": {"type": "integer", "default": 50},
            },
            "required": ["pattern", "task"],
        },
    },
    {
        "name": "redcon_budget",
        "description": (
            "Plan how to fit requested files within a token budget, selecting "
            "compression strategies per file. Returns a plan with token counts "
            "and any files that had to be dropped."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Relative paths of files to fit",
                },
                "task": {"type": "string", "description": "Task description"},
                "max_tokens": {
                    "type": "integer",
                    "description": "Total token budget",
                },
                "repo": {"type": "string", "default": "."},
            },
            "required": ["files", "task", "max_tokens"],
        },
    },
]


def _dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route a tool call to the appropriate handler."""
    try:
        if name == "redcon_rank":
            return tools.tool_rank(
                task=args.get("task", ""),
                repo=args.get("repo", "."),
                top_k=int(args.get("top_k", 25)),
            )
        if name == "redcon_overview":
            return tools.tool_overview(
                task=args.get("task", ""),
                repo=args.get("repo", "."),
            )
        if name == "redcon_compress":
            return tools.tool_compress(
                path=args.get("path", ""),
                task=args.get("task", ""),
                repo=args.get("repo", "."),
                max_tokens=int(args.get("max_tokens", 2000)),
            )
        if name == "redcon_search":
            return tools.tool_search(
                pattern=args.get("pattern", ""),
                task=args.get("task", ""),
                repo=args.get("repo", "."),
                scope=args.get("scope", "ranked"),
                top_k=int(args.get("top_k", 25)),
                max_results=int(args.get("max_results", 50)),
            )
        if name == "redcon_budget":
            return tools.tool_budget(
                files=args.get("files", []),
                task=args.get("task", ""),
                max_tokens=int(args.get("max_tokens", 8000)),
                repo=args.get("repo", "."),
            )
        return {"error": f"unknown tool: {name}"}
    except Exception as e:
        logger.exception("tool dispatch failed: %s", name)
        return {"error": str(e)}


def create_server() -> Any:
    """Build and return a configured MCP server instance."""
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            "mcp package is not installed. Run: pip install redcon[mcp]"
        )

    server = Server("redcon")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            types.Tool(
                name=schema["name"],
                description=schema["description"],
                inputSchema=schema["inputSchema"],
            )
            for schema in _TOOL_SCHEMAS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        result = _dispatch_tool(name, arguments or {})
        text = json.dumps(result, indent=2, default=str)
        return [types.TextContent(type="text", text=text)]

    return server


async def serve() -> None:
    """Run the MCP server over stdio transport."""
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            "mcp package is not installed. Run: pip install redcon[mcp]"
        )

    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
