# V15: Control-flow-graph (CFG) summary - replace function bodies with signature + outgoing calls + return-shape only

## Hypothesis

The current symbol-extraction tier in `redcon/compressors/symbols.py`
preserves full bodies for selected symbols (with mild condensation:
docstring strip, decorator condense, multi-line signature collapse,
data-block truncation, class-tail stubbing). For the *orchestration-style*
question - "what does this module do, who does it call, what does it
hand back?" - the body is mostly unread payload. A CFG summary emits, per
function:

  signature (with annotations) + control-construct counts (`if`, `for`,
  `while`, `try`, `with`, `yield`, recursion flag) + ordered list of
  *qualified outgoing calls* + raise-site list + return-shape list.

Claim: against the full body, this representation lands at **roughly
85-90% token reduction** on real Redcon source while preserving the
information an agent needs to (a) chain calls across files, (b) decide
which function to expand next, (c) answer "what kind of value does this
return". It cannot answer "is the timeout retry off-by-one?" - that
needs the body. So the strategy must be **task-conditional**: opt in
when the score and keyword density say the agent is doing structural /
orchestration reasoning, opt out when the agent is debugging.

## Theoretical basis

### 1. What survives a CFG summary, formally

Treat a function body B as a string of statements. Define three
projections:

  pi_sig(B) = the signature line(s) (parameters + return annotation).
  pi_cfg(B) = the unordered multi-set of (statement_class, outgoing_call,
              return_shape, raise_target) tuples extracted by `ast.walk`.
  pi_body(B) = the literal source text (what symbol-extract preserves).

`pi_cfg` is what V15 transmits; the gap between `pi_cfg ∪ pi_sig` and
`pi_body` is exactly the *intra-statement local information*: literal
constants, arithmetic, string formatting, branch predicates, off-by-one
indices, lock-ordering, etc. Bug-hunting tasks live in that gap; API-
shape tasks do not.

### 2. Token budget arithmetic

Let n_lines(B) be the line count of a function body and L the average
cl100k tokens per line of Python in this codebase. Empirically (cl100k
on Redcon source) L ~ 8.5 tokens/line for non-trivial Python code
(higher than English because identifiers + operators tokenise poorly).
A CFG summary has fixed structure:

  T_cfg(B) ~ T_sig + 1 (flow line) + ceil(|calls|/k) + 1 (returns line)

with k ~ 3 calls per token-line on cl100k after BPE merges. So:

  T_cfg ~ T_sig + 1 + |unique_calls|/3 + 1

and:

  T_full(B) = T_sig + sum_{line in body} L

For a 30-line function with 9 unique outgoing calls, T_sig ~ 25,
T_full ~ 25 + 30*8.5 = 280, T_cfg ~ 25 + 1 + 3 + 1 = 30. Reduction
1 - 30/280 = 89%. The reduction grows monotonically in body length
and shrinks toward zero for tiny dispatch functions where the body IS
its outgoing calls; we should expect the regime to be:

  - Functions with body >= 12 lines: 80-95% reduction.
  - Functions with body 6-12 lines (dispatch tables, simple setters):
    50-70%.
  - Functions with body <= 5 lines: often *negative* - the summary
    overhead exceeds the body. V15 must opt out below a body-size floor.

### 3. Information-theoretic note

`pi_cfg` is a many-to-one projection of `pi_body`: given the summary, an
infinite family of bodies could produce it. If the agent's posterior
over "what this function does at the API level" is dominated by the
type of `pi_cfg`, that posterior is preserved. If the agent's posterior
needs to distinguish among the bodies in the equivalence class
(e.g. `time.sleep(1.0 * 2**n)` vs `time.sleep(min(60, 1.0 * 2**n))` -
both summaries say `calls: time.sleep`), V15 destroys the relevant
information. This is the same observation as differential privacy's
"granularity matters": the loss is task-dependent, so the projection
must be task-dependent.

## Concrete proposal for Redcon

Three changes, all read-only at runtime:

### A. New file: `redcon/compressors/cfg_summary.py` (~120 lines)

Pure AST walk; no new deps. Reuses `ast.unparse` (stdlib >= 3.9) for
signature rendering. Emits the format demonstrated in the experiment.

```python
# cfg_summary.py
import ast

def cfg_summary_of(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    sig = _render_signature(fn)            # def name(...) -> T:
    flow, calls, raises, returns = _walk(fn)
    lines = [sig]
    if flow:    lines.append(f"  # flow: {flow}")
    if calls:   lines.append(f"  # calls: {calls}")
    if raises:  lines.append(f"  # raises: {raises}")
    lines.append(f"  # returns: {returns or '<implicit None>'}")
    lines.append("  ...")
    return "\n".join(lines)
```

`_walk` does one `ast.walk(fn)` pass, classifying nodes; `_qualify_call`
turns `Attribute` chains into dotted strings (`foo.bar.baz()`).
Intra-function nested defs are *not* descended into (their summaries
would belong to a separate CFG entry if the agent wanted them).

### B. Strategy entry sketch in `redcon/compressors/representations.py`

Insert a new tier between `symbol` and `slice`:

```python
# representations.py - sketch only, do NOT edit production
_STRATEGY_PRIORITY = {"full": 0, "symbol": 1, "cfg": 2, "slice": 3, "snippet": 4, "summary": 5}

# Inside build_tiers, after the symbol tier is built:
if cfg_selection is not None and _cfg_eligible(ranked, keywords):
    cfg_text = _cleanup(f"# {path}\n{cfg_selection.text}", "cfg")
    tiers.append(Tier(
        strategy="cfg",
        text=cfg_text,
        tokens=token_estimator(cfg_text),
        chunk_strategy=f"cfg-summary-{language}",
        chunk_reason=cfg_selection.chunk_reason,
        selected_ranges=cfg_selection.selected_ranges,
        symbols=cfg_selection.symbols,
    ))
```

`_cfg_eligible(ranked, keywords)` is the task-conditional gate (next
section). When the gate fails, the tier is simply not emitted and the
greedy packer falls through to `slice` / `snippet` / `summary` exactly
as today.

### C. Task-conditional gate

The bug-hunting case must not silently lose bodies. Two signals,
combined:

  1. **Bug-keyword density per file**. Define a small fixed lexicon of
     bug-investigation keywords (English-only, stable across users):
     `{"bug", "fix", "regression", "off.by.one", "race", "deadlock",
       "leak", "retry", "timeout", "panic", "crash", "exception",
       "assert", "wrong", "incorrect", "broken"}`. Compute
     `density = hits / max(1, file_keyword_hits)`. If density > 0.25
     for the *task keywords* on the file, the file is in
     "investigation mode": skip CFG, fall back to symbol with body.

  2. **Score floor**. Files at the head of the rank (top-3 by
     `ranked.heuristic_score`) get the body. CFG only kicks in for
     files at *medium* score: high enough that the agent should know
     they exist, low enough that the body is unlikely to be read
     line-by-line.

  3. **Body-size floor**. Functions < 12 lines stay full (the summary
     would be larger or roughly equal; demonstrated in the experiment
     for `_rewrite_compact`).

```python
# representations.py, helper
_BUG_KEYS = ("bug","fix","regression","race","deadlock","leak",
             "retry","timeout","panic","crash","exception","assert",
             "wrong","incorrect","broken","off-by-one")

def _cfg_eligible(ranked: RankedFile, task_keywords: list[str]) -> bool:
    # 1. agent investigation signal
    lowered = " ".join(task_keywords).lower()
    bug_hits = sum(1 for k in _BUG_KEYS if k in lowered)
    if bug_hits >= 2:
        return False
    # 2. file rank: only mid-tier scores get CFG
    score = ranked.heuristic_score or ranked.score
    if score >= _TOP_SCORE_BAND:        # very relevant - keep body
        return False
    if score < _CFG_MIN_SCORE:          # irrelevant - already summarised
        return False
    return True
```

The thresholds `_TOP_SCORE_BAND` and `_CFG_MIN_SCORE` are tunable from
`CompressionSettings`; default values come from the existing
`snippet_score_threshold` band (the same calibration that decides
symbol vs slice today).

## Estimated impact

### Token reduction (measured)

Experiment over 10 functions, one per file, spanning the candidate
paths in `redcon/cmd/`, `redcon/compressors/`, `redcon/scorers/`,
`redcon/core/`. cl100k via tiktoken on the full source vs the CFG
summary:

| function                                                       | full tok | cfg tok | reduction |
|----------------------------------------------------------------|---------:|--------:|----------:|
| redcon/cmd/pipeline.py::compress_command                       |      726 |     156 |    78.5%  |
| redcon/cmd/quality.py::_check_level                            |      258 |      88 |    65.9%  |
| redcon/cmd/rewriter.py::_rewrite_compact                       |      180 |      77 |    57.2%  |
| redcon/cmd/runner.py::run_command                              |      782 |     114 |    85.4%  |
| redcon/compressors/symbols.py::_ts_js_symbol_candidates        |      841 |     109 |    87.0%  |
| redcon/compressors/representations.py::build_tiers             |     1734 |     159 |    90.8%  |
| redcon/scorers/relevance.py::score_files                       |     2054 |     126 |    93.9%  |
| redcon/scorers/import_graph.py::_extract_go_import_edges       |      634 |      99 |    84.4%  |
| redcon/core/pipeline.py::run_pack                              |     1588 |     177 |    88.9%  |
| redcon/core/tokens.py::compare_builtin_token_estimators        |      364 |      96 |    73.6%  |
| **TOTAL / token-weighted mean**                                | **9161** |**1201** | **86.9%** |

Per-function: mean 80.6%, median 84.9%, stdev 11.8 pp, min 57.2%
(`_rewrite_compact` - a 12-line dispatch table where the body is
mostly the calls themselves), max 93.9% (`score_files` - a long
multi-stage scoring loop). Variance scales inversely with body
density: the more the function "is" its outgoing calls, the less
benefit; the more it computes locally, the bigger the win.

### Where it lands on the leaderboard

The file-side compressors are not currently quoted in BASELINE.md's
percentage table (that table is command-side). The closest analogue
on the file-side is the existing `summary` tier, which uses a
preview-line approach (`cfg.summary_preview_lines`) and typically lands
at ~50-70% reduction with much lower retained API information. CFG
beats `summary` on retained information per token *and* on raw
reduction across this sample.

Composed against the existing `symbol` tier (which already drops
docstrings, condenses class tails to method-stubs, and truncates data
blocks), CFG adds a roughly 60-75% relative reduction *on top*: the
symbol tier for a 30-line method might emit ~20 lines (~170 tokens);
CFG emits ~5 lines (~50 tokens). Approximate composed reduction vs
raw: ~92-95% (vs symbol-tier's ~50-60% vs raw on the same input).

### Latency

- Cold-start: zero. `ast` is already imported by `symbols.py`. No
  new deps.
- Warm parse: one extra `ast.walk` per function being summarised.
  Linear in body length; for a typical mid-size file (~40 functions,
  ~10 mid-tier eligible) this is sub-millisecond.
- Cache: CFG summary is a pure function of the AST, so it caches
  trivially keyed on `(path, content_hash)`. Slot it into the existing
  `SummaryCacheBackend` via a new `cfg-summary` cache key prefix.

### Affects

- New file `redcon/compressors/cfg_summary.py`.
- New tier in `redcon/compressors/representations.py::build_tiers`
  between `symbol` and `slice`.
- `_STRATEGY_PRIORITY` ordering (additive; existing tier order
  preserved).
- `redcon/cmd/quality.py`: no change (file-side, not command-side).
- Scorers: no change. The gate reads `ranked.heuristic_score` and
  task keywords, both already computed.
- Cache: optional new prefix; falls back to recomputation if absent
  (recompute is fast).

## Implementation cost

- `cfg_summary.py`: ~120 lines including the call-qualification helper
  and return-shape classifier (already prototyped in
  `/tmp/v15_cfg_experiment.py`).
- `representations.py` integration: ~25 lines (new tier + eligibility
  helper). Existing `_STRATEGY_PRIORITY` extended by one entry; downstream
  packing logic is unchanged because tiers are sorted by priority and
  the packer picks the most-detailed-affordable tier.
- Tests: ~8 cases - one per language we care about, plus
  body-size-floor, plus task-keyword gate (bug-words present -> tier
  not emitted), plus determinism check (run twice, byte-identical).
  ~80 lines total.
- New runtime deps: none. Pure stdlib `ast`.
- Risks to determinism: zero. `ast.walk` order is well-defined,
  `ast.unparse` is deterministic in CPython >= 3.9.
- Risks to robustness: low. AST parse failures already handled by the
  existing symbol-extraction pathway; CFG inherits that fallback for
  free.
- Risks to must-preserve: not applicable on the file-side (the
  must-preserve harness is command-side per
  `redcon/cmd/quality.py::run_quality_check`). However, if a file-side
  agent task asks "explain function `compress_command`'s timeout
  branch", the CFG summary will *legitimately* miss it. The
  task-conditional gate is the mitigation, and it must be tuned with
  agent-trajectory data before this strategy is the default.

## Disqualifiers / why this might be wrong

1. **Already partially done in disguise**. The class-tail stubbing in
   `symbols.py::_condense_class_body` already collapses a remainder
   class body to method signatures. CFG summary generalises that to
   *every* function, not just the tail. So V15 is a generalisation of
   an existing tactic, not a new dimension. Marginal contribution: the
   class-tail tactic only fires for `len(body_lines) > 40`; CFG applies
   to every mid-tier function regardless of class membership.

2. **The numbers oversell because the corpus is favourable**.
   Redcon's own source is unusually call-heavy and orchestration-shaped
   (it IS a pipeline) - exactly the regime where CFG summaries win.
   On a numerical-algorithm codebase (linalg, ML training loops) the
   informative content lives in the body's arithmetic and indexing,
   not in the call graph; the task-gate triggers more aggressively
   and the win shrinks. Concrete prediction: on numpy/scipy-style
   files, the eligibility gate disqualifies ~50% of functions and the
   net contribution drops to ~30% reduction at the file level. We
   should not promise 85-90% as a global claim.

3. **The agent might silently regress on debugging tasks**. The bug-
   keyword gate is a binary heuristic over a fixed lexicon. A user
   asking "why does this hang sometimes?" lacks an explicit bug-word
   and would currently pass through the gate, getting CFG summaries
   when they actually need bodies. Mitigations - fuzzier keyword
   matching, agent-feedback loop, or "asked-to-expand" signal from
   `redcon_compress` - all introduce non-determinism or cross-call
   state that BASELINE.md's constraints make expensive.

4. **Composition with existing symbol tier is fiddly**. Today's
   symbol tier already trims; CFG would replace, not augment. So
   `build_tiers` must decide between them, and the eligibility logic
   has to be *coherent* with the existing `score_qualifies /
   force_compress / symbol_beats_slice` decision tree without
   regressing the cases it currently handles well. Risk of double-
   negation and tier-ordering bugs in the integration is non-trivial;
   plan on a property test that asserts "CFG tier output is a strict
   superset of summary information for the same file".

5. **Summary line "calls: foo, bar, +12 more" is itself fragile** to
   tokenizer variation. cl100k merges short identifier sequences with
   commas and spaces, but llama-3 / o200k handle them differently. So
   the absolute reduction percentages cited above are cl100k-specific.
   Cross-tokenizer evaluation would shift them by 5-10 pp.

6. **Overlaps with V13 (CST templates) and V14 (type-driven literal
   collapsing)**. If V13 ships first and emits a per-file template,
   the CFG signature lines are redundant with the template's parameter
   slots. We should sequence carefully: V15 first if we want a near-
   term file-side win without grammar work; V13 first if we expect a
   bigger payoff from cross-file deduplication.

## Verdict

- Novelty: medium. The technique exists in literature (program-
  summarisation, `ctags`, language-server symbol outlines) but the
  *task-conditional* application inside Redcon's tier ladder is new
  to this codebase and not covered by BASELINE.md.
- Feasibility: high. ~150 lines of pure stdlib code, deterministic,
  cache-friendly, no runtime deps.
- Estimated speed of prototype: 1 day (impl + tests + integration);
  +1 day for the eligibility-gate calibration on a small fixture set.
- Recommend prototype: **yes**, scoped as an additional file-side
  tier behind a feature flag, with the bug-keyword gate ON by default.
  Land the tier first; calibrate the gate's score thresholds against
  the existing scoring band; only later promote to default. The
  86.9% token-weighted reduction over 10 real Redcon functions
  (median 84.9%) is large enough to clear the breakthrough bar
  *for the file-side ladder specifically*, but the calibration burden
  to avoid silently breaking debugging tasks is real and warrants
  staged rollout.
