# Getting Started

## Install

```bash
pip install redcon
```

Useful extras (see [Optional extras](#optional-extras) below):

```bash
pip install "redcon[mcp]"          # MCP server for coding agents
pip install "redcon[tokenizers]"   # exact token counts via tiktoken
```

Working on redcon itself? Use an editable install from a checkout instead:

```bash
git clone https://github.com/natiixnt/redcon
cd redcon
python3 -m pip install -e .[dev]
```

## First pack in 60 seconds

```bash
cd your-project

# One-command setup: writes redcon.toml, registers the MCP server for
# detected agents (Claude Code, Cursor, Windsurf, VS Code, Codex,
# Gemini) and updates AGENTS.md.
redcon init

# Check the environment (optional deps, MCP registration, config)
redcon doctor

# Rank relevant files
redcon plan "add caching to search API" --repo .

# Pack context under budget
redcon pack "add caching to search API" --repo . --max-tokens 30000

# Summarize run artifact
redcon report run.json
```

## Agent integration

Two mechanisms make agents use redcon automatically:

- **MCP server** (`redcon mcp install` / `status` / `uninstall`): exposes
  redcon tools (rank, overview, compress, search, budget, run) to any
  MCP-compatible agent. See [MCP and hooks](mcp-and-hooks.md).
- **Claude Code hooks** (`redcon hooks install`): deterministic context
  injection on every prompt, no reliance on the model choosing to call a
  tool. See [MCP and hooks](mcp-and-hooks.md).

`redcon init` sets both up for detected agents; the VS Code extension
mirrors every run into its dashboard automatically.

## Extended Workflow

```bash
# Compare two runs
redcon diff old-run.json new-run.json

# Compare packing strategies
redcon benchmark "add rate limiting to auth API" --repo .
```

## Optional extras

| Extra | Installs | Unlocks |
| --- | --- | --- |
| `mcp` | `mcp` | `redcon mcp serve` - the MCP server for coding agents |
| `tokenizers` | `tiktoken` | exact token counts instead of the heuristic estimator |
| `symbols` | `tree-sitter` + language packs | symbol extraction for repo-map and better file context |
| `ast_grep` | `ast-grep-py` | structural code search fallback (CLI binary preferred) |
| `redis` | `redis` | shared cache backend |
| `gateway` | `fastapi`, `uvicorn` | `redcon-gateway` HTTP runtime |
| `heavy_compression` | `llmlingua` | LLMLingua-2 semantic compression fallback (heavy: pulls torch) |
| `dev` | `pytest`, `fakeredis` | running the test suite |

```bash
pip install "redcon[mcp,tokenizers,symbols]"
```

## Example Repositories

See commands and fixtures in [`examples/README.md`](../examples/README.md).
