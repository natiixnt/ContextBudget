# MCP Server and Claude Code Hooks

redcon integrates with coding agents through two complementary
mechanisms. The MCP server is *advisory*: it gives the agent tools and
good reasons to call them, but the model decides. The Claude Code hook
is *deterministic*: redcon context is injected on every qualifying
prompt whether or not the model would have asked for it.

Both are configured automatically by `redcon init`; the commands below
manage them individually.

## MCP server

Requires the `mcp` extra:

```bash
pip install "redcon[mcp]"
```

### Registering

```bash
redcon mcp install      # register for detected agents in this project
redcon mcp status       # where is redcon registered?
redcon mcp uninstall    # remove the registrations
redcon mcp serve        # run the stdio server (agents invoke this)
```

`install` writes the appropriate config for each detected agent:
Claude Code (`.mcp.json`), Cursor (`.cursor/mcp.json`), Windsurf,
VS Code (`.vscode/mcp.json`), Codex (`~/.codex/config.toml`) and
Gemini (`~/.gemini/settings.json`). Restart the IDE or agent session
after installing so it picks up the new tools.

### Tools exposed

| Tool | What it does |
| --- | --- |
| `redcon_rank` | rank repository files by relevance to the task |
| `redcon_overview` | lightweight repo map grouped by directory |
| `redcon_compress` | compressed single-file content for cheap inspection |
| `redcon_search` | regex search scoped to ranked files or the full repo |
| `redcon_structural_search` | ast-grep structural code search |
| `redcon_repo_map` | symbol-level repository map (tree-sitter) |
| `redcon_quality_check` | compression quality gate for a command output |
| `redcon_budget` | plan file packing within a token budget |
| `redcon_run` | run a shell command and return its output compressed |

Every pack triggered through MCP lands in `.redcon/runs/`, so the
VS Code extension picks it up automatically.

## Claude Code hooks

No extra dependencies needed:

```bash
redcon hooks install    # writes a UserPromptSubmit hook to .claude/settings.json
redcon hooks status     # is the hook registered?
redcon hooks uninstall  # remove it
```

### What the hook does

On every prompt submitted to Claude Code, `redcon hooks run
user-prompt-submit` runs a fast file ranking (`run_plan`, top 8 files)
and injects a compact `<redcon-context>` block with the most relevant
files. The agent starts every task already knowing where to look,
without reading the repository first.

Guardrails, all deterministic:

- prompts shorter than 20 characters are skipped (greetings, "ok")
- slash commands (`/...`) and memory notes (`#...`) are skipped
- the injected block is capped at 2400 characters
- the hook is fail-open: any error exits 0 with no output, so a broken
  hook can never block your prompt
- set `REDCON_HOOK_DISABLE=1` to turn injection off without
  uninstalling

### Removing safely

`redcon hooks uninstall` only removes the redcon entry from
`.claude/settings.json`; it refuses to touch a file it cannot parse and
leaves all other hooks intact.

## Which one do I want?

Both. MCP gives the agent on-demand tools for search, compression and
budgeting mid-task; the hook guarantees a relevance-ranked starting
point on every prompt. They share the same config (`redcon.toml`) and
the same run feed, and `redcon doctor` verifies both setups.
