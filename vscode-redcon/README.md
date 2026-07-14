# Redcon - Context Budget for VS Code

Deterministic context budgeting for AI coding agents. Rank, compress, and pack repository context under explicit token limits - and watch how many tokens (and dollars) redcon saves you, directly in your editor.

![VS Code](https://img.shields.io/badge/VS%20Code-1.85+-blue)
![License](https://img.shields.io/badge/license-FSL--1.1--MIT-blue)

![Redcon Analytics dashboard](https://raw.githubusercontent.com/natiixnt/redcon/main/vscode-redcon/media/dashboard-dark.png)

---

## Features

### Savings panel

The activity bar view leads with what matters: cumulative tokens and
dollars saved, a per-run trend, and your last run - one click opens the
full dashboard. Below it, a live run feed: every run shows the tokens
that actually went to the agent against the gray would-be total, the
cut percentage, age and a risk dot. Click any run to inspect it.

<img src="https://raw.githubusercontent.com/natiixnt/redcon/main/vscode-redcon/media/panel-dark.png" width="330" alt="Redcon sidebar panel" />

### Plug and play: agent runs appear by themselves

Runs arrive automatically. Whenever an agent (Claude Code, Cursor,
Windsurf, Codex, Gemini) or the CLI packs context, redcon mirrors the
run report into `.redcon/runs/` and the extension picks it up live -
panel, status bar and dashboard update with zero clicking. Manual packs
live in the view title bar and the command palette.

### Analytics dashboard

Brand-new in 0.9.0, implementing the Redcon Analytics design:

- cumulative savings hero with per-run trend and dollar estimate
- KPI cards with budget threshold ticks and quality risk states
- budget utilization and strategy share donuts
- shared-scale token impact chart (packed vs saved per file)
- packed context and file rankings tables
- light and dark theme aware; six data accent presets

### Status bar, decorations, CodeLens

- live token usage and tokens saved in the status bar
- relevance score badges on files in the Explorer
- compression strategy and savings above each packed file:
  `Redcon: snippet | 400/1200 tok (-67%)`

## Getting started

1. Install this extension.
2. Install the CLI: `pip install redcon`
3. In your project: `redcon init` - one command registers the MCP
   server for your agents, installs hooks and writes `redcon.toml`.
   (The extension's setup checklist can do this for you too.)
4. Work normally. Runs land in the panel by themselves.

## Commands

| Command | What it does |
| --- | --- |
| `Redcon: Pack Context` | pack context for a task under the token budget |
| `Redcon: Plan - Rank Files` | rank files by task relevance |
| `Redcon: Plan Agent Workflow` | plan context across a multi-step workflow |
| `Redcon: Open Dashboard` | open the analytics dashboard |
| `Redcon: Doctor - Check Environment` | diagnose CLI, extras and MCP registration |
| `Redcon: Initialize Config` | run `redcon init` |
| `Redcon: Copy Context to Clipboard` | copy the packed context |
| `Redcon: Sync Context to Agents` | write context files for agents |
| `Redcon: Export Context` | export the packed context to a file |
| `Redcon: Benchmark Strategies` | compare packing strategies |
| `Redcon: Simulate Agent Cost` | estimate workflow token cost |
| `Redcon: Check Token Drift` | compare token estimates across runs |
| `Redcon: Install Redcon & Set Up MCP` | guided CLI install |
| `Redcon: Register MCP Server` | register MCP for detected agents |
| `Redcon: Open Configuration` | open `redcon.toml` |
| `Redcon: Quick Start Guide` | open the docs |

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `redcon.cliCommand` | `redcon` | CLI command or path to executable |
| `redcon.pythonPath` | `python` | Python used by the guided installer |
| `redcon.defaultMaxTokens` | `30000` | token budget for pack commands |
| `redcon.defaultTopFiles` | `25` | maximum number of ranked files |
| `redcon.configPath` | auto | path to `redcon.toml` |
| `redcon.autoRefreshOnSave` | `false` | re-run plan on save |
| `redcon.showStatusBar` | `true` | budget gauge in the status bar |
| `redcon.showFileDecorations` | `true` | score badges in the Explorer |
| `redcon.showCodeLens` | `true` | strategy CodeLens above files |
| `redcon.costPerMillionTokens` | `3.0` | USD per million input tokens for dollar estimates |
| `redcon.display.primaryMetric` | `tokens` | leading number: `tokens` or `dollars` |
| `redcon.display.dataAccent` | `red` | data mark accent: red, blue, violet, crimson, wine, gradient |
| `redcon.budget.policy` | `auto-raise` | budget policy shown on the dashboard: auto-raise, strict-cap, ask-first |
| `redcon.views.showMiniDashboard` | `true` | savings card on top of the sidebar |
| `redcon.views.showRecentRuns` | `true` | run feed in the sidebar |
| `redcon.views.showSetup` | `true` | setup checklist while setup is incomplete |
| `redcon.dashboard.showKpis` | `true` | KPI cards row |
| `redcon.dashboard.showDonuts` | `true` | budget and strategy donuts |
| `redcon.dashboard.showImpact` | `true` | token impact chart |
| `redcon.dashboard.showTables` | `true` | packed context and rankings tables |
| `redcon.contextSync.enabled` | `true` | generate agent context files after runs |
| `redcon.contextSync.autoSyncOnPack` | `true` | sync automatically after each pack |
| `redcon.contextSync.targets` | claude, cursor, copilot | which agents get context files |
| `redcon.contextSync.maxFiles` | `30` | max files in the context map |

## Requirements

- VS Code 1.85+
- Python 3.10+ with `pip install redcon` (the setup checklist can
  install it for you)
- A trusted workspace (the extension runs the CLI against your files)

## Links

- [Repository](https://github.com/natiixnt/redcon)
- [CLI documentation](https://github.com/natiixnt/redcon/tree/main/docs)
- [Issues](https://github.com/natiixnt/redcon/issues)
