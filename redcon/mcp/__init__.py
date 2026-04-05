"""
Redcon MCP server - exposes ranking, compression, and search as MCP tools.

Agents like Claude Code can call these tools to pull targeted context
instead of receiving a monolithic blob, using 5x fewer tokens in typical tasks.
"""

from redcon.mcp.server import create_server, serve

__all__ = ["create_server", "serve"]
