"""
Redcon MCP server - stdio transport for integration with Claude Code,
Cursor, Windsurf, and other MCP-compatible agents.

Exposes 6 tools:
  - redcon_rank: score and rank files by task relevance
  - redcon_overview: lightweight repo map grouped by directory
  - redcon_compress: compressed single-file content for cheap inspection
  - redcon_search: regex search scoped to ranked files or full repo
  - redcon_budget: plan file packing within a token budget
  - redcon_run: run a shell command and return its output compressed
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
    {
        "name": "redcon_structural_search",
        "description": (
            "Search code by AST pattern (ast-grep), not regex. Patterns "
            "like `class $NAME { $$$ }` match real class declarations and "
            "skip text occurrences inside comments / strings. Available "
            "when ast-grep is on PATH or redcon[ast_grep] is installed; "
            "returns backend=unavailable otherwise so callers can fall "
            "back to redcon_search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "scope": {"type": "string", "default": "."},
                "language": {"type": "string", "description": "Language hint (python, javascript, rust, ...)"},
                "max_results": {"type": "integer", "default": 200},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "redcon_repo_map",
        "description": (
            "Aider-style repo map: top ranked files plus their tree-sitter "
            "class/function signatures fitted under a token budget. "
            "Differentiates from redcon_overview by emitting actual code "
            "structure (signatures with line numbers), not just paths. "
            "When the redcon[symbols] extra is missing the map degrades "
            "to a path-only listing rather than failing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "repo": {"type": "string", "default": "."},
                "budget": {"type": "integer", "default": 8000},
                "top_files": {"type": "integer", "default": 60},
            },
            "required": ["task"],
        },
    },
    {
        "name": "redcon_quality_check",
        "description": (
            "Run a shell command, compress its output, and verify the "
            "compression against the M8 quality harness (must-preserve "
            "patterns, reduction floor, determinism). Use this instead "
            "of redcon_run when you want a verdict before consuming the "
            "compressed bytes - the response is small and the verdict is "
            "structured."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string", "default": "."},
                "max_output_tokens": {"type": "integer", "default": 4000},
                "remaining_tokens": {"type": "integer", "default": 30000},
                "quality_floor": {
                    "type": "string",
                    "enum": ["verbose", "compact", "ultra"],
                    "default": "compact",
                },
                "timeout_seconds": {"type": "integer", "default": 120},
                "prefer_compact_output": {"type": "boolean", "default": False},
            },
            "required": ["command"],
        },
    },
    {
        "name": "redcon_run",
        "description": (
            "Run a shell command (git diff/status/log and others) and return its "
            "output compressed for the LLM. Use this instead of the raw shell when "
            "the command would otherwise produce hundreds of lines of output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Full command line, e.g. 'git diff HEAD'",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory",
                    "default": ".",
                },
                "max_output_tokens": {
                    "type": "integer",
                    "description": "Hard cap on tokens returned",
                    "default": 4000,
                },
                "remaining_tokens": {
                    "type": "integer",
                    "description": "Remaining budget hint (drives compression aggressiveness)",
                    "default": 30000,
                },
                "quality_floor": {
                    "type": "string",
                    "enum": ["verbose", "compact", "ultra"],
                    "description": "Lowest acceptable detail level",
                    "default": "compact",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Kill the command after this many seconds",
                    "default": 120,
                },
                "prefer_compact_output": {
                    "type": "boolean",
                    "description": (
                        "Rewrite known commands to runner-native compact "
                        "flags (pytest --tb=line, cargo --quiet, jest "
                        "--reporter=basic) before spawning. Trades full "
                        "tracebacks for ~60-80% upstream reduction on "
                        "test-failure runs."
                    ),
                    "default": False,
                },
                "semantic_fallback": {
                    "type": "boolean",
                    "description": (
                        "Enable the LLMLingua-2 semantic compression "
                        "fallback for commands that no schema-specific "
                        "compressor recognised. Requires the optional "
                        "redcon[heavy_compression] extra (torch + "
                        "transformers + ~280 MB BERT-base checkpoint). "
                        "Silently falls through to plain passthrough "
                        "when the extra is missing."
                    ),
                    "default": False,
                },
            },
            "required": ["command"],
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
        if name == "redcon_run":
            return tools.tool_run(
                command=args.get("command", ""),
                cwd=args.get("cwd", "."),
                max_output_tokens=int(args.get("max_output_tokens", 4000)),
                remaining_tokens=int(args.get("remaining_tokens", 30000)),
                quality_floor=args.get("quality_floor", "compact"),
                timeout_seconds=int(args.get("timeout_seconds", 120)),
                prefer_compact_output=bool(args.get("prefer_compact_output", False)),
                semantic_fallback=bool(args.get("semantic_fallback", False)),
            )
        if name == "redcon_structural_search":
            return tools.tool_structural_search(
                pattern=args.get("pattern", ""),
                scope=args.get("scope", "."),
                language=args.get("language"),
                max_results=int(args.get("max_results", 200)),
            )
        if name == "redcon_repo_map":
            return tools.tool_repo_map(
                task=args.get("task", ""),
                repo=args.get("repo", "."),
                budget=int(args.get("budget", 8000)),
                top_files=int(args.get("top_files", 60)),
            )
        if name == "redcon_quality_check":
            return tools.tool_quality_check(
                command=args.get("command", ""),
                cwd=args.get("cwd", "."),
                max_output_tokens=int(args.get("max_output_tokens", 4000)),
                remaining_tokens=int(args.get("remaining_tokens", 30000)),
                quality_floor=args.get("quality_floor", "compact"),
                timeout_seconds=int(args.get("timeout_seconds", 120)),
                prefer_compact_output=bool(args.get("prefer_compact_output", False)),
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
