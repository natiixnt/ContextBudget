# Redcon Baseline (what already exists, do NOT reinvent)

This file is required reading for every researcher. If your idea reduces to something here, you have not contributed anything. Push past it.

## What Redcon is

Deterministic context budgeting for AI coding agents. Two halves:

1. **File-side** (`redcon plan` / `redcon pack`): scan repo -> score files vs task -> compress per file (full / snippet / symbol-extraction / summary) -> pack top-N under token budget. Outputs `run.json` + `run.md`.
2. **Command-side** (`redcon run`, MCP `redcon_run`): wrap a shell command (git, pytest, grep, find, ls, tree, docker, kubectl, lint, pkg_install, etc.), parse, return CompressedOutput with three tiers.

Local-first. No embeddings. No model calls. Deterministic same-input-same-output.

## Token reductions already shipped (compact tier)

| Compressor | Reduction | Notes |
|---|---|---|
| git diff | 97.0% | drop hunk bodies; per-file +/- counts; rename detection |
| pytest | 73.8% | failures + count of passes |
| grep / rg --json | 76.9% | dedup paths, group by file |
| find | 81.3% | path tree compression |
| ls -R | 33.5% | weakest, header overhead dominates |

Plus: `git_status`, `git_log`, `cargo_test`, `npm_test`, `go_test`, `lint`, `docker`, `pkg_install`, `kubectl`. **11 compressors total.** ULTRA tier reaches 99%+ on most.

## Architecture facts you must respect

- **`redcon/cmd/pipeline.py::compress_command`** is the entry. Runs argv rewriter (canonicalize known flags pre-cache), checks deterministic cache key, runs subprocess via `Popen`-based bounded streaming, picks compressor via `detect_compressor`, normalises whitespace (`\n{3,}` -> `\n\n` for cl100k friendliness).
- **Tiers**: `VERBOSE` (mild trim), `COMPACT` (default reduction floor 30%), `ULTRA` (floor 70%). `select_level` chooses based on remaining budget, max_output_tokens, and quality_floor. Caller can pin floor.
- **Quality harness** (`redcon/cmd/quality.py`): every compressor declares `must_preserve_patterns` regex tuple. Harness runs at all 3 tiers, asserts patterns survive at COMPACT/VERBOSE (ULTRA exempt), checks determinism (run twice, must be byte-identical), checks robustness (binary garbage, truncated mid-stream, 5000 newlines, random word spam). Reduction floors: -10% / 30% / 70%. Inputs <80 raw tokens skip floor (header dominates).
- **Log-pointer tier**: when raw output > 1 MiB, spill full bytes to `.redcon/cmd_runs/<digest>.log`, emit pointer + tail-30. Avoids running parsers on 50 MB docker build logs.
- **Tokenizer**: `_tokens_lite.estimate_tokens` is a cheap cl100k approximation; `redcon.core.tokens` is the canonical (tiktoken-backed) one. Several format choices (drop indent prefixes, collapse newlines, normalise paths) are explicitly cl100k-byte-pair-aware.
- **Format tricks already in place**: indented continuation lines drop the 3-space prefix (saves ~1 token/line on cl100k); `_normalise_whitespace` collapses 3+ newlines to 2 post-compression and re-counts tokens; argv is rewritten before cache lookup so compact-pinned and default share the cache when output is identical but key separately when not.
- **Prefix-gating**: hot regex paths (e.g. diff content lines) are dispatched on first byte before any regex; metadata regexes only fire on lines that pass a literal prefix test.
- **Cache**: per-process `MutableMapping[str, CompressionReport]` keyed on canonicalised argv + cwd hash. SQLite history is opt-in via `record_history=True`. Run history lives under `redcon/cache/run_history_sqlite.py` with delta/heatmap/drift hooks.
- **MCP tools**: `redcon_rank`, `redcon_overview`, `redcon_compress`, `redcon_search`, `redcon_budget`, `redcon_run`, `redcon_quality_check`. Convention: every tool result emits `_meta.redcon` block with the schema, level, token counts, cache_hit. (commit 257343)

## Existing scoring stack (file-side)

`redcon/scorers/`: `relevance.py` (keyword + token-aware match), `import_graph.py` (repo-local imports, no cross-repo collision), `file_roles.py` (test/docs/prod weighting), `history.py` (git churn). All deterministic. **No embeddings, no neural models** - this constraint is load-bearing for the product positioning ("deterministic, local-first, no embeddings"). Proposals that violate it are not impossible but must justify the regression on positioning.

## What is NOT done yet (open frontier)

- Cross-call dictionary or session-level dedup across multiple `redcon_run` invocations.
- Cross-tool dedup (pytest path also seen in grep, dedup ref).
- Adaptive sampling within a streaming subprocess.
- Tokenizer-specific recoding beyond a few ad-hoc rewrites.
- Information-theoretic floor estimation (no rate-distortion characterization).
- Predictive prefetch of likely-next agent calls.
- Custom BPE trained on Redcon's own output corpus.
- Snapshot deltas vs prior `redcon_run` invocations of the same command on the same repo.
- Agent-trajectory-aware budget shaping over a session.
- Differential testing / property-based fuzzing of compressors.
- Markov-blanket / set-cover style file selection.
- Stable session-scoped IDs for files/symbols.

## Constraints (non-negotiable unless your proposal explicitly justifies a break)

1. Deterministic same-input-same-output. No randomness.
2. No required network. (Optional services OK; default path stays local.)
3. No embedding models in the scoring/compression hot path.
4. Must-preserve patterns must hold at COMPACT (ULTRA may drop facts).
5. Cold-start latency budget: lazy-imports already shaved ~62% off cold-start; new techniques cannot regress this.
6. Cache key determinism (argv canonicalization + cwd) must be preserved. New keying schemes must be a strict superset.
7. Output is plain text targeted at a tokenizer (cl100k default). No binary protocols in the agent-facing surface.

## What "breakthrough" looks like for this project

A change that moves the **compact-tier reduction** by >=5 absolute points across multiple compressors, OR cuts cold-start latency by >=20%, OR introduces a new dimension of compression that compounds on top of existing tiers (e.g. cross-call dedup that turns 5 invocations totalling 20k tokens into 8k while preserving the same agent capabilities). Pure micro-optimisations on a single compressor: not breakthrough.
