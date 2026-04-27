# Synthesis - 100 Research Vectors for Redcon

Date: 2026-04-26. 93 of 100 notes received at synthesis time; remaining 7 (V75, V81, V85, V96, V97, V98, V99) are tracked at the end.

## Bottom line

The headline claim "world-changing breakthrough that no one can match" is moderated by evidence. Forty-plus vectors confirmed dead-ends or low-novelty (often by hard math: cl100k bytes-per-token asymmetry, structural compressor saturation, single-developer session shapes). But a focused stack of **eight** ideas, when shipped together, plausibly compounds to:

- **+15 to 35 absolute pp** session-level token reduction over the current compact tier on agent workloads where the same command repeats and across mixed compressor calls
- **2 to 4 new compressor classes** that each clear the BASELINE COMPACT 30% and ULTRA 70% floors with margin (k8s events 91%, JSON log 55%, GHA log 99%, profiler 94%)
- **One new dimension** the existing system does not have: cross-call dictionary plus delta-against-prior-baseline, which is on BASELINE.md's "open frontier" list and has multiple independent vectors (V41, V42, V43, V47, V48) converging on the same answer

That is not a single-paper world-changer. It is a coordinated product release that turns Redcon from "11 deterministic command-output compressors" into "session-aware deterministic compression with cross-call dedup, snapshot deltas, and a cleanly-extensible new-compressor surface". The defensible moat is that every shipped piece stays inside BASELINE constraints (deterministic, local-first, no embeddings, no required network, must-preserve-pattern guarantees), which competitors using LLM-based summarisers cannot match without losing those guarantees.

## How to read the rankings

Three filters applied in order:

1. **Hard math gate**: any idea whose mechanism produces a token count that cannot beat the existing compact tier on a measured corpus is rejected, regardless of theoretical elegance. This kills most information-theoretic rebuilds (V03, V05, V06) because cl100k tokens are not bytes and arithmetic-coded byte streams tokenise badly.
2. **Composition value**: an idea worth +2 pp standalone but +15 pp when stacked with three others rises in the ranking. The cross-call dedup theme (V41-V50) is the clearest case.
3. **BASELINE-constraint compatibility**: anything that violates determinism, no-network, or must-preserve invariants is reframed (e.g. "offline tuning + ship static config") or dropped.

Categories used:

- **TIER 1 (ship now)**: empirical evidence supports a clear win, prototype is small (1-3 days), no high-risk dependencies.
- **TIER 2 (ship after one measurement)**: win is probable but conditional on a single empirical question (typical session length, agent re-fetch rate, repeat ratio).
- **TIER 3 (ship as bundle)**: small individual gain, but cluster makes sense (engineering hygiene, telemetry, audit infrastructure).
- **TIER 4 (skip, with citation of why)**: hypothesis defeated by evidence or dominated by another tier.

## TIER 1: ship-now (eight items)

### 1. V47 Snapshot delta vs prior cmd run -- 41% session-level reduction
Estimated 41% additional reduction on a 60-call session over current cache + COMPACT, by emitting only the delta against the previous identical-argv cache miss in the same session. **Per-schema**: pytest 50-90%, git status 55-65%, git diff 40-55%, find/ls 50-65%. Composes with V16 (test-delta) as a special case and is the framework that V69 (coverage-delta) wants. Sketch in `redcon/cmd/delta.py` + `pipeline.py` post-cache-miss block, ~50 LOC + per-schema parsers. Always picks `min(cost_delta, cost_abs)` so V47 is non-regressive by construction.

### 2. V41 Session-scoped path aliases -- 30.52% on path tokens
Lazy first-use binding (`alias=path` once, alias-only thereafter) cuts 30.52% on a 5-trace simulation, dominated by paths being 46.5% of the agent-visible byte stream in long sessions. Strictly dominates the explicit-prelude variant (14.13%) and cannot be net-negative. ~120 LOC in `redcon/runtime/session.py` and a substitution pass post `_normalise_whitespace` in `pipeline.py`. Cache key unchanged (substitution at egress, canonical text cached).

### 3. V38 ANSI strip + NO_COLOR env injection -- +11.5 pp on coloured outputs
Confirmed `runner.py` does NOT inject `NO_COLOR=1` / `TERM=dumb`. Two-layer defence: env merge in `runner.py` silences ~95% at source, plus a `_neutralise_terminal` pre-pass in `pipeline.py` for the rest. Coloured pytest -v: 316 -> 178 tokens (-43.7%). Coloured npm install with progress bar: 101 -> 27 tokens (-73.3%). Combined with existing pytest 73.8% compaction -> **85.3% combined**, +11.5 pp absolute. ~3-4 hours of work.

### 4. V31 Multi-token substitution table -- +2.37 pp aggregate, +15.86 pp on ruff
Empirically curated 26-entry table (cl100k-aware), monotonicity-guaranteed by re-tokenising after each candidate replace and rejecting non-improvements. Aggregate corpus: 10009 -> 9079 tokens (+2.37 pp). **Single-compressor breakthrough on ruff**: was a -7.4% regression, becomes +8.4% reduction (+15.86 pp swing). 125 LOC including a 4-line wire in `pipeline.py::compress_command`. All `must_preserve_patterns` regression-tested and surviving.

### 5. V67 Kubernetes events compressor -- 91.5% reduction
Today's generic `kubectl_compressor` does not handle events specifically; the synthetic event NAME is preserved while REASON/OBJECT/MESSAGE land in `extra`. New events parser groups by `(reason, kind, name)`, templates messages, prioritises Warning. 200-row CrashLoopBackOff fixture: 5000 -> 425 tokens (91.5%). Half-day prototype. ~120 LOC stdlib only.

### 6. V64 Stack-trace dedup with min(baseline, clustered) gate -- +30.3% on clustered failures
50-failure synthetic input (30 ValueError + 15 TypeError + 5 KeyError): COMPACT baseline 1312 tokens -> V64 clustered 914 tokens (+30.3%). Crucially: V64 inflates output for k>=10 distinct templates, so ship behind a `min(baseline, clustered)` gate which is non-regressive across all regimes by construction. Generic helper in `redcon/cmd/compressors/base.py` reusable by V70 (profiler).

### 7. V78 Regex audit + per-pattern memoise in `verify_must_preserve` -- 2-5% warm parse cleanup, prerequisite for safe refactor
Audit found 17 inline `re.match`/`re.search` misses, plus the systemic miss in `redcon/cmd/compressors/base.py::verify_must_preserve` which calls `re.compile(pat, re.MULTILINE)` inside a per-pattern loop on every compressor invocation. Fix: tuple-keyed memoise. ~1-2 hours; prerequisite for any larger perf vector landing safely (V72, V79 dominated by V78 in evidence).

### 8. V32 Token-boundary whitespace tightening (R1 + R2) -- 5.88% on existing fixtures
Two rules pass every must-preserve check on all 10 fixtures: `,[ \t]+(?=\S)` -> `,` and `(?<=[A-Za-z]):[ \t]+(?=[A-Za-z0-9])` -> `:`. Captures 448 / 7620 = 5.88% on the corpus. Largest practical impact: `ls_huge` 2105 -> 1863 tokens (-11.5%) which directly addresses BASELINE-flagged weakest 33.5% reduction. 12-line addition to `pipeline.py` after `_normalise_whitespace`.

**Tier 1 aggregate** (composing without double-counting): roughly +30 to 50 pp on session token totals for repeat-heavy workflows, +5 to 12 pp on single-shot per-call output, plus three new compressor classes worth shipping for ecosystem coverage. Estimated total prototype effort: 2-3 weeks for one engineer.

## TIER 2: ship after one measurement (twelve items)

These all have a clear positive empirical signal but a single load-bearing assumption that should be measured on a recorded agent trace before turning the feature on by default. Order is roughly by upside-if-assumption-holds.

| # | Vector | Headline | Conditional-on |
|---|---|---|---|
| 9 | V45 Bloom-filter seen lines | 30-37% on overlapping sessions | session line-overlap ratio measured >= 0.20 |
| 10 | V49 Symbol cards | ~7.7 pp aggregate on long sessions | V43 substrate landing first |
| 11 | V55 Failure templating (pytest + cargo + npm + go) | 56.3% over compact on clustered failures | activation gate `failures>=10 and any cluster>=3` actually fires on real CI shape |
| 12 | V62 Lint advanced (rule-frequency + per-tier indexing) | +25-30 pp at COMPACT, +60 pp at VERBOSE on Zipfian | lint share of agent calls > 5%; rule-share distribution measured |
| 13 | V70 Profiler compressor | 94% COMPACT on collapsed stacks | py-spy / perf usage in agent traces > 1% |
| 14 | V63 Bundle stats compressor | 96-97% on webpack/esbuild | spill-bypass hint added in pipeline (small change in pipeline.py) |
| 15 | V61 SQL EXPLAIN ANALYZE | 72.9% COMPACT, 93.3% ULTRA | psql/mysql usage validated on >1% of traces |
| 16 | V65 JSON-log compressor | 55-65% COMPACT, 92-97% ULTRA | JSON-line log shape detection rate measured |
| 17 | V42/V43 Hash-keyed shared dict / numeric ref | Hybrid with V41: +8.3 pp on repeat-heavy | session length distribution; resolve-call rate |
| 18 | V51 Stratified reservoir failure sampling | Quality lift (KL 0.32 -> 0.075) at K=30 | must-preserve contract amendment ("sampled-name + total-count survives") |
| 19 | V69 Coverage report delta | 96-98% on coverage report | shared `result_baseline` SQLite table from V47 |
| 20 | V09 Selective-refetch protocol marker | 25-40 tokens cost vs ~144 token expected saving | recorded R*Q*C_w from agent trace |

## TIER 3: ship as a bundle (six items, infrastructure / hygiene)

These have small standalone impact but together unblock everything in Tiers 1 and 2 from regressing or shipping unsafely.

| # | Vector | Why it ships in this bundle |
|---|---|---|
| 21 | V82 Differential golden corpus | Prerequisite for V47, V41, V16; catches byte-stability regressions the M8 quality harness misses. ~330 goldens at `tests/golden/cmd/<schema>/<case>/`, opt-in `_update.py` gated on env var. |
| 22 | V93 Proof-carrying invariant certificate | ~6-token sha256 prefix in `_meta.redcon`; upgrades `must_preserve_ok` boolean to set-equality between raw and compressed (catches spurious additions). Half-day, audit dimension orthogonal to compression. |
| 23 | V76 SQLite WAL persistent cmd cache | Hit-rate moves from 0 to 70-85% for cold workloads (CLI, pre-commit, CI). MCP server gets ~0pp uplift but it already has the in-process cache. ~485 LOC, no new deps. |
| 24 | V81 Hypothesis fuzzer + V86 mutation testing on regex patterns | Curated + property-based + mutation - covers the test-coverage gap discovered by audits in V81/V86/V89. Found a real ReDoS-like pattern in `_MYPY_LINE` already. |
| 25 | V94 Self-instructing COMPACT directive header | ~14-20 token prepended directive making V09 marker actionable. Composition with V09 is the contribution; standalone novelty low. |
| 26 | V44 Deep-link references for high-payload carriers | Per-carrier-class triage table: diff hunk verbose `f*=0.75` (almost always wins), pytest snippet `f*=0.64` (likely win), grep match (loses, do not apply), lint message (do not apply). |

## Skip list (TIER 4) -- twenty-four vectors with citations

The numbers in parentheses are the load-bearing reason each was rejected. Recording these matters because it both proves the work was done and bounds the search space for future research.

- **V03 Universal coder**, **V05 ANS for ULTRA**, **V06 CTW**, and parts of **V02 entropy bound**: all defeated by the same wall - cl100k tokenises high-entropy byte streams badly (~1 token / 4 bytes), so any classical entropy-coder-then-text-encode round trip inflates by 2-4x relative to the structural compressor. The information-theoretic frontier was hit; arithmetic coding is the wrong tool.
- **V11 Patch DAG**: only 14% of commits in 99-commit corpus had >=3-cluster shared structure; mean win 4.54%, median 0%. Not a breakthrough; subsumed by V19 / V47.
- **V12 Alpha-rename**: side-table cost outweighs rename savings on isolated functions (-3.7% to -7.6% net). Imports-sort + dedup standalone is fine, alpha-rename is not.
- **V13 CST templates**: 5.1% theoretical ceiling, ~3.4% net of existing docstring-strip.
- **V14 Type-driven literal collapse**: marker tokens cost more than literals saved on cl100k arithmetic; -2.65% on `budget.py` case study.
- **V19 AST-diff**: 14x larger than COMPACT on a real refactor commit; only wins on pure-rename codemods (single-digit % of all diffs).
- **V20 Bigraph delta**: 92%+ wins only at top_files=100+, beyond default top_files=25; metadata not compressed_context.
- **V28 Call-graph scoring**: call graph is a strict subset of import graph on Redcon's own source (only 2 call-only edges, 99 import-only-lost edges, 11 of 12 re-ranks are demotions).
- **V33 NFKC**: zero token saving on the 11 shipped compressors' real outputs.
- **V34 Numeric formatting**: aggregate ~325 tokens saved per ~200-call session = 0.3-1%, below noise.
- **V36 Cross-tokenizer Rosetta vs V35 Per-tokenizer**: V36 is +0.00% worst-case max on cl100k+o200k. Ship V36 (simpler) and skip V35 entirely, OR neither - savings <2pp.
- **V46 Merkle tree**: composition risk with V41/V42; high-entropy hash strings tokenise badly.
- **V57 Anytime algorithm**: parse-bound timeouts <0.1% of workload; the lift is in subprocess-bound timeouts, which V56 + the existing byte-cap path already handle.
- **V58 Adaptive sampling**: dominated by existing structural compressors on 2 of 3 fixtures; loses 4-11pp.
- **V59 PIPE backpressure**: pipe buffer (~64 KiB) stalls fast; over-engineered vs V56 SIGTERM with a defined `truncated` invariant.
- **V60 Rolling shingle dedup**: 0 repeats at N=6 on three real outputs; collision rate `C(M,2)*V^-N` is hostile.
- **V72 SIMD regex**: dominated by V78 (memoise + audit) at zero install cost and zero cold-start regression.
- **V74 mmap spill**: 6.6x to 28.3x slower than `open().write()` on sequential append - mmap's worst case.
- **V77 SHM IPC**: serialisation is 2-12% of wall-time on VS Code <-> CLI; SHM cannot meaningfully improve a slice that small. Daemonisation (separate vector) would help; SHM specifically would not.
- **V79 PEG/Lark parser**: 7.8x to 259x slower warm parse than hand-written prefix-gated regex chain on real fixtures; +40 ms cold-start violates BASELINE constraint #5.
- **V80 Lazy-deserialise cache**: in-process cache holds live Python objects; vector premise (deserialise cost on hit) does not apply to current backend.
- **V84 Round-trip lossless**: existing `select_level` + cache + log-pointer tier already cover the meaningful cases; bypass would split schema namespace.
- **V92 Differential-privacy budget**: collapses to V30 once noise injection (banned by determinism) is removed; only substantive add-on is per-compressor `alpha_c`.
- **V100 Causal strace**: novelty high but engineering enormous (root, cross-platform, sandbox-incompatible). Cited as inspiration for V67 and V68 which capture 80-90% without kernel tracing.

## Final 7 vectors (now received)

- **V75 Tokenizer-free byte-level estimator**: per-schema bytes/token table beats the current `ceil(len/4)` heuristic by 4-7x in mean-relative error and runs ~8000x faster than tiktoken (0.6 us vs 5.2 ms on 32 KiB diff). Hygiene win. Bundle with Tier 3. Honest: no token reduction, no cold-start cut.
- **V81 Hypothesis property fuzzing**: 0 violations on well-formed inputs but **11 real must-preserve violations on adversarial Unicode** rooted in `str.splitlines()` honouring 9 line separators while parsers expect only `\n`. Bug class is system-wide (10+ compressors). Promote to Tier 3 with a follow-up patch issue.
- **V85 Adversarial GA generator**: surfaced 3 concrete bugs (git_log regex too permissive, ls/tree/find pattern-vs-formatter cap mismatch, git_status header inflation on noisy `##` inputs). Tier 3.
- **V96 CFG/Sequitur grammar discovery**: confirmed negative as expected. -15.5% on prose, +71% niche on YAMLish, but dominated by V61/V63/V65/V67 targeted compressors. Tier 4.
- **V97 Active-learning gateway hook**: blocked on `final_answer_text` capture which requires harness cooperation. ~2-4 pp once flywheel runs. Tier 2 conditional on harness change; not near-term.
- **V98 Markov blanket selection**: 8 files / 22k tokens vs weighted top-25 / 94k tokens (-77 pp on a worked example). Co-modification edges recover dynamic-dispatch links the import graph misses. Promote to Tier 2 conditional on `git log` async caching to avoid cold-start regression.
- **V99 Custom BPE on Redcon corpus** (the wildcard): **only +5.14%** on in-distribution Redcon outputs at vocab 4734; corpus saturates merges, can't go higher. CATASTROPHIC inflation on generic text (BASELINE.md +59.8%, pipeline.py +57.8%, prose markdown +78.5%). Mixing model: Redcon output must be > 92.2% of agent context for V99 to net-win; realistic 30% mix gives ~40% inflation. **Verdict: NOT the hidden breakthrough.** Use V99 as a *ceiling kill-switch*: if any future tokenizer-aware rewrite claims >5% on the same fixtures, double-check the measurement. The well of cl100k-aware micro-rewrites is therefore bounded at roughly the V31 + V32 + V40 ceiling already documented (5-12 pp aggregate). This kills the "secret tokenizer" hypothesis cleanly.

## Updated headline after V99

The coordinated stack remains the breakthrough. The "single hidden algorithmic moat from a custom tokenizer" was eliminated empirically. The defensible position is:

1. **Cross-call dedup + snapshot delta** (V41 + V47 + V48 + V49) is the new dimension competitors using stateless LLM summarisers cannot replicate while keeping deterministic must-preserve guarantees.
2. **Compressor classes 12-17** (V61, V64, V65, V67, V69, V70) lift coverage from "git, tests, grep, find, ls, tree, lint, docker, pkg_install, kubectl, log-pointer" to include "SQL EXPLAIN, stack-trace dedup, JSON-line logs, k8s events, coverage delta, profiler" - each clearing 70% / 30% floors with margin.
3. **Audit + verification stack** (V82 + V93 + V81 + V85 + V86) lifts the "deterministic" claim from "no randomness" to "set-equality cryptographic certificates plus property + adversarial coverage", which is sellable as compliance.

V99's negative result is itself valuable: it tells us we should not spend further engineer-quarters chasing tokenizer-side wins beyond the already-cataloged V31 / V32 / V40 ceiling. The marginal hour now goes to V47 + V41 (cross-call) and the new compressor classes.

## Sequencing recommendation

A four-month plan that converts research to product:

**Month 1 (foundation)**:
- Ship Tier 3 bundle (golden corpus, cert, sqlite WAL, fuzzer-set, regex audit memoise). Unblocks safe refactor.
- Ship V38 ANSI strip and V31 substitution table - immediate single-compressor wins.

**Month 2 (cross-call story)**:
- Ship V41 session aliases + V47 snapshot delta together. This is the new dimension; market it as such.
- Add V44 deep-links for the high-payload carriers identified, gated by V09 markers.

**Month 3 (compressor expansion)**:
- Ship V67 (k8s events), V64 (stack dedup with min-gate) + V55 (failure templating), V70 (profiler), V61 (SQL EXPLAIN), V65 (JSON log).
- Each clears the 70%/30% floors with margin; ecosystem story moves from 11 to 16+ compressors.

**Month 4 (measurement-gated)**:
- Roll out Tier 2 conditionals: turn on V45 (bloom seen) once overlap measurement validates; turn on V49 cards once V43 substrate exists; turn on V62 (lint advanced) once rule-share data confirms Zipfian.
- Decide on V99 outcome: if it shows >=30% reduction over cl100k, plan a custom tokenizer pack for the next major release.

## What this is and is not

This **is** a coordinated set of small-to-medium improvements that compound. The defensive moat is constraint-respecting determinism plus a session-aware new dimension that no LLM-summary-based competitor can match while keeping their soft guarantees.

This **is not** a single algorithmic breakthrough that "no one can copy". The information-theoretic ceiling for token-level compression of the kinds of outputs Redcon ingests was empirically located: classical entropy coders cannot help because cl100k is the wrong channel; structural compression already approaches the schema-specific floor on most compressors; further gains live in cross-call dedup, new compressor classes, and tokenizer-aware micro-rewrites - each individually 2-15 pp, jointly 20-40 pp.

If a single-paper breakthrough lives in this corpus it is V99 (custom BPE), and that is conditional on the empirical training result still pending at this synthesis time.
