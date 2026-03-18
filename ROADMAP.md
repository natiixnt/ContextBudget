# Roadmap: Self-contained progressive packer

Goal: every prompt Redcon produces must be complete and useful on its own,
and the packer must maximize information density under any budget by degrading
representations before dropping files entirely.

Three phases, each shippable independently, each with its own GitHub issues.

---

## Phase 1 - Self-contained cache (P0)

**Problem:** `@cached-summary:{ref_id}` markers leak into the LLM prompt via
`_build_prompt_text()`. The model sees useless placeholder strings instead of
code. Benchmarks look great because token counts drop, but the prompt is
broken.

**Root cause:** `context_compressor.py:594-598` replaces `candidate_text` with
the marker when a cache hit occurs, and `runtime.py:71-88` passes it through
verbatim.

**Fix:** The cache must never replace real content with an opaque marker in
`CompressedFile.text`. On a cache hit we skip recompression (performance win)
but always store the actual compressed text in the output. The cache reference
ID stays in `cache_reference` for accounting/delta, it just never touches
`text`.

### Subtasks

1. **context_compressor.py** - on cache hit, keep `candidate_text = compressed`
   (the real text). Use the cache hit only to skip redundant `put_fragment`.
   Token accounting stays the same (count real text tokens).
2. **runtime.py** - add a defensive guard: if any `text` field starts with
   `@cached-summary:`, log a warning and skip that entry. This is a safety net,
   not the primary fix.
3. **Tests** - add a test that packs twice with a warm cache and asserts no
   `@cached-summary` markers appear in `CompressedFile.text` or in the
   assembled prompt text.
4. **Benchmark validation** - run the 4 benchmark tasks before/after and
   document token count changes. Cache-assisted strategy will report fewer
   "saved" tokens because we no longer fake savings via markers.

**Estimated scope:** 4 files touched, ~80 lines changed, ~40 lines of new
tests.

---

## Phase 2 - Progressive budget packer

**Problem:** The compressor picks one representation per file. When the budget
fills up, remaining files are silently skipped. A 50-line utility that scores
well gets the same "full file" treatment as a 2000-line module, wasting budget.
If budget runs tight, high-scoring files can get dropped entirely because
earlier files consumed too much space.

**Fix:** For each file, pre-generate a tier list of representations with
decreasing token cost:

```
full  ->  symbol  ->  slice  ->  summary (1-line)
```

Then run a two-pass selection:

1. **Tentative pass** - assign each file its best affordable representation
   (highest tier that fits remaining budget), processing files in score order.
2. **Degradation pass** - if files were skipped, try to reclaim budget by
   downgrading the lowest-scoring *included* files one tier, then retry skipped
   files.

This is a bounded greedy with one degradation round - not a full knapsack
solver - so it stays deterministic and fast.

### Subtasks

1. **New: `redcon/compressors/representations.py`** - `FileRepresentations`
   dataclass holding `tiers: list[Tier]` where each `Tier` has `(strategy,
   text, tokens, selected_ranges, symbols)`. One function
   `build_representations(file_record, keywords, ...)` that produces the tier
   list by running existing extraction functions.
2. **Refactor `compress_ranked_files()`** - split the current monolithic loop:
   - First: build representations for all files (reuses existing symbol/slice/
     snippet/summary logic, just stores all variants).
   - Second: run tentative + degradation selection.
   - Third: post-process (cleanup, dedup imports, cache storage).
3. **New metrics** - add `degraded_files: list[str]` and
   `degradation_savings: int` to `CompressionResult`. Track how many files were
   downgraded and how many tokens that freed.
4. **Config** - add `progressive_packer_enabled: bool = True` and
   `max_degradation_rounds: int = 1` to `CompressionSettings`. The flag lets
   users fall back to the old greedy behavior if needed.
5. **Tests** - test that under a tight budget, a file that would have been
   skipped now appears with a degraded representation. Test that degradation
   metrics are populated.
6. **Benchmark validation** - compare old vs new packer on the 4 benchmark
   tasks. Expect 10-25% better file coverage at the same budget, or equivalent
   coverage at a lower budget.

**Estimated scope:** 1 new file (~200 lines), 1 major refactor (~250 lines
changed in context_compressor.py), config/schema updates, ~100 lines of new
tests.

---

## Phase 3 - File-role priors

**Problem:** Scoring treats all files equally. A `README.md`, an
`examples/demo.py`, and the actual `auth/service.py` all compete on keyword
overlap alone. Paraphrased tasks (e.g. "reduce prompt bloat" instead of "delta
compression") miss relevant files because there is no semantic bridge.

**Fix:** Two changes:

### A. File role classification

Classify every scanned file into one of: `prod`, `test`, `docs`, `example`,
`config`, `generated`. Use path heuristics:

| Role       | Heuristic                                                |
|------------|----------------------------------------------------------|
| test       | path contains `test`, `tests`, `spec`, `__tests__`      |
| docs       | path contains `docs`, `doc`, extension `.md`, `.rst`     |
| example    | path contains `example`, `examples`, `demo`, `sample`    |
| config     | extension `.toml`, `.yaml`, `.yml`, `.json`, `.cfg`, `.ini` and in root or config dir |
| generated  | path contains `generated`, `__pycache__`, `.g.`, `_pb2`  |
| prod       | everything else                                          |

### B. Role-based scoring adjustments

In `relevance.py`, after keyword scoring, apply multipliers:

| Role      | Multiplier | Rationale                                   |
|-----------|------------|---------------------------------------------|
| prod      | 1.0        | Baseline                                    |
| test      | 0.6        | Tests are relevant only when task mentions testing |
| docs      | 0.4        | Rarely needed unless task is about docs     |
| example   | 0.3        | Almost never the right context              |
| config    | 0.7        | Often useful but not primary                |
| generated | 0.1        | Almost never useful                         |

When task keywords contain "test", "spec", or "fixture", the test multiplier
becomes 1.2 instead. Same pattern for docs/example roles.

### Subtasks

1. **New: `redcon/scorers/file_roles.py`** - `classify_file_role(path) -> str`
   using path heuristics.
2. **Update `relevance.py`** - apply role multipliers after heuristic scoring.
   Add role to `RankedFile` for downstream visibility.
3. **Config** - add `role_multipliers: dict[str, float]` to `ScoreSettings`
   with the defaults above. Add `role_keyword_overrides` for the test/docs
   special cases.
4. **Update scanner** - include `role` in scan index entries so it does not need
   to be recomputed on every score run.
5. **Tests** - test that docs/examples score lower than equivalent prod files.
   Test keyword override (test task boosts test files).
6. **Benchmark validation** - verify that the "add rate limiting" task no longer
   pulls in docs/examples above prod code.

**Estimated scope:** 1 new file (~60 lines), updates to relevance.py (~40
lines), scanner/config updates (~30 lines), ~80 lines of new tests.

---

## Execution order

```
Phase 1 (P0)  -->  Phase 2  -->  Phase 3
   ~2d              ~5-6d          ~3-4d
```

Phase 1 first because it is a correctness bug - every prompt must be
self-contained before we optimize packing.

Phase 2 next because it has the highest impact on token efficiency and file
coverage, which is the core value proposition.

Phase 3 last because it improves recall quality but the system is already
functional without it.

Each phase gets its own GitHub issues (one per subtask), tests must pass before
merging, and benchmarks are run after each phase to measure impact.

---

## Out of scope (for now)

- **Language parity** (Rust/Java import graph, symbol extraction) - real need
  but lower priority than the packer/cache fixes. Revisit after Phase 3.
- **External summarization** - adds LLM dependency and latency. Not needed
  until deterministic summaries prove insufficient.
- **Semantic/embedding-based recall** - high cost, unclear marginal gain over
  file-role priors. Revisit after measuring Phase 3 impact.
- **Exact tokenizer** - heuristic estimator is within 5% for budget decisions.
  Not worth the tiktoken dependency or latency.
