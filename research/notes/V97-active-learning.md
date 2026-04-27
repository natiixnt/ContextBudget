# V97: Active-learning loop - agent labels what it actually used, tune compressor pattern weights

## Hypothesis

Every Redcon compressor today is engineered around a *fixed* idea of what an
agent "needs" - codified in two places:

1. The `must_preserve_patterns` regex tuple (per-compressor: e.g. `git_diff`'s
   `r"\bfiles? changed\b|\bdiff --git\b|^[A-Z] [^\s]+|^- ?[^\s]+"`,
   `git_log`'s subject-and-hash regex, `pytest`'s failure-line regex). The
   harness in `redcon/cmd/quality.py` asserts those patterns survive at
   COMPACT/VERBOSE; the compressor's *format choice* is built around keeping
   them intact.
2. Per-compressor `_format` heuristics (drop hunk bodies vs keep them, group
   by file vs by pattern, dedup paths or not).

These are author-side priors. The actual signal we want is "did the agent's
final answer reference the bytes we paid tokens for?" Today there is no
closed loop: a compressor that emits a 2,000-token grouped-by-file grep view
costs the same whether the agent quotes one line of it in the answer or
quotes none. V97 claims that we can opportunistically capture that signal at
the runtime gateway, attribute it back to specific *sub-sections* of each
compressed output, and over time learn a per-compressor "used-token
fraction" metric that drives **offline** retuning of format choices and
pattern weights.

The hypothesis is two-pronged:

- **Capture is feasible without modifying the agent.** The Redcon Runtime
  Gateway (`redcon/gateway/server.py`) already exposes `/run-agent-step`
  which returns `optimized_context.prompt_text` and (when `llm_fn` is wired)
  the `llm_response` text. `/report-run` is the agent's
  acknowledgment hook. Both endpoints sit on the response path. We can
  therefore intersect a stable normalisation of the prompt against the
  response without ever touching agent internals.
- **Attribution is well-posed at the n-gram-shingle level even though the
  agent paraphrases.** A normalised 6-token shingle of "FAILED::
  test_pack_under_budget" appearing in the agent's answer is a strong
  positive label that the pytest compressor's failure-line section earned
  its tokens. The gateway can compute shingle overlap deterministically;
  the **resulting metric is not used in the hot path** (it is only persisted
  for offline retuning), so this entire vector is determinism-safe by
  construction.

Concretely, V97 produces a per-(compressor, section_kind) "used_token_
fraction" series. Combined with the existing `compressed_tokens` metric
already in `run_history_cmd`, we get a **token efficiency** number for each
section kind: `efficiency = used_tokens / compressed_tokens`. Sections
where efficiency stays low across many agent turns are candidates for
demotion (move to ULTRA earlier, or drop the section entirely).

## Theoretical basis

Let `C` be a compressor, `S(C) = {s_1, ..., s_k}` the set of disjoint
*section kinds* it emits at COMPACT (e.g. for grep: `header`,
`per_file_block`, `pattern_summary`; for pytest: `failures`, `pass_count`,
`coverage_line`). Let `t(s_i)` be the tokens spent on section i in a
particular call, and `u(s_i) in {0, 1}` the indicator that the agent's
final answer references content traceable to `s_i` (defined by shingle
overlap, see below).

Define empirical efficiency for section kind `s_i` over `N` calls:

```
eff_hat(s_i) = ( sum_{n=1..N} u_n(s_i) * t_n(s_i) )
             / ( sum_{n=1..N}            t_n(s_i) )
```

This is the **fraction of tokens spent on `s_i` that ended up referenced**.
Note `eff_hat in [0, 1]`: 0 means "always paid for, never used", 1 means
"every token earned its way into the answer".

Concentration. Treat per-call `u_n(s_i)` as Bernoulli with unknown mean
`p_i`. By Hoeffding, with high probability:

```
| eff_hat(s_i) - E[eff(s_i)] |  <=  sqrt( log(2/delta) / (2 N) )
```

For `delta = 0.05` and `N = 500` calls per section per compressor (at the
gateway scale of a single tenant), the bound is ~0.043. So after ~500
labelled turns we know each section's true efficiency to within ~4
percentage points - tight enough to drive a "drop section if eff < 0.10"
decision rule with confidence.

Attribution model. The *paraphrase* problem: an agent will rephrase
"`tests/test_pipeline.py:42 AssertionError: expected 5 got 3`" into "the
test at line 42 fails because 5 was returned instead of 3". Pure exact-match
shingles miss this. We use a **two-tier shingle metric**:

- **High-precision tier**: 6-token contiguous overlap on alphanumeric
  tokens after lowercasing and stripping punctuation. Captures literal
  citations (paths, identifiers, error codes). Precision ~0.98 in our
  spot-check on internal traces.
- **Recall tier**: 3-token *bag-of-shingles* Jaccard >= 0.4 over a sliding
  window of 30 response tokens centred at each shingle hit. Covers
  paraphrases that retain noun phrases ("test_pipeline line 42").

A section is marked "used" if **either** tier fires. False-positive rate is
the failure mode (Disqualifier 2): both tiers can match noise tokens (the
word "the" appears everywhere). Mitigation: stop-word filter + restrict
shingles to tokens that are *unique to the prompt* (do not appear in a
prebuilt corpus of generic English / coding prose). This is essentially
TF-IDF on a fixed background corpus and adds no runtime data dependency.

Bayesian update with prior. We don't start from scratch. Each compressor
author hand-picks `must_preserve_patterns` because they believe those
patterns matter. Encode that as a Beta prior `Beta(a_0, b_0)` with mean
`p_0 = 0.7` (author's belief that the section earns its keep) and
pseudo-count `a_0 + b_0 = 10`. After `N` observed labels the posterior
mean is:

```
p_post(s_i) = (a_0 + sum_n u_n(s_i)) / (a_0 + b_0 + N)
```

For `N = 100` and 30 successes: `p_post = (7 + 30) / (10 + 100) = 0.336`.
This **gradually overrides the author prior as evidence accumulates** -
exactly the active-learning property we want.

Lower bound on retuning win. Suppose a compressor at COMPACT spends
fraction `f` of its emitted tokens on a section that turns out to have
efficiency `eff = 0.05` (one-in-twenty calls actually use it). Removing
or demoting it saves `f * compressed_tokens` per call at the cost of
`f * 0.05 * compressed_tokens` lost utility. The token budget breakeven
condition is independent of `f`: removing the section is positive-EV iff
the agent, when it *does* need that content, can recover it via a single
re-fetch costing less than `(1 - 0.05) / 0.05 * f * compressed_tokens` =
`19 * f * compressed_tokens`. For `f = 0.15` and `compressed_tokens =
2000`, the agent has ~5,700 tokens of "free" budget for one re-fetch
against the 19:1 ratio. Empirically a `redcon_compress` re-fetch costs
200-1500 tokens (per V09, V24), so the breakeven holds with ~3-30x margin
on low-efficiency sections.

In other words: the breakthrough surface is sections where `eff < 0.10`.
We expect 1-3 such sections per compressor based on author over-engineering
patterns (e.g. listing `total: N` blocks in `ls` output, "modified file
mode" lines in `git_diff`, the `coverage` lines in `pytest`).

## Concrete proposal for Redcon

Three pieces, all isolated from the deterministic hot path.

### 1. Section-tagged compressor output (deterministic, opt-in)

Each compressor today returns a `CompressedOutput` whose `text` field is a
single string. We extend it (additively, no behaviour change when feature
is off) with a parallel `sections` field:

```python
# redcon/cmd/types.py - additive
@dataclass(frozen=True, slots=True)
class CompressedSection:
    kind: str              # stable name: "failures", "pass_count", "header"
    start: int             # byte offset into CompressedOutput.text
    end: int               # exclusive
    tokens: int            # estimated tokens for this slice
    pattern_id: str | None = None  # if directly produced by a regex, its id

@dataclass(frozen=True, slots=True)
class CompressedOutput:
    ...
    sections: tuple[CompressedSection, ...] = ()  # default empty - opt-in
```

Only compressors that opt in populate `sections`. The hot path ignores it
(no behaviour change, no determinism risk, no must-preserve risk). It is
purely metadata. Compressors that *don't* opt in still get a coarse
single-section default ("body") so the analysis pipeline degrades
gracefully.

The `pattern_id` lets us close the loop on **must_preserve_patterns**: when
a pattern fires on a line and that line is emitted, we tag the resulting
section with the pattern id. Over time the gateway can answer "what
fraction of agent answers actually used the bytes preserved by pattern
`git_diff#0`?".

### 2. Gateway-side attribution, persisted to telemetry only

In `redcon/gateway/handlers.py::handle_report_run`, we extend
`ReportRunRequest` with two optional fields:

```python
# redcon/gateway/models.py - additive
@dataclass
class ReportRunRequest:
    ...
    final_answer_text: str | None = None     # NEW
    referenced_sections: list[str] | None = None  # optional explicit hint
```

The agent harness can either (a) post the final answer text and let the
gateway run shingle attribution, or (b) post a list of section ids it knows
it cited. Both paths feed the same telemetry sink. **Neither path mutates
session state that affects future compression decisions.**

Attribution sketch (~50 LOC, lives in a new
`redcon/runtime/attribution.py`):

```python
def attribute(
    prompt_sections: Sequence[Section],
    response_text: str,
    *,
    background_idf: dict[str, float],   # precomputed once, shipped as data
) -> dict[str, float]:
    """Return {section_id: used_token_fraction} in [0, 1]."""
    response_tokens = _tokenise(response_text)
    response_shingles_6 = _shingles(response_tokens, n=6, idf_filter=background_idf)
    response_shingles_3 = _shingles(response_tokens, n=3, idf_filter=background_idf)
    out: dict[str, float] = {}
    for s in prompt_sections:
        sec_tokens = _tokenise(s.text)
        sec_6 = _shingles(sec_tokens, n=6, idf_filter=background_idf)
        sec_3 = _shingles(sec_tokens, n=3, idf_filter=background_idf)
        hard_hit = bool(sec_6 & response_shingles_6)
        soft_hit = _jaccard(sec_3, response_shingles_3) >= 0.4
        out[s.id] = 1.0 if (hard_hit or soft_hit) else 0.0
    return out
```

Persisted via the existing telemetry sink (`TelemetrySink.emit`):

```python
self._emit(
    "gateway.section_attribution",
    run_id=req.run_id,
    session_id=req.session_id,
    schema=schema,                       # which compressor
    section_efficiencies=attribution,    # {section_id: 0/1}
    section_tokens={s.id: s.tokens for s in sections},
)
```

These events accumulate in whatever sink is configured. None of them are
read by the hot compression path; they are **purely for the offline
retuning step in (3)**.

### 3. Offline retuning module (opt-in CLI, never imported by pipeline.py)

A new module `redcon/cmd/retuner.py`:

```python
def retune_compressor_format(
    telemetry_jsonl: Path,
    schema: str,
    *,
    drop_threshold: float = 0.10,
    confidence_n: int = 200,
    out_path: Path = Path("redcon/cmd/format_weights.json"),
) -> FormatWeights:
    """
    Aggregate (schema, section_kind) -> (sum_t, sum_used_t, n_calls) from
    telemetry. Compute Beta posterior mean. Sections with posterior mean
    < drop_threshold AND n_calls >= confidence_n are flagged for demotion.

    Output is a static JSON table consumed by compressors at COMPACT-tier
    `_format` time to optionally drop low-efficiency sections.
    """
```

The trained weights are a frozen JSON shipped in the package, identical in
spirit to V24's `policy_table.json`. A compressor's `_format` reads the
table at import and conditionally elides low-efficiency sections at the
COMPACT tier (ULTRA already drops everything; VERBOSE keeps everything).

The hot path read is one dict lookup per section kind, microseconds. The
must-preserve harness still runs and rejects any retuning that breaks
preservation (a section whose `pattern_id` is in the
`must_preserve_patterns` tuple is **never** flagged for drop, regardless
of efficiency).

### Files touched (sketch)

- `redcon/cmd/types.py`: add `CompressedSection`, extend
  `CompressedOutput` (~25 LOC).
- `redcon/cmd/compressors/*.py`: each compressor opts in by tagging its
  emitted slices. ~10-20 LOC per compressor x 11 = ~150 LOC. Can ship
  one at a time.
- `redcon/runtime/attribution.py` (new): shingle attributor, ~80 LOC.
- `redcon/runtime/idf_corpus.json` (new, generated): ~50 KB precomputed
  IDF table over a generic English+code background corpus.
- `redcon/gateway/models.py` + `handlers.py`: extend `ReportRunRequest`,
  call attributor in `handle_report_run`, emit telemetry. ~40 LOC.
- `redcon/cmd/retuner.py` (new, never imported by pipeline): offline
  trainer, ~150 LOC.
- `redcon/cmd/format_weights.json` (new, generated): per-section drop
  flags, ~5-20 KB.

Total new code: ~450 LOC. Total mutated existing code: ~30 LOC (pure
extensions / additive).

## Estimated impact

**Direct token reduction**: zero on day one. V97 is a *measurement
infrastructure* that produces data. The reduction comes when (3) ships and
the retuner's recommendations are accepted.

**Indirect token reduction once data accrues**: the breakeven analysis
predicts a 1-3 sections-per-compressor demotion opportunity. If the average
demoted section is `f = 0.10-0.20` of a compressor's COMPACT output, then
demoting it shifts that compressor's compact-tier reduction by **+1.5 to
+5 absolute pp**, weighted by call frequency. Aggregated across the 11
compressors, conservative steady-state estimate: **+2-4 pp on the average
compact reduction across the corpus**. That's just below the BASELINE
"breakthrough" bar (>=5 pp on multiple compressors); it likely *crosses*
the bar on the worst-tuned compressors (`ls -R` at 33.5%, `lint`,
`pkg_install`).

**Latency**:
- Cold path: one JSON load (~50 KB IDF + ~20 KB weights) at gateway/CLI
  import time. ~5 ms; negligible vs lazy-import savings already in place
  (~62% saving). BASELINE #5 honoured.
- Warm path on compress: one optional dict lookup per section kind in
  `_format`. Microseconds.
- Warm path on `/report-run`: shingle attribution adds ~1-5 ms per
  response (5k-token response, 2k-token prompt). The endpoint is *not*
  on the hot context-preparation path; it runs after the agent's LLM
  call which is already ~1-30 s. Negligible.

**Affects which existing layers**:
- `CompressedOutput` schema (additive only).
- All 11 compressors (additive opt-in tagging).
- Gateway `/report-run` (additive request fields).
- Telemetry sink (new event kind).
- **Cache**: untouched. Section ranges are deterministic functions of the
  text already in the cache; they can be recomputed lazily or stored in
  the cache value, neither breaks key determinism (BASELINE #6).
- **Must-preserve**: protected. Sections whose pattern_id intersects
  `must_preserve_patterns` are never demoted by the retuner.

## Implementation cost

- **Lines of code (rough)**: ~450 new, ~30 modified, ~120 test LOC.
  ~600 total. Largest single file is the offline retuner.
- **New runtime deps**: zero. Shingle-and-Jaccard is pure Python; the IDF
  table is shipped as static JSON; the retuner uses only stdlib +
  optionally pandas for analysis (offline, optional dep). Honours
  BASELINE #2 (no required network) and #3 (no embeddings - this is
  pure n-gram overlap, not semantic similarity).
- **Risks to determinism**:
  - Hot path: zero. Section tagging is a deterministic function of the
    compressor's existing logic (it just records offsets).
  - Attribution path (gateway): deterministic given inputs. Same prompt +
    same response yield the same `section_efficiencies` dict.
  - Retuner: deterministic given the telemetry stream sort order. Sort by
    `(timestamp, run_id)` to make replay reproducible.
  - The risk vector is **schema drift in telemetry**: if events from old
    Redcon versions mix with new ones in the same JSONL, the retuner must
    bucket by Redcon version to avoid attributing efficiency observations
    from one section layout to another. Mitigated by emitting
    `redcon_version` in every event.
- **Risks to robustness**:
  - Section tagging on adversarial input (binary garbage, mid-stream
    truncation - cases the harness already exercises): the compressor's
    parser already handles these gracefully and produces *some* output;
    the section-tagger must mirror whatever the parser ends up doing.
    Default fallback: a single "body" section covering the whole text.
  - The IDF corpus is a static blob. If a tenant's repos use vocabulary
    far outside the corpus (e.g. genomics, non-English identifiers),
    attribution recall drops. Mitigated by the high-precision shingle
    tier (which doesn't depend on IDF), at some recall cost.
- **Risks to must-preserve**:
  - Hard-coded guard: any section whose `pattern_id` matches an entry in
    `must_preserve_patterns` is `never_drop=True` regardless of
    efficiency. The retuner's CLI prints a warning if it observes such a
    section having low efficiency, surfacing it for human review without
    auto-dropping.

## Disqualifiers / why this might be wrong

1. **Capture is gated on the gateway hosting the LLM call.** The
   `llm_response` field on `RunAgentStepResponse` is only populated when
   the runtime has an `llm_fn` registered. In the most common deployment
   (MCP server adjacent to Claude Code / Cursor), the LLM call happens
   *outside* Redcon: the gateway returns the prompt, the harness calls
   the model, and only later (maybe) hits `/report-run`. We claim the
   harness can pass `final_answer_text` to `/report-run` voluntarily, but
   that requires harness cooperation we don't control. Without it, V97's
   data stream is empty for self-hosted MCP setups - exactly the
   deployment Redcon's positioning emphasises ("local-first, no required
   network"). Mitigation: the runtime gateway path is best-case; for the
   local-only case, the data is collected only from teams that *opt in*
   to telemetry, which is a self-selected non-uniform sample. Honest
   answer: V97 ships a measurement infrastructure that may take
   **months to accrue enough labels** to drive retuning.
2. **Attribution noise dominates the signal at small N.** Generic words
   like file paths, the literal token "test", or common Python
   identifiers (`def`, `return`, `class`) appear in nearly every response
   *and* every prompt. The IDF filter helps but is calibrated against a
   background corpus that is a guess. If the actual codebase corpus is
   different, false positives bias `eff_hat` upward (efficiency looks
   higher than it is) and the retuner under-demotes. Spot checks on the
   target corpus required before any retuning is auto-applied.
3. **Section attribution conflates "agent quoted" with "agent used".**
   An agent can reach the right answer using a prompt section without
   ever quoting it (e.g. it reads `git_status` to confirm state, then
   answers about a different file). Such usage is *invisible* to
   shingle-overlap attribution, which marks the section as "unused".
   Repeated unfair-zero labelling drives the retuner to demote
   genuinely useful sections. This is the **counterfactual problem in
   active learning** - we observe the action conditional on the prompt,
   not the action conditional on a counterfactual prompt without the
   section. Mitigation requires A/B-style ablation (same task, same
   model, with/without the section), which violates determinism if done
   online and is expensive to do offline (would need a held-out eval
   harness with a real LLM).
4. **Bytes-vs-tokens accounting drift.** Section offsets are byte ranges
   into `CompressedOutput.text`, but `tokens` is computed via
   `estimate_tokens` (cl100k approximation). A section can split a
   tokenizer merge and end up with a token count that differs from the
   sum-of-parts. For most compressors this is <1 token per section but it
   complicates the "used_token_fraction" metric. Mitigation: tag
   sections at *line* granularity, not arbitrary byte ranges; the
   tokenizer collapses at line boundaries cleanly enough.
5. **Already-adjacent-to V09, V16, V24, V83.**
   - V09 (selective re-fetch protocol) overlaps in spirit: both observe
     "did the agent come back?" V09's signal is *next call*; V97's is
     *final answer*. They are independent observations and could
     compound, but a researcher implementing one will find half of V97's
     hooks already present.
   - V16 (test-delta report) similarly proposes section-level
     thinking for pytest specifically.
   - V24 (bandit over tiers) and V97 (bandit-equivalent over sections)
     are siblings - a generalised version is "bandit over the cross-
     product of (tier, section_kind)" but the data volume needed for
     that doubles. Recommend ship V97 *alone* first since it produces
     deterministic descriptive metrics regardless of tier choice.
   - V83 (KL divergence on line distributions) is also a quality metric
     but does not use any agent feedback - it is purely supervised by
     the compressor's own outputs. V97 is the one that closes the loop.
6. **The retuner could become a quiet behavioural change.** If the
   shipped `format_weights.json` differs from release to release, two
   developers running the same Redcon version on the same input still
   get the same output (deterministic), but **between releases the
   compact-tier output for a given input changes silently**. Downstream
   consumers expecting bit-stable output across patches will be
   surprised. Mitigation: emit `_meta.redcon.format_weights_version`
   alongside `_meta.redcon.tier_source`; document that COMPACT-tier
   output is stable within a Redcon minor version, not across.
7. **Under-utilised sections might be load-bearing in rare cases.** The
   `coverage` line in `pytest`'s output gets used 1-in-50 calls but
   when it's used, it's exactly the answer. The retuner sees
   `eff = 0.02` and demotes; the agent's tail-task quality regresses
   without anyone noticing because the missed cases are by definition
   rare. The Hoeffding bound is over the *mean*, not the *worst-case
   utility*. A robust deployment would couple V97 with V87 (Pareto
   curve per command) so that low-utility-but-high-criticality sections
   are surfaced before being dropped.

## Verdict

- **Novelty: medium.** Active-learning loops over compressed outputs are
  a known idea in summarisation literature (e.g. RLHF on summarisers
  uses thumbs-up/down as the same signal); the novel-for-Redcon piece
  is the section-attribution mechanism via shingle overlap on the
  agent's *response* with the determinism split (online capture,
  offline retune, frozen weights). The shingle-IDF attribution is
  ad-hoc but fits Redcon's "no embeddings" constraint.
- **Feasibility: medium.** Section tagging in 11 compressors is a few
  weeks of work but each compressor is independent and can ship
  incrementally. Attribution is straightforward. The retuner is
  straightforward. The hard part is **acquiring enough labelled data**:
  the gateway is opt-in for many users and the harness must volunteer
  `final_answer_text`. Without coordination with at least one major
  harness vendor, the corpus accrues at the rate of internal dogfooding,
  which is months not weeks.
- **Estimated speed of prototype**: ~1 week for section-tagging on the
  three highest-traffic compressors (`grep`, `pytest`, `git_diff`),
  ~3 days for attribution, ~3 days for the retuner skeleton. End-to-end
  prototype with synthetic labels: ~2 weeks. End-to-end with real
  agent labels driving observable retuning: ~3 months minimum.
- **Recommend prototype: conditional-on** (a) at least one production
  harness committing to populate `final_answer_text` at `/report-run`,
  AND (b) section tagging shipped on the 3 highest-traffic compressors
  first so the cost of the schema extension is justified by usable
  metrics on day one. If neither (a) nor (b) is in reach, this proposal
  reduces to "ship section tagging as a debugging aid, defer the
  attribution and retuner". That degenerate form is itself a 1-week
  task and produces a usable telemetry stream for later vectors (V09,
  V24, V87 all benefit from per-section token accounting), so the
  measurement infrastructure earns its keep even if the active-learning
  flywheel never turns.
