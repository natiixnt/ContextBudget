# V09: Channel-coding analogy - selective re-fetch protocol when receiver uncertainty is high

## Hypothesis

Today Redcon ships one compressed codeword per tool call and any follow-up
("show me file X in full") costs the agent a fresh, generically-phrased tool
call. We claim that a tiny, deterministic **uncertainty marker** baked into
the compressed output - listing the K paths whose hunk bodies were dropped
ranked by a per-file uncertainty score plus the exact follow-up call-site
to use - reduces wasted re-fetches enough to pay for itself even on small
diffs. Concretely: a 5-path marker costs ~25 cl100k tokens; a single naive
re-ask + over-fetch of one wrong file at COMPACT averages 250-1000 tokens
on the diff/grep/lint corpora. Break-even is one avoided over-fetch in
~10-40 tool calls. The marker is a *feedback-channel sketch* in the
Shannon sense: the encoder (compressor) commits an additional sub-codeword
that lets the receiver (agent) request a refinement instead of a generic
retransmission.

## Theoretical basis

Channel coding with feedback (Shavitt-Lapidot, Horstein 1963; Schalkwijk-Kailath
1966): for many channels the capacity is unchanged by feedback but the
**error exponent doubles**, and the bits to drive the next refinement need
not be uniformly distributed - they can be concentrated on the high-posterior
candidates. In our setting:

- Source: the raw command output (e.g. `git diff` of length N tokens).
- Codeword: the COMPACT-tier compressed text of length M tokens (M = 0.03N
  for git diff per BASELINE).
- Receiver state: the agent's posterior over "which file's dropped hunk
  body do I actually need".
- Feedback symbol: one `redcon_compress` (or `redcon_run --path=...`) call.

Without a marker, the agent's posterior over re-fetch paths is roughly
uniform across all `F` mentioned files. Expected over-fetch cost:

```
E_no_marker = sum_{i=1..F} p_i * cost(i)
where p_i ~= 1/F under uniform prior
```

With the marker, the encoder transmits its own per-file uncertainty
score `u_i` (deterministic, derived from features below). The agent's
posterior collapses onto the top-K. The information cost of the marker:

```
H_marker = K * (B_path + B_score + B_call_template)
```

For K=5, B_path averaged over a real repo at cl100k ~= 4 tokens, score
field 1 token, shared call template amortized to ~3 tokens per slot, so
H_marker ~= 5 * (4 + 1 + 3) = ~40 tokens (worst case), ~25 tokens with
the path-prefix dedup trick already used in the diff compressor (drop
common dir prefix, write only suffix).

Break-even derivation. Let `R` = re-fetch rate (probability the agent
asks a follow-up after a COMPACT result), `Q` = probability the
unguided follow-up fetches the *wrong* file, `C_w` = wasted-over-fetch
cost in tokens. Marker pays off when:

```
R * Q * C_w  >  H_marker  per tool call
```

Empirically R ~= 0.2-0.4 on diffs/grep/lint in agent traces (rough), Q
under uniform prior ~= (F-1)/F ~= 0.8 for F=5, and C_w on git diff
COMPACT for one file ranges 200-1500 tokens (one file's hunks at
verbose). Plug in: 0.3 * 0.8 * 600 = 144 tokens >> 25-40 tokens. The
inequality holds with a fat margin even at conservative R=0.1.

The channel-coding identity that makes this rigorous: with feedback,
the encoder can emit a **selector** of size log2(K) bits over candidate
re-fetch targets and the agent's expected refinement cost drops from
H(file-relevance | codeword) to H(file-relevance | codeword, marker) =
0 if K covers the truly relevant file. The marker is therefore a side-
channel transmitting the encoder's posterior, not new payload bits.

## Concrete proposal for Redcon

Three additions, all backwards-compatible.

**1. Per-compressor `uncertainty_signal()` method**

A new optional method on the `Compressor` protocol in
`redcon/cmd/compressors/base.py`. Returns a tuple of `UncertaintyHint`
records *without* re-running compression - it reads off the canonical
typed result the compressor already builds.

```python
@dataclass(frozen=True, slots=True)
class UncertaintyHint:
    target: str            # e.g. "redcon/cmd/pipeline.py" or "tests/test_x.py::TestY"
    score: int             # 0..9 quantised, deterministic, see below
    reason: str            # one short token: "hunks", "matches", "errors"
    refetch_argv: tuple[str, ...]  # canonical re-fetch invocation, e.g. ("git","diff","--","redcon/cmd/pipeline.py")

class Compressor(Protocol):
    def uncertainty_signal(self, parsed) -> tuple[UncertaintyHint, ...]: ...
```

Default returns `()` so existing compressors keep working untouched.

**2. Deterministic uncertainty score (the QUALITY signal)**

Two signals chosen, both already computable from the canonical types
without re-parsing:

- **S1: hunk-density-per-file** (for `git_diff`): defined as
  `(insertions + deletions) / hunks` for that file, with files where
  `hunks * avg_hunk_lines > T_compact_drop` rated highest. This is the
  quantity that exactly captures "I dropped material the agent might
  need". Tied directly to the compactor's information loss - it
  literally measures the bytes the agent did not see.
- **S2: must-preserve-pattern hits per file** (for `grep`, `lint`,
  `pytest`): count of `must_preserve_patterns` matches whose anchor
  is inside that file's slice. Already computed by
  `verify_must_preserve` in `compressors/base.py`; we just need to
  bucket by path instead of all-or-nothing. This is the quantity that
  measures "this file is the carrier of the surviving facts" -
  high-pattern-hit files are exactly where re-fetch will pay off.

Why these two and not the others V09 listed:

- *Score-volatility* needs cross-call state; violates the
  determinism+statelessness contract that `compress_command` upholds.
  Nice-to-have, but breaks BASELINE constraint #1+#6 unless we route
  through the SQLite history (opt-in only).
- *Churn delta* requires git history scan per call; cold-start cost
  hits BASELINE constraint #5. Leave to a V47-shaped follow-up.

S1 is computed from `DiffFile` already; S2 is computed by re-running
the compiled regex once per per-file slice, which we already pay
inside `verify_must_preserve`. Both are O(N) over the parsed tree and
add zero new state.

Score quantisation: bucket the per-compressor-natural metric into 0-9
via fixed thresholds keyed on the compressor schema (e.g. for diff:
`min(9, (ins+del)//8)` capped). Quantisation is what keeps the marker
cheap: 1 token per file. Fixed thresholds keep determinism.

**3. Marker syntax in the compressed text plus `_meta.redcon`**

Visible block (one compact line per candidate, dropped under ULTRA):

```
?: 5 candidates may need detail
  redcon/cmd/pipeline.py 7 hunks  -> redcon_compress path=redcon/cmd/pipeline.py
  redcon/cmd/quality.py  4 errors -> redcon_compress path=redcon/cmd/quality.py
  ...
```

Token cost of one row (cl100k, with path-prefix dedup against the
preceding diff body): ~5-7 tokens. K=5 rows + a 4-token header = ~30
tokens floor, ~40 ceiling.

In parallel, `_meta.redcon` gains a structured `refetch_candidates`
array so framework-level code (AutoGen, Microsoft Agent Framework,
Claude Code) can act on it without parsing prose:

```json
"_meta": {"redcon": {
  "schema_version": "2",
  "tool": "redcon_run",
  "refetch_candidates": [
    {"target":"redcon/cmd/pipeline.py","score":7,"reason":"hunks",
     "refetch_tool":"redcon_compress",
     "refetch_args":{"path":"redcon/cmd/pipeline.py","task":"$TASK"}}
  ]
}}
```

This bumps `_REDCON_META_SCHEMA_VERSION` in
`redcon/mcp/tools.py` from "1" to "2"; older clients ignore unknown
fields per MCP convention. The K candidates are ranked by uncertainty
score, ties broken lexicographically on target for full determinism.

**4. Files touched (sketch)**

- `redcon/cmd/types.py`: add `UncertaintyHint` dataclass.
- `redcon/cmd/compressors/base.py`: protocol gets optional method;
  default helper `_bucketize(score: int) -> int`.
- `redcon/cmd/compressors/git_diff.py`,
  `grep_compressor.py`, `lint_compressor.py`,
  `pytest_compressor.py`: implement `uncertainty_signal`. ~12 lines
  each.
- `redcon/cmd/pipeline.py::compress_command`: after compression, call
  `compressor.uncertainty_signal(...)`, format top-K into the text
  iff `level != ULTRA` *and* `K >= 2`, attach to a new
  `report.refetch_candidates` field. ~25 lines.
- `redcon/mcp/tools.py::tool_run` and `tool_compress`: surface
  `refetch_candidates` in `_meta.redcon`. ~15 lines.

Pseudo-code for the diff hook:

```python
def uncertainty_signal(self, result: DiffResult) -> tuple[UncertaintyHint, ...]:
    K_MAX = 5
    scored = []
    for f in result.files:
        if f.binary or not f.hunks:
            continue
        # S1: hunk-density. Files with bigger drop pay more under COMPACT.
        magnitude = f.insertions + f.deletions
        score = min(9, magnitude // 8)
        if score == 0:
            continue
        scored.append(UncertaintyHint(
            target=f.path,
            score=score,
            reason="hunks",
            refetch_argv=("git","diff","--",f.path),
        ))
    scored.sort(key=lambda u: (-u.score, u.target))
    return tuple(scored[:K_MAX])
```

## Estimated impact

- **Token reduction**: marker *adds* ~25-40 tokens at COMPACT, so
  per-call reduction worsens by 1-3 percentage points on small inputs.
  At session level, expect net reduction of 5-15% across tool-call
  sequences containing >=1 follow-up: the saved over-fetch (200-1500
  tokens) dwarfs the marker cost. The break-even derivation above is
  the load-bearing claim.
- **Latency**: `uncertainty_signal` is O(F) where F = files in result.
  On all observed corpora F < 200; cost is ~0.05ms per call. No new
  imports, no regex compilation in hot path. Cold-start unaffected.
- **Affects**: only the four compressors with multi-target outputs
  (diff, grep, lint, pytest). git_status, git_log, docker, pkg_install,
  kubectl can opt in later but their natural targets aren't files.
  `_REDCON_META_SCHEMA_VERSION` bump cascades to anyone parsing the
  meta block - backwards compatible by MCP unknown-field rules.

## Implementation cost

- ~150 LOC total: protocol extension (10), four compressor hooks (~50
  combined), pipeline plumbing (~25), MCP surface (~20),
  tests/fixtures (~50).
- No new runtime deps. No network. No model. Honours all BASELINE
  constraints.
- Risks to determinism: zero - quantised scores from already-existing
  parsed types, ties broken lexicographically, no clock or random.
- Risks to robustness: a malformed diff could produce zero candidates;
  marker is then simply omitted (existing behaviour). Adversarial
  input with 50000 "files" capped by K_MAX=5 + a hard
  `len(result.files) <= 10000` guard.
- Risks to must-preserve: marker text is *additive*, never replaces
  existing must-preserve content; pattern checks unaffected.

## Disqualifiers / why this might be wrong

1. **Agent traces may not show the assumed re-fetch rate.** If real
   agents (Claude Code, Cursor, Cline) almost never follow up after a
   COMPACT diff because they can already act, R approaches 0 and the
   marker just costs tokens. Mitigation: ship behind a
   `BudgetHint.emit_refetch_marker` flag, default off until measured.
   This makes the proposal essentially V24-adjacent (multi-armed
   bandit on whether marker pays off) but with a single switch
   instead.
2. **Markers may bias agents toward over-fetching** ("oh, 5 candidates
   - I should look at all 5"). Channel-coding analogy assumes a
   rational receiver; LLM agents are not rational maximum-likelihood
   decoders. The marker can backfire by *inducing* fetches the agent
   would otherwise have skipped. This is a real concern from the
   self-instructing-prompt-format literature (V94 territory).
   Mitigation: cap K at 3 or even 1, tune empirically, and phrase the
   marker as discouraging ("most files unchanged") rather than
   inviting.
3. **The deterministic uncertainty proxy may not predict actual
   relevance.** Hunk density correlates with information loss but not
   necessarily with task relevance. A 200-line refactor in a vendor
   file scores high but is irrelevant; a 3-line fix in the actually-
   buggy module scores low. The relevance scorers
   (`redcon/scorers/relevance.py`, `import_graph.py`) already know
   this, but the cmd-side compressors don't - they live in a
   different process tree. Cross-cutting that boundary is non-trivial
   and risks breaking the `redcon.cmd` standalone-ness that BASELINE
   says is load-bearing.
4. **Already partially implemented.** The diff compressor's COMPACT
   output already lists every file with `+N -M` counts; an attentive
   agent can read the highest-magnitude file off that list without a
   marker. The marker may only be reframing what's already there.
5. **MCP schema-version bump churn.** Any consumer that pinned
   schema_version="1" by exact match (rather than semver) breaks.
   Low-risk in practice but a real coordination cost.

## Verdict

- Novelty: **medium**. The channel-coding framing is novel for code
  context tools; the engineering is incremental over the existing
  must-preserve harness and `_meta.redcon` block. It is *not* a
  cross-call breakthrough (V41-V50 territory) - those would compound
  more.
- Feasibility: **high**. Two days for a flagged-off prototype on
  git_diff alone, then a week to extend across grep/lint/pytest and
  measure against a recorded agent-trace corpus.
- Estimated speed of prototype: **2-3 days** for diff-only behind a
  flag, **1-2 weeks** to land across four compressors with proper
  fuzzed quality fixtures and an A/B harness against recorded traces.
- Recommend prototype: **conditional-on** instrumenting at least one
  recorded agent trace first to estimate R (re-fetch rate) and Q
  (wrong-file rate) on real outputs. If R*Q*C_w on the trace clears
  H_marker by >=2x at K=3, build it; otherwise this is a feature in
  search of a problem.
