# V21: Speculative-decoding analog - emit "expected next call" so the agent skips a tool round-trip

## Hypothesis

LLM-serving systems use speculative decoding (Leviathan et al. 2023, Chen
et al. 2023, the original "block-wise parallel decoding" of Stern et al.
2018) to predict the next K tokens with a cheap drafter, ship them
eagerly, and let the main model verify in one step. We claim the same
pattern applies to MCP tool-call sequences. When `redcon_run "git diff
HEAD"` returns a multi-file diff under COMPACT, the agent's next call is
overwhelmingly one of: (a) `redcon_compress` on a changed file, (b)
`redcon_run "git diff <file>"`, or (c) `redcon_search` for a symbol that
appeared in the diff. We can predict (a) deterministically - the changed
file with highest hunk-density-times-task-relevance - precompute its
COMPACT body server-side, and attach it as `_meta.redcon.draft` on the
parent response. The agent verifies cheaply (does this draft answer my
next question?) and either consumes it or ignores it; on a hit, the
second tool call is elided entirely, saving its full per-call protocol
overhead plus the drafted body.

The crisp claim, which differs from V09 (selective re-fetch marker) and
V25 (Markov prefetch over arbitrary call sequences): **V21 ships not just
a pointer but the actual compressed payload**. It only pays off when
per-call protocol overhead `O` plus prediction accuracy `p` clear a
break-even line that V09 cannot reach because V09 still requires a round
trip.

## Theoretical basis

**Speculative decoding cost model (adapted).** Standard speculative
decoding writes expected step-count as `E[steps] = 1 / (1 - p^k)` where
`p` is per-token draft acceptance and `k` is draft length. We use the
single-shot (k=1) special case because each tool call is a discrete
event, not a stream:

```
E[round_trips_baseline]  = 2     (run, then compress)
E[round_trips_with_V21]  = 2 - p (compress is elided with prob p)
```

**Token cost ledger.** Let:

- `O` = per-call MCP protocol overhead (request envelope + response
  envelope + `_meta.redcon` block + JSON keys not in `text`)
- `B` = compressed bytes the speculative draft itself contributes
- `M` = compressed bytes the agent would have received from the elided
  call had it issued it
- `p` = probability the speculative draft matches the call the agent
  would have issued
- `q = 1 - p` = miss probability

Expected token cost per parent call:

```
E[tokens_no_V21] = O_run + |run_text|        +  p_followup * (O_compress + M)
E[tokens_V21]    = O_run + |run_text| + B    +  p_followup * (1 - p) * (O_compress + M_correct_target)
```

Where `M_correct_target <= M` because if the prediction missed, the
agent's actual follow-up may also be on the *correct* file. Subtracting:

```
delta = (B) - p_followup * p * (O_compress + M)
```

Net saving when `p_followup * p * (O_compress + M) > B`. Two regimes
matter:

1. **Overhead-dominated:** if `O_compress >> B` (small bodies, big
   envelopes) the term `p * O_compress` alone can pay. Then V21 saves
   tokens *even if M ~= B* because we only ship body once instead of
   twice.
2. **Body-dominated:** if `O_compress ~ 0` (V21 saves only `M`), V21
   reduces to "shipped same content earlier"; latency benefit only,
   token-flat.

**Per-call protocol overhead, measured.** Reading `redcon/mcp/tools.py`
`tool_run` (lines 614-711) the response carries (excluding `text`):

```
command, cwd, schema, level, original_tokens, compressed_tokens,
reduction_pct, must_preserve_ok, truncated, cache_hit, returncode,
duration_seconds, raw_stdout_bytes, raw_stderr_bytes, notes,
_meta.redcon{schema_version, tool, schema, level, original_tokens,
             compressed_tokens, reduction_pct, must_preserve_ok,
             cache_hit}
```

JSON-serialised at indent=2 (server.py:401), conservative cl100k
estimate: each numeric/bool key+value pair is 4-7 tokens, each string
key+value 5-10 tokens, structural punctuation 2-3 tokens per row. With
~16 top-level keys plus 9 _meta keys plus indent whitespace, **`O ~=
130-180 tokens` per response, plus the input echo (`command`, `cwd`)
~10-30 tokens, plus the request envelope on the agent side**. Realistic
total per-call overhead `O = 180-250 tokens` when both directions are
counted.

**Break-even.** Plug `O_compress = 200`, `M = 400` (median COMPACT body
on a representative file), `p_followup = 0.35`, accuracy `p = 0.6`,
`B = 400` (we ship the full compressed file as draft):

```
delta = 400  -  0.35 * 0.6 * (200 + 400)  =  400 - 126  =  +274 tokens (cost)
```

V21 *loses* at p=0.6. Solve for break-even `p`:

```
p_be = B / (p_followup * (O_compress + M))
     = 400 / (0.35 * 600)  =  1.90
```

Not achievable (>1). So at `B = M = 400`, V21 cannot win on tokens; only
on latency (one fewer round trip). The break-even shifts only when:

- `B << M`: ship a *shrunken* draft (e.g. ULTRA-tier, ~80 tokens) instead
  of full COMPACT. Then `p_be = 80 / (0.35 * 600) = 0.38`. Achievable.
- `O_compress` is large relative to `M` (small files / huge envelopes).
  At `M = 80`, `O_compress = 200`, `B = 80`: `p_be = 80 / (0.35 *
  280) = 0.82`. Borderline.

**Conclusion of the math:** V21 must ship a **compressed-of-compressed**
draft (call it ULTRA-of-COMPACT or "thumbnail") rather than the body the
agent would otherwise have fetched. With thumbnails of ~50-80 tokens and
prediction accuracy >=40%, V21 wins tokens; below that it only wins
latency.

## Concrete proposal for Redcon

**1. New module `redcon/cmd/speculation.py`.** Stateless predictor that
takes a `CompressionReport` plus the parsed canonical type
(`DiffResult`, `GrepResult`, etc.) and returns at most one
`SpeculativeDraft`:

```python
@dataclass(frozen=True, slots=True)
class SpeculativeDraft:
    next_tool: str            # "redcon_compress" | "redcon_run" | "redcon_search"
    next_args: dict[str, str] # canonical, deterministic
    draft_text: str           # the precomputed compressed body
    draft_tokens: int
    confidence: int           # 0..9 quantised
    reason: str               # "highest_hunk_density" | "first_failing_test_path" | ...

def speculate(report: CompressionReport, parsed: object | None,
              hint: BudgetHint) -> SpeculativeDraft | None: ...
```

Predictor table (deterministic, ~60 LOC):

| parent schema      | predicted next tool   | target rule                                                    |
|--------------------|-----------------------|----------------------------------------------------------------|
| `git_diff`         | `redcon_compress`     | file with max(insertions+deletions); ties -> lex first         |
| `pytest`/`cargo_*` | `redcon_compress`     | first failure's `file` field (already in TestFailure)          |
| `grep`/`rg --json` | `redcon_compress`     | path with max match-density; ties -> lex first                 |
| `git_status`       | `redcon_run`          | `git diff -- <first modified path>`                            |
| `git_log`          | `redcon_run`          | `git show <first short_sha>` if a commit-focused task          |
| `lint`             | `redcon_compress`     | path with most error-severity hits                             |
| anything else      | `None`                | no prediction; old behaviour                                   |

Each rule is a one-liner; total predictor cost is O(F) over already-
parsed canonical types. No new I/O, no new regex, no clock or random.

**2. Thumbnail builder.** Reuse existing pipeline. Don't ship the full
COMPACT body as the draft; ship a *thumbnail*: re-invoke the engine on
the predicted target with `quality_floor=ULTRA` and a hard
`max_output_tokens=80` cap (approx half of `O_compress`). For
`redcon_compress` predictions the engine call is
`engine.pack(task, repo, max_tokens=80, top_files=1)` against the
predicted path; for `redcon_run` predictions it is
`compress_command(predicted_argv, hint=ULTRA_thumbnail_hint)`.

The thumbnail is intentionally lossy; the agent uses it as a "do I need
to fetch the full thing?" probe, not as a substitute. This is the
**drafter** in speculative decoding terms - cheap, often-right, easy to
verify.

**3. Surface in `_meta.redcon`.** New optional sub-block:

```json
"_meta": {"redcon": {
  "schema_version": "2",
  "tool": "redcon_run",
  ...,
  "speculative_draft": {
    "next_tool": "redcon_compress",
    "next_args": {"path": "redcon/cmd/pipeline.py", "task": "$TASK"},
    "thumbnail": "<<= 80 token ULTRA body =>>",
    "thumbnail_tokens": 73,
    "confidence": 7,
    "reason": "highest_hunk_density",
    "verifier_hint": "If your next planned call matches next_tool+next_args, you may use thumbnail or call the tool for full detail."
  }
}}
```

Bumps `_REDCON_META_SCHEMA_VERSION` to `"2"`. Older clients that ignore
unknown `_meta` fields lose nothing.

**4. Files touched (sketch).**

- `redcon/cmd/types.py`: add `SpeculativeDraft` dataclass (~10 LOC).
- `redcon/cmd/speculation.py`: new module, predictor table + thumbnail
  builder (~120 LOC).
- `redcon/cmd/pipeline.py::compress_command`: after building the
  report, call `speculate(report, parsed, hint)`; attach to
  `report.speculative_draft` if non-None and
  `hint.emit_speculation` is set. Skip on cache_hit *of the
  speculation itself* to avoid re-computing on every call. Total
  ~25 LOC, all behind a flag (default off).
- `redcon/cmd/budget.py::BudgetHint`: add `emit_speculation: bool =
  False`. Default off; opt-in only.
- `redcon/mcp/tools.py::tool_run`, `tool_compress`, `tool_quality_check`:
  surface `speculative_draft` in `_meta.redcon`. ~15 LOC.
- `redcon/mcp/server.py`: add `emit_speculation` param to the `run`
  schema. ~5 LOC.

Pseudo-code for the `git_diff` -> `redcon_compress` rule:

```python
def _predict_diff_followup(diff: DiffResult, hint: BudgetHint) -> tuple[str, dict, str] | None:
    if not diff.files:
        return None
    candidates = [f for f in diff.files if not f.binary and f.hunks]
    if not candidates:
        return None
    # Deterministic ranking: density desc, then path lex asc.
    target = max(candidates, key=lambda f: (f.insertions + f.deletions, -lex_rank(f.path)))
    # Confidence quantised from magnitude.
    conf = min(9, (target.insertions + target.deletions) // 16)
    if conf < 2:                      # below confidence floor; no speculation
        return None
    return ("redcon_compress",
            {"path": target.path, "task": "$TASK"},
            "highest_hunk_density")

def speculate(report, parsed, hint):
    if not hint.emit_speculation: return None
    schema = report.output.schema
    rule = _RULES.get(schema)         # lookup table
    if rule is None: return None
    pred = rule(parsed, hint)
    if pred is None: return None
    next_tool, next_args, reason = pred
    thumb = _build_thumbnail(next_tool, next_args, max_tokens=80)
    if thumb is None or thumb.tokens > 80: return None
    return SpeculativeDraft(next_tool, next_args, thumb.text, thumb.tokens,
                            confidence=conf, reason=reason)
```

**5. Verifier prompt hint.** The agent needs a one-line instruction so
that thumbnails are used correctly. Embed in the parent text under
`level != ULTRA`:

```
?> Speculative draft attached: thumbnail of redcon_compress on
   redcon/cmd/pipeline.py (73 tok, ULTRA). If that matches your next
   step, use it; else call the tool for COMPACT/VERBOSE detail.
```

Cost: ~25 tokens, paid once. Total visible-block cost = thumbnail (~73)
+ banner (~25) = ~100 tokens. Compare against the break-even formulas
above.

## Estimated impact

- **Token budget:** at `p = 0.4`, `p_followup = 0.35`, `O_compress = 200`,
  `M = 400`, `B = 100`: `delta = 100 - 0.35 * 0.4 * 600 = 100 - 84 =
  +16 tokens`. Marginal *cost* in net per call. At `p = 0.6`: `delta = 100
  - 126 = -26 tokens`, marginal *saving*. Sensitivity is high; results
  will vary per workload. On a 10-call session with 30% follow-up rate,
  expect **+/-3% session tokens**, dominated by accuracy of `p`.
- **Latency:** unambiguous win when accepted. One MCP round trip is
  100-500ms over stdio; eliding it on `p * p_followup ~= 0.21` of calls
  saves ~25-100ms per parent call on average. This is the strongest
  reason to ship V21, and it doesn't depend on the token-cost ledger.
- **Tool-call count:** down by `p * p_followup ~= 21%` on calls where
  the parent is one of the seven supported schemas. Helpful for agents
  with tool-call quotas.
- **Affects:** seven compressors with multi-target outputs (diff,
  pytest, cargo_test, npm_test, go_test, grep, lint). git_status and
  git_log get one rule each (redcon_run-targeted, not redcon_compress).
  Cache layer: thumbnails get their own cache key
  `(parent_cache_key, "speculate", target)`; reuses pipeline cache.

## Implementation cost

- **~200 LOC.** Predictor table (60), thumbnail builder (40), pipeline
  plumbing (25), MCP surface (20), types/dataclass (10), tests (45).
- **No new runtime deps.** No network. No model. Honours BASELINE
  constraints 1-7. Cold-start unaffected (lazy import in
  `pipeline.py`, predictor only loaded if `emit_speculation=True`).
- **Determinism:** predictor reads only canonical parsed types, uses
  fixed quantisation, ties broken lexicographically. Thumbnail is
  produced by an existing deterministic pipeline call.
- **Robustness:** binary garbage / truncated input -> `parsed is None`
  -> `speculate` returns `None`. K=1 cap means at most one extra
  pipeline call, bounded by the same budget/timeout guards.
- **Must-preserve:** thumbnail goes through the same compressor's
  ULTRA path, which BASELINE explicitly exempts from
  must-preserve-at-COMPACT. Parent text is unchanged. Banner is
  additive.

## Disqualifiers / why this might be wrong

1. **Prediction accuracy is unknown and likely <40%.** The whole math
   pivots on `p`. Real agents may follow a `git diff` with
   `redcon_search` for a callee referenced in a hunk, not
   `redcon_compress` on the file with the most lines. Without a
   trace corpus we cannot estimate `p`. This is the same disqualifier
   V09 has, but V21 is more sensitive because it ships *content* not
   just a pointer. Mitigation: implement a small offline analyser
   over recorded MCP transcripts before shipping; require `p >= 0.4`
   measured on at least two distinct agents before flag flips on by
   default.

2. **Speculative bundles confuse agents.** LLMs do not behave like
   verifier circuits in speculative decoding - they do not always
   "verify and accept/reject" cleanly. A thumbnail labelled `next_tool=
   redcon_compress` may anchor the agent into asking exactly that, even
   when its own better plan was `redcon_search`. This is the
   self-instructing-prompt-format risk (V94 territory): the
   intervention can *cause* the prediction to come true via priming,
   not because the agent independently needed it. That breaks the
   evaluation - we'd measure inflated `p` from the priming, not actual
   forecast quality. Mitigation: A/B with the banner-text suppressed
   on half of runs.

3. **Cache invalidation across speculation.** Thumbnail cache key
   includes `(parent_cmd_argv_canonical, predicted_argv_canonical,
   cwd_hash)`. If the predicted target file changes between the
   parent call and a real follow-up, the thumbnail may be stale. The
   parent's cache key already covers cwd state, but per-file mtime is
   not part of it. Means: thumbnail can show stale content even if
   `parent.cache_hit=False`. Mitigation: include the predicted
   target's `stat().st_mtime_ns` in the speculation cache key.
   Adds an `os.stat` per call.

4. **The predictor is a shallow heuristic.** "Highest hunk density"
   is a proxy for "what the agent will look at next" that the existing
   relevance/import-graph scorers already do *better* on the file
   side. Cross-cutting that boundary is hard because `redcon.cmd` is
   intentionally standalone (no scorer imports). Without scorer
   signals, V21's predictor is near coin-flip on real codebases where
   the largest hunk lives in a vendor file or generated `_pb2.py`.
   This is the same boundary that V09 hits.

5. **V21 partially overlaps V09 and V25.** V09 ships a pointer (no
   body). V25 ships a Markov chain over call sequences (no body). V21
   is V09 + thumbnail body, so if V09 ships first, V21 reduces to "add
   a body to the marker" - smaller delta than the framing suggests.
   Mark V21 as **conditional on V09 not shipping the body field
   first**, otherwise re-scope as an extension of V09.

6. **Per-call overhead `O` may be smaller than estimated.** The 130-180
   token figure includes `_meta.redcon` (which V21 *adds to*), input
   echo, and JSON whitespace. Some agent harnesses compress JSON
   whitespace before sending to the model, dropping `O` to maybe
   60-80 tokens. At that point V21's break-even `p` rises and the
   token saving evaporates entirely; only latency remains. Need to
   audit how Claude Code, Cursor, Cline render MCP responses into
   model-visible tokens before claiming the saving.

7. **No mechanism to retract a wrong speculation.** Speculative
   decoding has cheap verification (compare next-token logits). Tools
   have no analog: once the thumbnail enters the agent's context it
   cannot be "rejected" without spending tokens to *say* it was
   rejected. So the "verify cheaply" half of the speculative-decoding
   analogy doesn't transfer cleanly. The math above assumes wrong
   thumbnails are simply ignored at no cost; in practice they pollute
   the context and may cost the agent attention budget.

## Verdict

- **Novelty:** medium-high. The speculative-decoding-to-MCP analogy is
  novel framing for the problem space. Engineering-wise, it composes
  V09 (pointer) + an extra pipeline invocation; the new piece is the
  draft-thumbnail trade-off and the prediction table. Not a
  cross-call-dictionary breakthrough (V41-V50 territory).
- **Feasibility:** high. ~200 LOC behind a flag. All deterministic.
  No new deps.
- **Estimated speed of prototype:** 3-5 days for `git_diff ->
  redcon_compress` end-to-end behind a flag, with thumbnail caching
  and the verifier banner. **2 weeks** to extend across all seven
  schemas with a recorded-trace evaluation harness.
- **Recommend prototype:** **conditional-on** two prerequisites:
  (a) a recorded-MCP-transcript corpus from at least two agents from
  which `p_followup` and `p` can be estimated; and (b) confirmation
  that V09 has not already swallowed the win. If `p_followup * p` on
  the corpus clears 0.25 with K=1 thumbnails, build it. Otherwise
  ship V09 alone; V21's body-shipping increment buys little over
  V09's pointer at moderate accuracy.
