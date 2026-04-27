# V59: Budget-aware backpressure to subprocess (PIPE pause)

## Hypothesis
Once an incremental compressor has decided "I have all the information I will ever emit" (e.g. 100 pytest failures already collected, ULTRA budget allows only 30; or git diff has tallied per-file +/- counts and is dropping all hunk bodies), Redcon should stop reading the subprocess pipe. The OS pipe buffer (typically 64 KiB on Linux/macOS) fills, the subprocess blocks on its next `write()`, and we hold it in a paused state. From there we either resume reading (rare: the agent asked for the long-form view), or we SIGTERM (common). This generalises V56 (early-kill on cap-hit) and V57 (anytime emit). The novelty is treating the subprocess as a recoverable pause point rather than a discard point: tail content is preserved as kernel-buffered state until we decide its fate.

## Theoretical basis
Pipe backpressure is a built-in flow-control primitive of POSIX. A `write(2)` on a full pipe blocks the writer (or returns `EAGAIN` for `O_NONBLOCK`). Linux pipe capacity defaults to 16 pages = 65 536 bytes (configurable via `F_SETPIPE_SZ` up to `/proc/sys/fs/pipe-max-size`); macOS defaults to 16 KiB and silently caps lower. Producer-consumer queueing theory tells us that if producer rate `lambda_p` >> consumer rate `lambda_c`, mean queue length grows without bound; if we set `lambda_c = 0` (we stop reading), the queue saturates at pipe capacity `B` and the producer blocks deterministically.

Back-of-envelope, for a typical noisy command that the compressor saturates early:
```
B          = 65_536            # pipe buffer bytes (Linux)
P_rate     ~ 50 MB/s           # pytest, find, grep raw stdout
T_block    = B / P_rate        # ~ 1.3 ms until the subprocess stalls
T_term     = 1 s               # _KILL_GRACE_SECONDS
W_cur      = bytes-after-saturation  (V56 truncates these)
W_v59      = min(W_cur, B)            (V59 keeps up to one pipe-buffer of tail)
T_extra_cpu = (E[delta-bytes-read] / 50 MB/s) ~ negligible
```
Two information-theoretic facts:
1. Tail bytes after saturation carry mostly redundant information (Theme A: rate-distortion at near-zero D), but not zero. A test that fails *after* failure 31 in an ULTRA-budget run has `H = log2(N_total)` worth of "did this also fail" bits per failure, plus maybe failure-template novelty.
2. Holding the producer in pipe-block costs O(1) memory per call (the pipe buffer was allocated regardless), so V59 is strictly Pareto-better than V56 on the "accidentally needed the tail" branch.

## Concrete proposal for Redcon

Files touched (production source modified only in a follow-up - this note proposes design):
- `redcon/cmd/runner.py` - add a `BackpressureGate` collaborator passed into `run_command`.
- `redcon/cmd/compressors/base.py` - introduce optional `incremental_feed(chunk: bytes) -> Saturation` for compressors that know how to declare saturation.
- `redcon/cmd/pipeline.py` - install the gate from `BudgetHint` (compute target_bytes based on tier + max_output_tokens).

API sketch:
```python
class Saturation(Enum):
    NEED_MORE = 0       # keep draining
    SOFT_FULL = 1       # info-theoretic ceiling reached; pause but keep alive
    HARD_FULL = 2       # user asked for compact; safe to kill

class BackpressureGate:
    def __init__(self, *, target_bytes: int, resume_on_request: bool = False):
        self.target_bytes = target_bytes
        self.state = Saturation.NEED_MORE
        self.paused_at: float | None = None

    def update(self, total_bytes: int, compressor_signal: Saturation) -> None:
        # combine byte-count cap with compressor hint
        ...

# in run_command's main loop:
streams_to_read = streams if gate.state == Saturation.NEED_MORE else []
ready = _select_ready(streams_to_read, deadline - now)
if gate.state == Saturation.SOFT_FULL:
    # do NOT close stdout; pipe fills, subprocess blocks on write
    if time.monotonic() - gate.paused_at > _PAUSE_LINGER:
        _terminate(proc)
        break
elif gate.state == Saturation.HARD_FULL:
    _terminate(proc)
    break
```
Compressors that have a natural saturation point (pytest: failure-count cap, grep: distinct-path-count cap, git diff: per-file tally complete + N-th hunk body discarded) implement `incremental_feed`. Compressors without one stay on the V56 path. The gate is opt-in per compressor and the cache key is unaffected (decision happens *during* run, not in argv).

## Estimated impact
- Token reduction: zero direct delta on COMPACT/ULTRA output (V56 already truncates), but rescues the "pinned VERBOSE under tight budget" case where today we either (a) read the full 16 MiB then compress, or (b) cap-truncate and lose tail. Effective wall-time reduction: median pytest run with `--maxfail=N`-style early stop emulated for binaries that don't support it. Estimated 5-15% wall-time savings on pytest/grep/find on huge repos where the compressor saturates within the first 5-20% of output.
- Latency: cold parse unchanged. Warm: smaller buffers to scan -> faster compress step. Cold-start unaffected (no new imports; `select` and `signal` already imported).
- Cache layers: untouched. Determinism preserved provided the saturation predicate is byte-deterministic (counts of \n, regex hits) - which it is for the targets listed.
- Affects: `git diff`, `git log`, `pytest`, `cargo_test`, `npm_test`, `go_test`, `grep / rg --json`, `find`. Not useful for `git_status`, `ls`, `tree` (small outputs), `lint`, `kubectl` (already small or bounded), `docker` (log-pointer tier handles).

## Implementation cost
- Lines of code: ~120 new lines in runner (gate, pause/resume branch), ~20 LOC per opted-in compressor for the `incremental_feed` shim (4-6 compressors initially -> ~120 LOC).
- New runtime deps: none. Uses stdlib `select`, `os`, `signal`. No network, no embeddings.
- Risks:
  1. Determinism: the saturation point depends on chunk arrival order. If `select` returns chunks at slightly different boundaries between runs (in practice it does), the boundary at which `incremental_feed` flips to `SOFT_FULL` may shift by a few bytes -> different paused-tail content. Mitigation: snapshot decision strictly on cumulative byte count or completed-line count, not on chunk boundary.
  2. Robustness: subprocesses with custom SIGPIPE handling may not exit cleanly on `terminate`. Tested cases needed: `git` (ignores SIGPIPE in pager mode), `pytest` (well-behaved), `rg --json` (well-behaved), `find` (well-behaved), `docker logs` (forwards from container - blocks differently).
  3. Must-preserve: a compressor that signals `SOFT_FULL` early but in fact had unsatisfied `must_preserve_patterns` would silently violate quality. Quality harness must add a "feed truncated stream" mode that simulates pause-then-kill and asserts patterns still hold on the tier the gate produces.
  4. Resume path is essentially never exercised in normal MCP use (one-shot calls); over-engineering risk if we ship the resume API but no caller uses it.

## Disqualifiers / why this might be wrong
1. **64 KiB is tiny.** The producer often outpaces us anyway and the pipe stays near-full at all times. In that regime, "pause" amounts to "block immediately on next write", which is observationally identical to V56's SIGTERM (which fires within `_KILL_GRACE_SECONDS = 1.0`). The recoverable-state advantage shrinks to a sub-second window.
2. **No agent currently asks "give me the rest" mid-call.** The MCP surface is request-response; there is no protocol for "I changed my mind, resume the paused subprocess". Without that protocol, V59 is V56 with extra branches. We would need a second MCP tool (`redcon_resume`) tied to a transient subprocess handle, which crosses the local-first / stateless boundary the project currently keeps.
3. **Compressors aren't incremental.** Per `runner.py` docstring: "Per-compressor streaming ... is intentionally NOT here - the cost of that refactor isn't justified by current parse times". V59's saturation signal *requires* incremental feeding. So this vector pays the cost of that refactor whose payoff was already deemed insufficient at the parse-time level. The argument has to shift from "save parse time" to "save subprocess wall-time" - which is real but smaller than the refactor cost.
4. **SIGPIPE semantics differ across binaries.** `git` historically ignored SIGPIPE in pager mode; `find` exits clean; `docker` inherits container behavior. Tail-preservation guarantees evaporate if the binary is in the "queues forever, ignores SIGPIPE on pause-and-kill" bucket.
5. **Already half-implemented as V56 + log-pointer.** Log-pointer at 1 MiB and the existing `cap_hit_reason` SIGTERM branch in `runner.py:199-202` cover the "obviously too much output" case. The remaining gap V59 fills is narrow: outputs between ~ pipe-capacity and the compressor's saturation point, inside the 1 MiB log-pointer threshold. That's a thin sliver.

## Verdict
- Novelty: low (pipe backpressure is textbook; the conjunction with compressor-saturation signals is mildly new in this project but conceptually a small extension of V56).
- Feasibility: medium. The runner-side gate is straightforward (~half a day). The incremental-feed protocol on compressors is the real cost (~3-5 days for 4-6 compressors plus quality-harness updates).
- Estimated speed of prototype: 2-3 days for runner gate + one compressor (pytest) end-to-end with quality tests.
- Recommend prototype: conditional - on first completing V56 (early-kill) and measuring the residual tail-loss it actually causes in production traces. If V56 truncates >5% of agent-relevant tail bytes (meaning the compressor produces materially different output when fed cap-truncated input vs full input), V59 is justified. If V56's truncation is invisible (the compressor was already saturated), V59 is academic and we should not pay the incremental-feed refactor cost.
