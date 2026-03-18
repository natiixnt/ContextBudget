# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
