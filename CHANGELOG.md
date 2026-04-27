# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Five new cmd-side compressors: `kubectl_events` (specialised inside
  KubectlGetCompressor for event-shape headers, 91.5% reduction),
  `profiler` (py-spy / perf collapsed-stack, 90%), `json_log` (NDJSON
  with schema-mining, 91%), `coverage` (lowest-coverage top-K, 73%),
  `sql_explain` (Postgres + MySQL TREE, 71%), `bundle_stats` (webpack
  + esbuild metafiles, 84%). Total now 16 cmd compressors.
- V47 schema-aware delta dispatcher with structured renderers for
  pytest (set-diff over failure names + count delta), git_diff
  (file-set + per-file +/- counts), coverage (per-file pp moves above
  0.5pp threshold). Generic line-delta is the fallback.
- V41 session-scoped path aliasing layer (`PathAliaser`); first-use
  binding `f001=path` then bare alias on later mentions, scoped to
  callers passing a session aliaser into `compress_command`.
- V93 invariant-cert sha prefix `mp_sha=<16hex>` stamped on
  COMPACT/VERBOSE outputs; upgrades must-preserve from existence to
  set-equality so auditors can detect spurious additions.
- V62 lint rule-pivot COMPACT layout chosen by min-gate vs the existing
  per-file layout. Wins on Zipfian distributions with >=3 codes.
- V51 stratified file-balanced sampling for >30 test failures (still
  preserves every failing name in a tail summary).
- V64 generic skeleton-clustering helper (`_skeletons.py`) reused by
  pytest cluster path and exposed for future trace compressors.
- V31 24-entry tokenizer-aware substitution table applied at
  compact/ultra tier with re-tokenisation gate.
- V32 whitespace tightening (`,` and `:` gap collapse) post
  `_normalise_whitespace`.
- V38 NO_COLOR / TERM=dumb env injection in runner plus ANSI / OSC /
  CR-overwrite stripper pre-compress.
- 100-vector research corpus under `research/` (BASELINE.md, INDEX.md,
  SYNTHESIS.md, plus one note per V01..V100).

### Changed

- README compressor table extended to 15 schemas (was 11) and includes
  a new "Cross-call dimension" subsection covering V41/V47/V93.
- `redcon cmd-bench` baseline (`benchmarks/cmd_baseline.json`) now
  covers all 16 schemas / 75+ axes; per-schema markdown reports under
  `docs/benchmarks/cmd/` regenerated.
- `verify_must_preserve` now memoises compiled patterns (V78), so
  per-call dynamic must-preserve sets stop thrashing `re._cache`.
- `git_diff`, `git_log`, `git_status` and `coverage` build their
  must-preserve patterns from parsed entries rather than static regex,
  so adversarial mutation no longer trips the contract.
- V85 adversarial GA fuzzer expanded to all 16 compressors with a
  deterministic per-test seed (sha1, not built-in hash); `_NOT_YET_
  ENFORCED` set is empty so `REDCON_V85_ENFORCE=1` is a hard CI gate
  for every shipped compressor.

### Fixed

- ANSI sequences and CR-overwrite progress bars no longer bleed into
  compressed output (V38).
- `git_status`, `json_log`, `bundle_stats` fall through to raw
  passthrough when the structured form would inflate (non-regressive
  guard for adversarial noise).
- Empty-subject `git log` rows now emit `commit <short_sha>` instead
  of collapsing to a bare `<short_sha>` line.
- ls/tree/find must-preserve patterns aligned with the formatter's
  per-directory slicing - basenames the formatter actually emits, not
  full nested paths.
- Removed inline `re.match` / `re.search` calls in `symbols.py`,
  `tree_sitter.py`, and `sql_explain_compressor.py` per the V78 audit.

## [1.1.0] - 2026-03-18

### Added

- Per-signal score breakdown in RankedFile
- Go import graph support
- License header and docstring skipping in deterministic summarizer

### Changed

- Extracted shared file patterns, fixed types, updated SDK

### Fixed

- TOML config loading, file-role substring matching, and degradation test

## [1.0.0] - 2026-03-01

### Added

- Initial public release
- Deterministic context budgeting engine
- CLI with plan, pack, report, diff, benchmark, heatmap, and watch commands
- Workspace support for multi-repo and monorepo-package workflows
- Agent middleware layer
- Plugin system for scorers, compressors, token estimators, and summarizers
- GitHub Action for CI integration
- Docker image
- Redcon Cloud gateway (commercial)
