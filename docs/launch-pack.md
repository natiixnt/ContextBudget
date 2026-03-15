# Redcon Launch Pack

## Repository Description

Deterministic context packing and budget enforcement for coding-agent workflows (CLI + Python API).

## Tagline

Stop sending your whole repository to the model.

## Demo Scenarios (Short Narratives)

### 1) Small feature, local iteration loop
A developer adds caching to a search API. They run `plan` to inspect ranked files, `pack` with a max-token budget, and `report` to verify savings before handing context to an agent.

### 2) Risky auth change, CI policy gate
A team updates auth middleware and enables strict policy mode in CI. The run fails on budget/risk threshold, and violation output tells them exactly what to fix before merge.

### 3) Large refactor, strategy benchmarking
During a broad service refactor, the team runs `benchmark` to compare naive, top-k, compressed, and cache-assisted strategies, then uses `diff` between runs to track context drift.

## Social Copy

### Tweet

Redcon is open-source infrastructure for token-aware coding-agent workflows: deterministic context packing, cache reuse, strict policy gates, diff + benchmark artifacts, and a reusable Python API. Stop sending your whole repo to the model.

### Hacker News Description

Redcon reduces context waste in coding-agent workflows. Instead of sending broad repository slices, it deterministically ranks files, compresses context, reuses cache, and enforces token/risk policies in CI. It ships as a CLI and Python API, outputs machine-readable artifacts (`run.json`, diff, benchmark), and stays local-first. Telemetry is optional, disabled by default, and includes no network sink in OSS.

## Launch Checklist

- [ ] Validate all commands on a clean clone: `plan`, `pack`, `report`, `diff`, `benchmark`
- [ ] Run full tests and confirm CI pass
- [ ] Refresh sample outputs under `examples/sample-outputs/`
- [ ] Verify strict policy examples (pass/fail) using `examples/policy.toml`
- [ ] Verify GitHub Action example and uploaded artifacts
- [ ] Confirm README reflects current command flags and outputs
- [ ] Confirm Python API examples run (`RedconEngine`, `BudgetGuard`)
- [ ] Confirm telemetry docs match implementation (disabled default, local-only sink)
- [ ] Publish/label 20 backlog issues and mark 10 as `good first issue`
- [ ] Capture launch screenshots/GIFs and add to docs/assets
- [ ] Prepare release notes: current capabilities vs roadmap

## Screenshot / GIF Shot List

1. Terminal: `redcon plan` ranked files with scores.
2. Terminal: `redcon pack` showing input/saved tokens and risk.
3. Artifact view: `run.json` snippet with `compressed_context[].chunk_strategy` metadata.
4. Terminal: strict policy failure output with explicit violations.
5. Markdown: `redcon report` summary section.
6. Markdown: `redcon diff` showing files added/removed and token deltas.
7. Markdown table: `redcon benchmark` strategy comparison.
8. GitHub Actions Summary: CI run report and policy pass/fail.
9. Python API snippet in terminal/notebook using `BudgetGuard`.
10. Local telemetry file (`.redcon/telemetry.jsonl`) with event samples.
