# V92: Differential-privacy-style global info budget across a session

## Hypothesis

Differential privacy (DP) gives a unified scalar accounting of "information
about the underlying data revealed to an analyst": every query consumes some
epsilon, queries compose additively (or via tighter advanced-composition
bounds), and once the cumulative budget is exhausted no further queries are
answered or they are answered at coarser granularity. Apply this metaphor to
Redcon's session: each `redcon_run` / `redcon_compress` / `redcon_search`
shifts the agent's posterior over the repository state. Sum the per-call
"info revealed" estimates (proxy: emitted compressed_tokens, optionally
weighted by an information-density coefficient per compressor) into a
session-scalar `epsilon_used`, cap at `epsilon_total`, and *clamp the tier
selector* so late calls are forced toward COMPACT/ULTRA/log-pointer once the
budget is spent. The claim is that a single global scalar with a known cap
gives the agent both (a) a deployable circuit-breaker that prevents
accidental context-window overruns mid-task, and (b) an externally
auditable "how much of this repo did the agent actually see" number.

Honest framing: this is a metaphor, not real DP. Real DP requires noise
injection calibrated to query sensitivity, and noise contradicts the
deterministic-replay constraint in BASELINE.md #1. We keep the
*budget-as-a-scalar* idea and discard the *noise* mechanism. After
discarding the only thing that distinguishes DP from plain online resource
allocation, what remains is the equalised-remainder allocator already
specified in V30. The contribution of V92 is therefore confined to (i)
re-framing the session ledger as an *info-revealed* quantity rather than a
token quantity, (ii) introducing a per-compressor info-density coefficient
`alpha_c` so 1 token of `git diff --stat` does not cost the same as 1 token
of `pytest -v`, and (iii) the audit / circuit-breaker surface.

## Theoretical basis

### What DP actually says

Pure DP: a randomised mechanism `M` is `epsilon`-DP if for all neighbouring
datasets `D, D'` and outputs `S`,

```
Pr[M(D) in S] <= e^epsilon * Pr[M(D') in S]
```

Sequential composition (Dwork & Roth, 2014, Thm 3.16): running mechanisms
`M_1, ..., M_k` with budgets `eps_1, ..., eps_k` yields a combined
mechanism that is `(sum_i eps_i)`-DP. Advanced composition (Dwork-Rothblum-
Vadhan, 2010) tightens this to `O(sqrt(k log(1/delta)) * eps)` for
`(epsilon, delta)`-DP, which is the formalism most modern systems use.

Two pieces of DP are load-bearing for the original guarantee:

1. **Noise**. The Laplace/Gaussian mechanism adds calibrated noise to query
   answers; the "info revealed" bound is a property of *the noise
   distribution*, not the query.
2. **Sensitivity**. Each query has a known maximal effect on the output if
   one record changes; epsilon is calibrated to that sensitivity.

Redcon has neither. Outputs are deterministic (constraint #1). There is no
adversarial neighbour-dataset model; the "data" is the source repo, which
is not perturbed. So we cannot lift the guarantee.

### What survives the metaphor: an info-revealed accumulator

Drop the noise; keep the bookkeeping. Define a cumulative score

```
epsilon_used(t) = sum_{i=1..t} alpha_{c_i} * compressed_tokens_i
```

where `c_i` is the compressor used on call `i` (git_diff, grep, pytest, ...)
and `alpha_c >= 0` is a *fixed per-compressor info-density coefficient*.
A reasonable choice:

```
alpha_c  =  H_raw(c)  /  median(compressed_tokens for c)
```

with `H_raw(c)` an empirical entropy estimate over a fixed corpus of raw
outputs of compressor `c` (Shannon byte-entropy of stdout, deterministic
for a fixed corpus, computed once at compressor-author time and shipped as
a constant). Compressors that distill more bits per token (e.g. `git diff`
with rename detection, where each emitted token references a hunk that
fanned out from many raw lines) get higher alpha. A line of `ls -R` and a
line of `git status` should not count the same.

The session policy is then:

```
budget := EPS_TOTAL                 # e.g. 32 000 token-units (alpha=1 baseline)
remaining := budget - epsilon_used  # scalar
tier_choice := select_level(hint=BudgetHint(
    remaining_tokens = remaining / max(alpha_for_this_call, alpha_floor),
    max_output_tokens = adaptive_cap(remaining, expected_remaining_calls),
))
```

### Back-of-envelope

Take three compressors with median compressed sizes from BASELINE:

```
git diff   median ~ 250 tokens, raw byte-entropy H ~ 5.6 bits/byte -> alpha_diff   ~ 1.0
grep       median ~ 300 tokens, H ~ 4.4 bits/byte                  -> alpha_grep   ~ 0.65
ls -R      median ~ 500 tokens, H ~ 3.0 bits/byte                  -> alpha_ls     ~ 0.30
```

(coefficients normalised so `alpha_diff = 1`). A session of 8 calls
(`diff, grep, grep, ls, pytest, diff, grep, ls`) at flat 4000-token
per-call cap reveals nominal `8 * 4000 = 32 000` token-units. With
DP-weighted accounting:

```
effective_eps = 1.0*4000 + 0.65*4000 + 0.65*4000 + 0.30*4000
              + 0.85*4000 + 1.0*4000 + 0.65*4000 + 0.30*4000
            ~ 4000 * (1.0 + 0.65 + 0.65 + 0.30 + 0.85 + 1.0 + 0.65 + 0.30)
            ~ 4000 * 5.40 = 21 600
```

Out of a 32 000-unit budget, only 21 600 has actually been "spent";
10 400 units of headroom remain that flat token-only accounting cannot
see. That headroom can be redirected into giving the next high-alpha call
(another `git diff`, say) a VERBOSE tier instead of COMPACT.

### Composition (the only place DP math gives us a tighter knob)

Plain sequential composition is already what V30 does (linear summation).
DP advanced composition would say that *if* the per-call costs were
randomised, we could pay only `sqrt(k log(1/delta)) * eps_max` total for k
calls in expectation. We are deterministic, so this bound does not apply
literally - but it does motivate a *concave aggregator*:

```
epsilon_used(t) = sqrt(sum_{i=1..t} (alpha_{c_i} * compressed_tokens_i)^2)
```

i.e. an L2 instead of L1 sum. This is the deterministic stand-in for
advanced-composition: many small calls accumulate sub-linearly while a
single huge call is penalised at full weight. Whether this matches actual
agent cognitive cost is unproven and is exactly the "metaphor not theorem"
caveat - we cannot derive it from a privacy guarantee we are not making.
But it gives a knob that V30's plain L1 ledger does not have.

### Reduction to V30

If `alpha_c = 1` for all compressors and the aggregator is L1, V92 is
*literally* V30: same equation, same equalised-remainder allocator, same
floor. The only structural additions are the per-compressor weights and
the optional L2 aggregator. The audit-surface ("how much of the repo did
the agent see") is also expressible in V30 by surfacing `cumulative_tokens`
- which BASELINE notes already exists on `RuntimeSession` for the
file-side path. So V92's substantive new content reduces to:

- `alpha_c` weights: a calibration constant table per compressor.
- L2 aggregator: a one-line change in the ledger update formula.

That is it. Everything else is V30 with a different name.

## Concrete proposal for Redcon

Sketch only - production source is not modified. Live next to V30, not
replacing it. If V30 ships, V92 is a 30-LOC follow-on patch.

```python
# redcon/runtime/session.py - sketch additions on top of V30 CmdLedger
@dataclass
class DPCmdLedger(CmdLedger):
    """V30 CmdLedger plus DP-style info-density weighting."""
    epsilon_total: int = 32_000           # token-units at alpha=1 baseline
    aggregator: Literal["l1", "l2"] = "l1"
    # Per-compressor density coefficients; default 1.0.
    alpha: dict[str, float] = field(default_factory=lambda: {
        "git_diff":  1.00,
        "git_log":   0.85,
        "pytest":    0.85,
        "grep":      0.65,
        "find":      0.55,
        "ls":        0.30,
        "lint":      0.75,
        "docker":    0.50,
        "kubectl":   0.55,
        "pkg_install": 0.40,
        "git_status": 0.70,
    })
    _l2_squared: float = 0.0              # for L2 aggregator

    def info_revealed(self) -> float:
        return (self._l2_squared ** 0.5) if self.aggregator == "l2" else float(self.spent_tokens)

    def remaining_eps(self) -> float:
        return max(0.0, self.epsilon_total - self.info_revealed())

    def adaptive_cap_dp(self, compressor: str) -> int:
        a = self.alpha.get(compressor, 1.0)
        a = max(a, 0.1)                   # alpha_floor; never divide by ~0
        rem_eps   = self.remaining_eps()
        rem_calls = max(1, self.expected_remaining_calls - self.call_count)
        per_call_eps = max(self.floor_per_call * a, rem_eps / rem_calls)
        return int(per_call_eps / a)      # convert eps back to tokens for this compressor

    def record_dp(self, compressor: str, compressed_tokens: int) -> None:
        a = self.alpha.get(compressor, 1.0)
        cost = a * compressed_tokens
        self.spent_tokens += compressed_tokens   # raw token mirror (V30 compat)
        self._l2_squared += cost * cost
        self.call_count += 1
        self.last_compressed_tokens.append(compressed_tokens)
```

Plumbing:

- `redcon/cmd/pipeline.py::compress_command` (already gains a `session`
  kwarg under V30) calls `session.adaptive_cap_dp(compressor_name)`
  instead of `session.adaptive_cap()`.
- `_meta.redcon` block adds `epsilon_used`, `epsilon_total`,
  `info_density_alpha`, surfacing the audit number to the agent.
- `redcon/cmd/quality.py` adds a one-shot calibration command
  `redcon calibrate-alpha` that reads the test corpus, computes
  Shannon byte-entropy per compressor over the raw stdout fixtures,
  and emits an `alpha.json` shipped as a constant. Determinism: yes,
  the corpus is fixed and entropy is closed-form.

`alpha.json` is a table of constants, not a learned model - this matters
for BASELINE constraint #3 (no embeddings) and #1 (determinism).

## Estimated impact

- **Token reduction**: zero direct reduction-pp on any single
  compressor. Indirect: same dynamic as V30 (better tier choice over a
  session) plus one extra knob: high-alpha calls (diff, lint) get
  capped tighter, low-alpha calls (ls, docker) get more headroom.
  Best case is +2 to +5% additional agent-visible content over V30
  alone, and only on sessions that mix high- and low-alpha
  compressors. Mostly-uniform sessions (8 git diffs in a row) are
  unaffected vs V30.
- **Latency**: +0 cold (`alpha.json` is loaded lazily on first session).
  +0 warm (one float multiply per call in the ledger).
- **Affects**: same surface as V30 - `pipeline.py`, `mcp/tools.py`,
  `runtime/session.py`. No compressor-internal change.

The audit number is the one genuine gain V92 has over V30: a single
session-scalar that an external operator can log and an agent can show
the user ("I have used 17400 / 32000 info budget on this task"). V30
exposes the same number under a token name; V92 calls it
`epsilon_used` and rescales by `alpha`.

## Implementation cost

- ~50 LOC on top of V30 (which is ~200 LOC). If V30 is shipped first,
  V92 is essentially: alpha table + L2 aggregator + audit surface.
- ~80 LOC for `redcon calibrate-alpha` (fixture corpus walk, entropy
  computation, JSON write).
- New runtime deps: none. Pure Python math.
- Risks to determinism: zero. `alpha.json` is a shipped constant; the
  ledger is deterministic given (alpha, call sequence, compressed
  sizes). Two runs of the same session produce the same ledger.
- Risks to robustness: alpha calibration could drift if a compressor's
  entropy profile changes after a code change. Mitigation: the
  calibration command is part of the existing quality harness; alpha
  drift triggers a CI warning, not a failure (the budget still works
  with stale alpha, just less efficiently).
- Risks to must-preserve: zero. Per-tier must-preserve is a property of
  the compressor; V92 only chooses the tier.

## Disqualifiers / why this might be wrong

1. **Reduces to V30.** Stated upfront. Without the per-compressor alpha
   coefficients and the L2 aggregator, the entire design is V30.
   With the alpha coefficients, the only argument for them over V30's
   uniform weights is empirical: do mixed-compressor sessions
   actually benefit? On Redcon's own test fixtures the spread is real
   (alpha_ls=0.30 vs alpha_diff=1.00) but the volume of `ls -R` calls
   in a typical agent session is small enough that the wall-clock
   gain over V30 is in the noise. An A/B between V30 and V92 may be
   indistinguishable.

2. **DP framing is window dressing.** The hard part of DP is the noise
   mechanism and the sensitivity analysis; we do neither. Anyone with a
   DP background reading the code will see immediately that this is a
   weighted L1/L2 budget allocator labelled `epsilon`. The label could
   harm credibility - "Redcon claims differential privacy guarantees"
   is the wrong inference for a reviewer to draw, and a careless
   marketing line could land us there. Mitigation is to never call it
   "differential privacy"; call it "per-compressor weighted info
   ledger". At which point the DP motivation is gone.

3. **Alpha coefficients are guesswork.** The proposed `alpha_c =
   H_raw(c) / median_compressed_tokens(c)` is one of many sensible
   normalisations. Alternatives: information-loss-weighted (alpha =
   1 - reduction%), agent-cost-weighted (alpha = average pp the
   compressor moves the agent toward task completion - requires
   trajectory data and arguably violates BASELINE #3 if learned).
   Picking the wrong alpha moves us from "V30 with extra constants"
   to "V30 with extra constants that mis-allocate".

4. **L2 aggregator has no rigorous justification.** Advanced
   composition is a *probabilistic* bound for *randomised* mechanisms.
   We are neither probabilistic nor randomised. Using
   `sqrt(sum sq(...))` is a stylistic choice that mimics the formula's
   shape, not a derivation. It might empirically work (sub-linear
   accumulation reflects diminishing real cost of many small
   queries) but the metaphor cannot bear weight.

5. **Audit number is also already trivially expressible.** The runtime
   already exposes `cumulative_tokens` on `RuntimeSession` for the
   file-side path. V30 explicitly extends that to the cmd-side. The
   delta from V30 to V92 on the audit dimension is renaming
   `cumulative_tokens` to `epsilon_used` - a documentation change,
   not a system change.

6. **Multi-tenant pollution risk.** A single shared ledger across
   sessions in a long-running gateway is fine for V30; V92 inherits
   the same risk. No new risk introduced, no new mitigation.

7. **Already partially implemented.** `RuntimeSession.cumulative_tokens`
   exists. The `_meta.redcon` convention (commit 257343) already
   surfaces token counts. The piece truly missing is the per-call
   *cap shaping* from a session ledger, which is V30's job.

## Verdict

- **Novelty: low.** V92 is V30 with two add-ons: per-compressor
  info-density coefficients and an optional L2 aggregator. The DP
  metaphor does not survive contact with the determinism constraint.
  Drop the metaphor and the system reduces to "weighted online
  resource allocation", which is V30. The audit-surface contribution
  is a renaming of `cumulative_tokens`. If the question is "does V92
  contribute anything past V30?", the answer is "alpha weights and
  L2 aggregator and a label" - measurable but small.
- **Feasibility: high.** 50 LOC on top of V30, deterministic, no
  deps, no embeddings.
- **Estimated speed of prototype: 1-2 days** *after* V30 lands. From
  a cold start without V30, the V30 prerequisites dominate and the
  total is ~2 weeks (V30's own estimate).
- **Recommend prototype: no, in this form.** Pursue **V30** instead;
  ship the equalised-remainder allocator and the cmd-side ledger
  there. After V30 has shipped and we have real session traces, run
  a single offline experiment: do mixed-compressor sessions actually
  benefit from per-compressor alpha weights? If yes (>=2pp lift in
  agent-visible content beyond V30), then V92's alpha-weight patch is
  worth a 50-LOC follow-on. If no, V92 is closed.
- **Honest summary**: the DP metaphor told us nothing V30 did not
  already say. Filing this report so the boundary is documented:
  Redcon's session-budget surface is captured by online resource
  allocation, not by privacy accounting.
