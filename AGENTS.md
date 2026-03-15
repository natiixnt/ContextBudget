# AGENTS.md

## Project Intent

Redcon minimizes token waste in coding-agent workflows by selecting, compressing, caching, and budgeting repository context.

## Agent Operating Rules

- Prefer deterministic heuristics over probabilistic/model calls.
- Keep modules separated by concern:
  - `scanners`: repository traversal and metadata collection
  - `scorers`: relevance ranking
  - `compressors`: context reduction strategies
  - `cache`: summary persistence
  - `core`: pipeline orchestration + render/CLI plumbing
- Maintain typed function signatures.
- Add or update tests whenever behavior changes.
- Keep reports stable and machine-readable (`run.json`).

## Extension Hooks

Future LLM-backed summarization should plug into `compressors` without changing CLI contracts.
