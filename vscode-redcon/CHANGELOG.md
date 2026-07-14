# Changelog

## 0.9.1 - 2026-07-14

### Changed

- Marketplace icon is now the actual redcon mark: the white triple
  chevron from the brand logo on the brand gradient, replacing the
  older generic icon.

## 0.9.0 - 2026-07-11

### Added

- Brand new analytics dashboard implementing the Redcon Analytics
  design handoff: brand gradient banner with the redcon lockup, a
  cumulative savings hero with per-run trend, four KPI cards with
  budget threshold ticks and risk states, donut panels for budget and
  strategy share, a shared-scale token impact chart and side-by-side
  packed context / file rankings tables.
- New settings, all live-reactive:
  - `redcon.display.primaryMetric` (`tokens` | `dollars`) swaps which
    number leads in the hero and the saved-this-run KPI.
  - `redcon.budget.policy` (`auto-raise` | `strict-cap` | `ask-first`)
    drives the budget card footnote and the high-risk note.
  - `redcon.display.dataAccent` (`red` default, `blue`, `violet`,
    `crimson`, `wine`, `gradient`) recolors all data marks; chrome red
    and status colors stay fixed.
  - `redcon.costPerMillionTokens` converts saved tokens into dollars.
- Status bar shows tokens saved next to budget usage after a run.
- The chat-styled sidebar is gone. The panel now leads with a savings
  card in the brand gradient (cumulative savings, dollar estimate,
  per-run trend and the last run summary) that is the single
  click-through to the full dashboard, followed by the run feed:
  ranked rows with a savings bar, time-ago and a risk dot, each
  opening that run in the dashboard. There is no analyze form in the
  body; runs arrive automatically through the artifact watcher when
  an agent uses redcon, and a manual pack sits in the view title bar
  together with copy context, sync, doctor, config and help. The
  setup checklist only appears while setup is incomplete. Sections
  are toggleable via `redcon.views.*`.

### Fixed

- Marketplace metadata points at the real repository (was a dead org),
  the CLI-not-found hint says `pip install redcon`, `redcon.pythonPath`
  is honored by the guided installer instead of being dead weight, the
  long-standing `tsc` error in `redcon.ts` is gone (`tsc --noEmit` is
  clean for the whole extension), nested sourcemaps are excluded from
  the VSIX, and untrusted-workspace behavior is declared explicitly.
- README rewritten for the 0.9.0 UI with screenshots, the full settings
  table and command list.

### Notes

- Telegraf display font is referenced with a system-stack fallback and
  is NOT bundled, permanently: Pangram Pangram confirmed that OTF files
  may not be redistributed inside an app or a public repository. Their
  licenses cover embedding in closed, non-extractable products only, so
  a font license only becomes relevant for a future closed commercial
  tier.
- Run history now records tokens saved per run (drives the hero and
  trend). Dashboard HTML rendering lives in a pure module
  (`webview/dashboardHtml.ts`) with no vscode dependency, verified by
  rendering both themes and all accent presets in Chromium.

## 0.8.0 - 2026-04-27

### Added (delivered by the underlying Redcon CLI)

- Five new cmd-side compressors the extension's `Redcon: Run Command`
  workflow now picks up automatically: `kubectl_events`, `profiler`
  (py-spy + perf collapsed-stack), `json_log` (NDJSON schema-mining),
  `coverage` (coverage report), `sql_explain` (Postgres + MySQL TREE),
  and `bundle_stats` (webpack + esbuild metafiles). Total now 16
  compressors visible to the dashboard's per-schema reduction view.
- Cross-call session dimension:
  - V41 path aliases collapse repeated paths to short `f001` form
  - V43 reference ledger replaces repeated paragraph blocks with
    session-stable `{ref:001}` markers
  - V47 snapshot-delta dispatcher with schema-aware renderers for
    pytest (set-diff over failure names), git_diff (file-set diff),
    and coverage (per-file pp moves)
  - V49 symbol aliases collapse repeated CamelCase / snake_case
    identifiers to `c001`
  - V93 invariant-cert sha-prefix stamped on COMPACT/VERBOSE outputs
- V85 adversarial GA fuzzer covers all 16 compressor schemas as a
  hard CI gate when `REDCON_V85_ENFORCE=1`.

### Changed

- VS Code marketplace metadata bumped to reflect the broader compressor
  ecosystem; no breaking surface changes in the extension itself.

## 0.7.5 - 2026-03-30

- Glass-style setup UI, centered logo.

## 0.7.0 - 2026-03-29

- One-click setup for Redcon CLI and MCP server.
- Auto-install MCP config for Claude Code, Cursor, Windsurf.

## 0.1.0 - 2026-03-18

### Added

- Activity bar with 4 panels: Budget, File Ranking, Packed Context, Run History
- Status bar with live token budget gauge and quality risk indicator
- File decorations showing relevance scores in the Explorer
- CodeLens showing compression strategy and token savings above files
- Dashboard webview with KPI cards, budget gauge, bar charts, and tables
- 14 commands: Pack, Plan, Plan Agent, Doctor, Init, Export, Benchmark, Simulate, Drift, Dashboard, Config, Copy Context, Refresh, Reveal File
- 9 configurable settings for CLI path, budget, display options
- 6 custom theme colors for score tiers and budget indicators
- Welcome views with quick-start links
- Auto-detection of Redcon CLI installation
- Run history loading from workspace artifacts
