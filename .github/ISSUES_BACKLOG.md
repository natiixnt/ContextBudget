# ContextBudget Launch Backlog (20 Issues)

This backlog is aligned with the current repository (CLI + Python API + examples + CI policy + telemetry abstraction) and near-term roadmap items.

## Issues

1. **Publish JSON Schemas for `run.json`, diff, and benchmark artifacts**
   - Add versioned schema files for run, diff, and benchmark outputs.
   - Add schema validation tests to protect contract stability for integrations.
   - Labels: `core`, `artifacts`, `api`.

2. **Add `contextbudget validate <artifact.json>` command**
   - Validate artifacts against JSON Schemas.
   - Return non-zero on invalid schema in CI.
   - Labels: `cli`, `artifacts`, `ci`.

3. **Add changed-files targeting for `plan` and `pack`**
   - Accept changed-file paths as optional input.
   - Prioritize changed files and nearby dependencies in ranking.
   - Labels: `cli`, `scoring`, `workflow`.

4. **Generate shell completions for bash and zsh**
   - Add completion scripts and install instructions.
   - Keep command/help output compatibility.
   - Labels: `cli`, `docs`, `good first issue`.

5. **Add cache TTL and explicit cache prune command**
   - Support TTL-based cache freshness.
   - Add command to prune expired/unused entries.
   - Labels: `cache`, `cli`, `good first issue`.

6. **Expose scoring component breakdown in plan output**
   - Show weighted contribution of path/content/import-graph signals.
   - Keep ranking deterministic and explainable.
   - Labels: `scoring`, `reporting`, `good first issue`.

7. **Add HTML renderer for run/diff/benchmark reports**
   - Generate static HTML artifacts for CI and sharing.
   - Keep Markdown/JSON outputs unchanged.
   - Labels: `reporting`, `ci`, `ux`.

8. **Policy rule: fail when critical files are skipped**
   - Add configurable threshold for skipped critical files.
   - Integrate with strict mode exit codes.
   - Labels: `policy`, `cli`, `good first issue`.

9. **Expand examples gallery with one realistic service repo**
   - Add mini repo scenario with reproducible commands and outputs.
   - Keep deterministic results for tests/docs.
   - Labels: `examples`, `docs`, `good first issue`.

10. **Add CLI vs Python API parity tests**
    - Verify plan/pack/report parity for equivalent inputs.
    - Protect thin-wrapper contract in CLI.
    - Labels: `tests`, `api`, `good first issue`.

11. **Benchmark mode: add CSV output option**
    - Add optional CSV artifact alongside JSON/Markdown.
    - Preserve current output defaults.
    - Labels: `benchmark`, `reporting`, `good first issue`.

12. **Benchmark mode: compare with previous benchmark artifact**
    - Add baseline input flag and delta summary.
    - Keep deterministic metric definitions.
    - Labels: `benchmark`, `analysis`.

13. **Document telemetry event field map and privacy model**
    - Document all event names and payload fields.
    - Clarify explicit opt-in and no-network defaults.
    - Labels: `docs`, `telemetry`, `good first issue`.

14. **Add CI recipes for changed-files and strict policy gates**
    - Expand docs with copy-paste workflow examples.
    - Cover pull_request and workflow_dispatch usage.
    - Labels: `docs`, `ci`, `good first issue`.

15. **Improve import-graph test coverage for mixed Python/TS repos**
    - Add realistic fixtures with cross-file relevance propagation checks.
    - Guard against ranking regressions.
    - Labels: `tests`, `scoring`, `good first issue`.

16. **Pluggable token-estimator backend interface** `[Roadmap]`
    - Keep deterministic default estimator.
    - Add clean extension hook for alternate tokenizers.
    - Labels: `core`, `tokens`, `roadmap`.

17. **Incremental scan index for repeated runs** `[Roadmap]`
    - Cache scan metadata and skip unchanged files when possible.
    - Maintain output compatibility.
    - Labels: `scanner`, `performance`, `roadmap`.

18. **Plugin interface for custom scorers/compressors** `[Roadmap]`
    - Define stable extension points for third-party modules.
    - Avoid breaking CLI and artifact contracts.
    - Labels: `architecture`, `plugins`, `roadmap`.

19. **Monorepo workspace-root support in config** `[Roadmap]`
    - Support multiple workspace roots and scoped scanning.
    - Keep defaults simple for single-repo usage.
    - Labels: `config`, `scanner`, `roadmap`.

20. **Optional LLM-assisted summarization plugin with deterministic fallback** `[Roadmap]`
    - Keep default OSS flow deterministic.
    - Add explicit opt-in plugin path only.
    - Labels: `compressor`, `roadmap`.

## Good First Issues (10)

- #4 Generate shell completions for bash and zsh
- #5 Add cache TTL and explicit cache prune command
- #6 Expose scoring component breakdown in plan output
- #8 Policy rule: fail when critical files are skipped
- #9 Expand examples gallery with one realistic service repo
- #10 Add CLI vs Python API parity tests
- #11 Benchmark mode: add CSV output option
- #13 Document telemetry event field map and privacy model
- #14 Add CI recipes for changed-files and strict policy gates
- #15 Improve import-graph test coverage for mixed Python/TS repos
