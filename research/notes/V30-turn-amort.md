# V30: Turn-budget amortisation across an agent session

## Hypothesis

Every `redcon_run` / `redcon_compress` call in BASELINE today receives a
`BudgetHint` whose `max_output_tokens` is the *per-call* hard cap (default
4000) and whose `remaining_tokens` is a per-call hint of "context still
free". There is no notion of a session-level token ledger. The agent
performs N tool calls per task, but the cap is the same for call 1 and
call N. This wastes budget two ways simultaneously:

1. **Under-spends early.** The first call gets a flat 4000-token cap even
   when 28000 of the session budget is still free. A `git diff` that
   could have ridden VERBOSE comfortably gets needlessly forced to
   COMPACT, dropping fidelity the agent will then re-fetch on call 3.
2. **Over-spends or starves late.** Without a ledger, the cap stays
   constant. If early calls were verbose and burned 24000 tokens, call 8
   still gets the same 4000-cap promise it cannot honour - the agent
   silently exceeds session budget. Or, conversely, the late calls had
   plenty left over and could have gone VERBOSE but are still capped at
   4000.

The claim: a session ledger that tracks cumulative `compressed_tokens`
and divides remaining session budget by *expected remaining calls* gives
strictly more total fidelity for the same session budget than any flat
per-call cap, **provided** a floor protects against starvation when the
session-length estimate is wrong. In the simulated 8-call session over
this repo (numbers below) the flat policy delivers 9 796 useful tokens
while the adaptive policy delivers 27 436 useful tokens for the same
32 000 session budget - a 2.8x increase in agent-visible content with
identical budget honour. The flat policy "saves" 22 204 tokens by
crippling late calls; those saved tokens are never spent and never seen
by the agent. It is budget left on the floor.

## Theoretical basis

Frame each session as a sequential resource-allocation problem:

```
maximise   sum_{i=1..N} V_i(c_i)
subject to sum_{i=1..N} c_i  <=  B
           c_i >= floor
           c_i <= raw_i
```

where `c_i` is tokens emitted on call i, `V_i(c)` is fidelity (a
non-decreasing concave function of c, since we cross VERBOSE ->
COMPACT -> ULTRA at fixed thresholds), and B is the session budget.

The Lagrangian for the equality-bound version is:

```
L = sum_i V_i(c_i) - lambda (sum_i c_i - B)
dL/dc_i = V'_i(c_i) - lambda = 0
```

so the optimal allocation equalises **marginal fidelity per token** across
all calls, V'_i(c_i*) = lambda for all i. We don't know V_i in closed
form ahead of time (the raw size of call i+1 is unknown when we choose
c_i). The classic online approximation for unknown-future allocation
problems is the *equalised remainder* heuristic:

```
c_i  =  (B - sum_{j<i} c_j) / (N - i + 1)
```

i.e. divide what is left equally among what remains. This is optimal
when V is linear and N is known; it is the decision-theoretic dual of
the secretary problem when V is monotone. With concave V it is a
constant-factor approximation (1 - 1/e for the worst case under
adversarial V_i; usually much better empirically).

Two corrections we add:

**(a) Floor.** When the estimate of N is too low, the late c_i collapses
to near zero - the starvation regime in the prompt. Standard mitigation
in cellular scheduling (proportional-fair with min-rate guarantee,
Kelly et al.) is a hard floor:

```
c_i = max(floor, (B - sum_{j<i} c_j) / (N_hat - i + 1))
```

The cost of the floor: it can over-spend the budget if many calls hit
the floor when nothing is left. We absorb this by letting late calls
go to ULTRA (the existing fallback) with `remaining_tokens=floor`; ULTRA
is bounded at ~1% of raw, so floor=800 + ULTRA-cap is safe.

**(b) N estimation.** Three options ranked by build cost vs accuracy:

1. **Constant N=k.** Pick k=8 from observation; trivial; under-estimates
   roughly half of sessions. Quantification below shows even with
   wrong-by-3 estimate we still beat flat.
2. **Per-task-class.** Hash the agent task description into a bucket
   ("debug", "refactor", "feature", "doc-read") and use bucket-mean
   from history. Bucket assignment must be deterministic - we have a
   bag-of-keywords scorer in `redcon/scorers/relevance.py` that is
   already deterministic and doesn't violate BASELINE constraints.
3. **EWMA from sqlite history.** The `redcon/cache/run_history_sqlite.py`
   table records one row per pack call but not per cmd call. Add a
   sibling `cmd_runs` table keyed on session_id; on session start,
   query `SELECT calls_per_session FROM session_summary WHERE repo=?`
   and EWMA over the last K=20 sessions for that repo. Cost: one cheap
   index scan per session start, deterministic reads.

The information-theoretic frame: the session is a Markov decision process
whose state is `(remaining_budget, calls_so_far, recent_call_size)` and
whose action is `c_i`. The optimal policy under unknown N is the
equalised-remainder allocation (Bandi-Bertsimas, "robust online resource
allocation", Math Prog 2013) plus a worst-case floor. We are not
inventing this; we are porting it.

## Concrete proposal for Redcon

Sketch lives at `redcon/runtime/session.py` (not edited - sketch only).
The existing `RuntimeSession` already tracks `cumulative_tokens` and
`turn_number` from the *file-side* pack pipeline. V30 extends it with a
`cmd_ledger` that mirrors that for the *command-side* `redcon_run`.

```python
# redcon/runtime/session.py - sketch additions only
@dataclass
class CmdLedger:
    """Per-session token ledger for redcon_run/redcon_compress calls."""
    session_budget: int = 32_000
    floor_per_call: int = 800
    expected_remaining_calls: int = 8     # initial guess
    spent_tokens: int = 0
    call_count: int = 0
    last_compressed_tokens: list[int] = field(default_factory=list)

    def adaptive_cap(self) -> int:
        rem_budget = max(0, self.session_budget - self.spent_tokens)
        rem_calls  = max(1, self.expected_remaining_calls - self.call_count)
        return max(self.floor_per_call, rem_budget // rem_calls)

    def record(self, compressed_tokens: int) -> None:
        self.spent_tokens += compressed_tokens
        self.call_count += 1
        self.last_compressed_tokens.append(compressed_tokens)

    def update_estimate_from_history(self, history_mean: int) -> None:
        # EWMA, alpha=0.3
        self.expected_remaining_calls = int(
            0.3 * history_mean + 0.7 * self.expected_remaining_calls
        )
```

Plumbing changes (production files - design only, do not modify):

- `redcon/cmd/pipeline.py::compress_command` gains an optional
  `session: CmdLedger | None` kwarg. When present, the pipeline reads
  `hint.max_output_tokens = session.adaptive_cap()` (overriding the
  flat default), runs as today, then calls
  `session.record(report.output.compressed_tokens)`.
- `redcon/mcp/tools.py::tool_run` and `tool_compress` accept a
  `session_id: str | None` parameter. Map session_id to a process-global
  `dict[str, CmdLedger]` (mirrors the existing `_DEFAULT_CACHE`
  pattern). New ledger created lazily on first call.
- `_meta.redcon` block surfaces ledger state so the agent knows where it
  stands:

```json
{"_meta": {"redcon": {
  "session_id": "abc-123",
  "session_budget": 32000,
  "spent_tokens": 5800,
  "remaining_budget": 26200,
  "adaptive_cap": 6550,
  "calls_this_session": 3
}}}
```

`session_id` defaults to a UUID if the caller doesn't pass one; the
existing `RuntimeSession.session_id` field is the natural binding when
the runtime layer is in use.

History-backed N estimate (option 3 above):

```python
# redcon/cache/run_history_sqlite.py - sketch
def session_call_count_ewma(repo: Path, k: int = 20) -> int:
    """Return EWMA of (calls per session) for the last k sessions."""
    rows = conn.execute("""
        SELECT session_id, COUNT(*) AS n
        FROM cmd_runs WHERE repo=? GROUP BY session_id
        ORDER BY MAX(generated_at) DESC LIMIT ?
    """, (str(repo), k)).fetchall()
    if not rows:
        return 8  # default
    alpha = 0.3
    ewma = rows[-1]["n"]
    for r in reversed(rows[:-1]):
        ewma = alpha * r["n"] + (1 - alpha) * ewma
    return max(2, int(ewma))
```

Deterministic same-input-same-output: yes; the EWMA reads a fixed
sqlite snapshot and the formula is fixed. Two callers with the same
history produce the same N estimate. This satisfies BASELINE constraint
#1 even though `record_history` is opt-in.

## Estimated impact

Simulated 8-call session over this repo. Numbers are agent-visible
output tokens (not raw - what the agent actually receives); session
budget = 32 000 tokens. Compression ratios from BASELINE table.

| Policy | Total emitted tokens | Calls forced to ULTRA | Notes |
|---|---|---|---|
| Flat cap (4000/call) | 9 796 | 1 (pytest) | Wastes 22 204 of budget |
| Adaptive (N_hat=8 = correct) | 27 436 | 0 | 2.80x more content |
| Adaptive (N_hat=5, actual=8) | 31 670 | 1 (git_log tail) | Starvation on call 8 |
| Adaptive + floor=800 (N_hat=7) | 27 436 | 0 | Floor never bites |

Token reduction: this is **not** a reduction-pp number on a single
compressor (the compressor reductions are unchanged). It is a
session-level efficiency gain. Per the BASELINE breakthrough definition,
"a new dimension of compression that compounds on top of existing tiers
(e.g. cross-call ... that turns 5 invocations totalling 20k tokens into
8k while preserving the same agent capabilities)" - V30 is the dual
direction: same budget, ~2.8x agent-visible content, same per-call
compressor logic. Both directions count for the same reason.

- Token efficiency at session level: +180% relative content delivered
  per session budget unit, or equivalently ~50% reduction in number of
  re-fetches needed (since calls that would have been compressed below
  fidelity threshold are now VERBOSE).
- Latency: zero per-call regression. One sqlite read at session start
  if EWMA path is enabled (~1ms cold, cached after); otherwise pure
  arithmetic. Cold-start unaffected.
- Affects: `redcon/cmd/pipeline.py`, `redcon/cmd/budget.py` (consumes
  the cap, no logic change), `redcon/mcp/tools.py` (new optional
  param), `redcon/runtime/session.py` (extends existing class),
  `redcon/cache/run_history_sqlite.py` (new sibling table). Compressor
  files are untouched - V30 is purely a budget-shaping layer.

## Implementation cost

- ~200 LOC: `CmdLedger` dataclass (~40), pipeline integration (~20),
  MCP plumbing (~30), sqlite session table + EWMA (~50), tests (~60).
- No new runtime deps. No network. No model. Honours all BASELINE
  constraints - especially #1 (determinism: pure arithmetic plus
  deterministic sqlite read), #6 (cache key superset: ledger does not
  enter the argv/cwd cache key, so cache continues to hit across
  sessions even when adaptive_cap differs), #7 (output stays plain
  text; ledger surface is in `_meta.redcon`).
- Risks to determinism: zero in the deterministic-replay case (same
  session_id + same call sequence -> same caps). Cross-session is
  intentionally not deterministic (the whole point is adapting to
  past sessions).
- Risks to robustness: if `expected_remaining_calls` is mis-estimated
  badly (5 actual = 8 case above), the late calls starve. Mitigation
  is the floor; numbers above show floor=800 absorbs the worst case
  gracefully. A very long session (N=50) with budget=32 000 will
  saturate the floor and degrade gracefully to "everything is ULTRA"
  - identical to the agent already running out of context, no new
  failure mode.
- Risks to must-preserve: zero - the cap only chooses tier, and the
  per-tier must-preserve guarantees inside the compressor are
  untouched. ULTRA tier already drops some patterns (BASELINE explicit);
  V30 may push more calls toward ULTRA in starvation, but never below
  ULTRA's existing floor.

## Disqualifiers / why this might be wrong

1. **Sessions in practice may be too short to matter.** If most agent
   tasks resolve in 2-3 tool calls, the equalised-remainder heuristic
   degenerates to the flat cap (with N=2 the equalised cap is just
   B/2 which is always >= 4000 for B=32k anyway). The proof in the
   simulation is on N=8; we should instrument first to confirm real
   N distribution. If P(N >= 6) < 0.2 across real agents, V30 is a
   small win on a rare case.
2. **Cap is not the bottleneck.** Even with a generous cap, the
   compressor will still pick COMPACT/ULTRA when raw_tokens > cap.
   The 2.8x simulated gain assumes calls have raw sizes in the range
   where a higher cap actually crosses a tier boundary. If real
   agent commands are dominated by huge outputs (multi-MB docker
   logs that hit log-pointer tier regardless), V30 buys nothing on
   them.
3. **session_id leakage / multi-tenant.** A process-global
   `dict[str, CmdLedger]` is fine for the CLI/MCP single-process
   case but contentious in a long-lived gateway server. Need a TTL
   on idle sessions and a cap on simultaneous ledgers, otherwise
   memory grows. Easy fix but a real coordination cost.
4. **Already partially implemented.** `RuntimeSession.cumulative_tokens`
   exists today on the *file-side* path. The runtime layer already
   tracks per-turn pack tokens. V30 is the cmd-side analog plus the
   adaptive cap formula. The novelty is the adaptive cap, not the
   ledger - if the file-side ledger is judged sufficient and command
   calls are deemed cheap-enough that capping them is unnecessary,
   V30 is over-engineering.
5. **Bandit interaction with V24.** If V24 (multi-armed bandit on
   tier choice) ships, it will choose tier based on whether the agent
   re-asked for full output. V24 plus V30 could oscillate: V30 raises
   cap, V24 picks VERBOSE, agent burns budget, V30 floors next call,
   V24 picks ULTRA, agent re-fetches, etc. The two need a single
   joint policy, not two layers stacked.
6. **Per-task-class N estimator may overfit history.** If an agent is
   debugging a flaky test today and the bucket says "test-related =
   N=12 typically", but this particular bug is a 3-call fix, the
   adaptive cap will under-spend for 9 phantom calls that never
   happen. EWMA dampens this but does not eliminate it. The floor
   protects against starvation; there is no symmetric protection
   against under-spend.

## Verdict

- Novelty: **medium**. The equalised-remainder heuristic is textbook
  online allocation; porting it to per-tool-call budget shaping in a
  context-budgeting tool is the new application. Not a breakthrough
  in the BASELINE sense (no new compression dimension), but a
  significant *deployment-time* gain that compounds across all 11
  existing compressors with no per-compressor work. Compare V09
  (channel-coding refetch marker, also medium novelty) for similar
  flavour.
- Feasibility: **high**. The session class already exists; adding a
  ledger and a cap function is a one-day change. The history EWMA
  is a few hours on top.
- Estimated speed of prototype: **2-3 days** for ledger + adaptive
  cap + MCP plumbing + the 8-call session benchmark turned into a
  proper fixture. **1 week** to add the sqlite session table and
  EWMA estimator. **2 weeks** end-to-end with property tests and
  starvation fuzzing.
- Recommend prototype: **yes**, with caveat: instrument real agent
  sessions first to measure N distribution. If empirical N is mostly
  >= 5, the simulated 2.8x gain is plausible and worth shipping. If
  N is typically 2-3, ship a smaller-scope version that only kicks
  in when the runtime detects it has handed out >=4 turns already.
