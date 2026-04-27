# V25: Markov chain over MCP call sequences, prefetch likely-next compressed views

## Hypothesis

Agent tool-call streams are not i.i.d. - they exhibit strong, low-order
sequential dependencies. After `git status` the agent almost always
issues `git diff` (or `git diff --staged`). After `pytest -x` on a red
result the agent typically follows with `grep` or `git diff` on the
failing path. We claim that a first-order Markov chain over the
canonicalised argv (already produced by `rewrite_argv`) is enough to
predict the *next* `redcon_run` call with sufficient probability that a
background prefetch warming the deterministic cache wins net latency,
even allowing for wasted work on mispredictions. Operating point:
prefetch only when `P(next | current) >= tau` for a learned threshold
`tau` (initial guess `tau = 0.55`). Concretely: at session level on a
realistic agent trace the prefetch fires on roughly 25-40% of calls,
hits roughly 60-75% of those firings, and on a hit converts a 200-2000
ms cold parse into a <1 ms cache lookup.

## Theoretical basis

A first-order Markov chain over a discrete state space `S` of canonical
argv tuples gives transition matrix `P[i,j] = P(X_{t+1} = j | X_t = i)`.
Maximum-likelihood estimate from observed counts:

```
P_hat[i,j] = N(i -> j) / sum_k N(i -> k)
```

with Laplace add-alpha smoothing for sparsity (`alpha = 1` by default).
The MLE is consistent (Anderson-Goodman 1957): for each row, as
`sum_k N(i -> k) -> infinity`, `P_hat[i,:] -> P[i,:]` in probability.

Expected latency saving per call from prefetching the top-arg-max
prediction `j* = argmax_j P_hat[i,j]` of probability `p* = P_hat[i,j*]`:

```
E[latency_saved | i]
  =  P(prefetch fires) * P(prefetch hits | fires) * (T_cold - T_warm)
     - P(prefetch fires) * P(prefetch wrong | fires) * P(wrong hurts) * T_waste
```

With `T_cold` = cold parse time (200-2000 ms across compressors),
`T_warm` ~= 0.5 ms (cache lookup),
`T_waste` ~= subprocess+parse cost on a wrong call (200-2000 ms),
and `P(wrong hurts) ~= 0` if prefetch runs on a separate thread that
shares only the cache - so wasted work hurts CPU and disk only, not
agent latency. Under the threshold rule:

```
P(fires) = P(p* >= tau) ~= 0.30   (empirical for tau=0.55)
P(hits | fires) = E[p* | p* >= tau] ~= 0.70
```

so

```
E[saved per call] ~= 0.30 * 0.70 * (1000 - 1) ms ~= 210 ms / call
```

A 30-call session saves ~6 s of wall time at the agent surface. The
key inequality:

```
tau >= T_waste_amortised / (T_cold - T_warm)
```

If wasted work runs strictly off the agent's critical path (background
thread, never blocks `compress_command`), `T_waste_amortised` is
purely a CPU-cost term. Setting `tau` from the *off-path* CPU budget
(say 5% of session CPU) gives `tau ~= 0.4-0.6` for normal compressors
and `tau ~= 0.8` for log-pointer-tier candidates (since prefetching a
wrong huge-output command burns I/O even off-path).

Markov chain order rationale. A second-order (`P[i,h,j]`) chain
strictly dominates first-order in expected log-likelihood
(Csiszar-Talata 2006), but parameter count grows as `|S|^3`. With the
top-20 distinct calls (per the vector spec) we have at most
`20 * 20 = 400` first-order parameters and `8000` second-order; the
sample size in `run_history_cmd` after a few weeks of use is at most a
few thousand rows on a heavy user. AIC penalty
`2k - 2 ln L` strongly favours first-order at this sample size. We
therefore stay first-order until we collect >>10k rows, at which
point upgrading is mechanical.

## Concrete proposal for Redcon

Five additions, all opt-in and all backwards-compatible.

**1. Predictor module: `redcon/cmd/predictor.py` (new, ~80 LOC)**

Pure-Python, stdlib only. Reads the existing `run_history_cmd` table
(already populated when `record_history=True`). Builds the transition
matrix lazily on first call, caches it in-process, refreshes when the
row count grows by >=N (default 50).

```python
from collections import Counter, defaultdict
from typing import Iterable, Mapping

class CallPredictor:
    """First-order Markov over canonicalised argv strings."""
    def __init__(self, alpha: float = 1.0, tau: float = 0.55) -> None:
        self._counts: dict[str, Counter[str]] = defaultdict(Counter)
        self._tau = tau
        self._alpha = alpha
        self._dirty = True
        self._matrix: dict[str, dict[str, float]] = {}

    def fit(self, sessions: Iterable[list[str]]) -> None:
        for s in sessions:
            for a, b in zip(s, s[1:]):
                self._counts[a][b] += 1
        self._dirty = True

    def predict(self, current: str) -> tuple[str, float] | None:
        if self._dirty:
            self._build()
        row = self._matrix.get(current)
        if not row:
            return None
        nxt = max(row, key=row.get)
        return (nxt, row[nxt]) if row[nxt] >= self._tau else None
```

Sessions are derived from the `run_history_cmd` table by grouping
rows on `cwd` and a *gap-based* session bound (consecutive rows in the
same cwd within 10 minutes are in the same session). This is purely
deterministic post-hoc clustering on the existing table - no new
`session_id` column needed. The vector's "session_id" hypothesis is
supplied by the gap rule, not stored as a primary key.

**2. Schema check on the existing history (no migration needed)**

The vector asks us to confirm `(call_argv, timestamp, session_id)` are
recorded. Reading `redcon/cmd/history.py::_SCHEMA` shows the existing
columns are:

```
generated_at TEXT       -- timestamp (UTC ISO 8601)
command      TEXT       -- canonical command string  (== argv joined)
cwd          TEXT       -- workspace root
cache_digest TEXT       -- canonicalised key (rewriter-normalised)
... level / tokens / cache_hit / returncode / duration_seconds
```

So `(call_argv, timestamp)` are present (`command` plus
`generated_at`), and `cache_digest` is a stronger key than raw argv
because it's already canonicalised by `rewrite_argv`. There is **no**
`session_id`. The first-order chain operates on `cache_digest` (the
deterministic key), which ensures `git status -uno` and `git status`
collapse to the same state when the rewriter says they should. The
gap-based session bound replaces the missing column.

**3. Post-call hook in `redcon/cmd/pipeline.py`**

Insert after the existing cache write (line ~161 of pipeline.py):

```python
if effective_cache is not None:
    effective_cache[cache_key.digest] = report

# v25: speculative cache warm. Off by default; opt-in via env or hint.
if hint and getattr(hint, "prefetch_next", False):
    from redcon.cmd.predictor import schedule_prefetch
    schedule_prefetch(
        current_digest=cache_key.digest,
        cwd=cwd_path,
        cache=effective_cache,
        timeout=hint.prefetch_timeout_s or 5,
    )
```

`schedule_prefetch` resolves the predicted next call via the
`CallPredictor`, validates it is in the allow-list, and runs
`compress_command(...)` on a daemon `threading.Thread` so it never
blocks the calling response. Result lands in the same `effective_cache`
keyed by its own digest - the next real call hits cache.

**4. BudgetHint additions (`redcon/cmd/budget.py`)**

```python
@dataclass(frozen=True, slots=True)
class BudgetHint:
    ...
    prefetch_next: bool = False        # opt-in; default off
    prefetch_threshold: float = 0.55
    prefetch_timeout_s: int = 5
    prefetch_max_per_call: int = 1     # only top-1 in v25
```

Setting `prefetch_next=False` keeps current behaviour byte-identical
(crucial for BASELINE constraint #1 / #5). Tests that rely on
deterministic side-effect-free `compress_command` keep passing.

**5. Diagnostic CLI: `redcon predict --top-k 20`**

Reads `run_history_cmd`, prints the top-20 distinct argv states and the
top-3 outgoing transitions per state with smoothed probabilities. Used
to set `tau` empirically per repo. ~30 LOC.

**Pseudo-code for the prefetch worker**

```python
def schedule_prefetch(*, current_digest, cwd, cache, timeout):
    pred = _predictor_singleton(cwd)
    nxt = pred.predict(current_digest)
    if nxt is None:
        return
    next_digest, prob = nxt
    if next_digest in cache:           # already warm; skip
        return
    argv = pred.argv_for_digest(next_digest)
    if argv is None:
        return
    if not _is_safe_to_prefetch(argv): # block writes; reads only
        return

    def _worker() -> None:
        try:
            compress_command(
                argv,
                cwd=cwd,
                hint=BudgetHint(... prefetch_next=False),
                timeout_seconds=timeout,
            )
        except Exception:
            return  # silent: prefetch failure must not surface
    threading.Thread(target=_worker, daemon=True).start()
```

Safety filter `_is_safe_to_prefetch` allow-lists read-only commands by
schema family: `git status`, `git diff*`, `git log*`, `pytest`,
`grep`, `rg`, `find`, `ls`, `tree`, `lint *`, `kubectl get*`,
`docker ps*`. Anything that can mutate state (`git commit`, `pkg
install`, `docker run`) is excluded - the rewriter already exposes the
verb so this is one regex per schema.

## Estimated impact

- **Token reduction**: zero direct change. Cache contents are
  byte-identical; tokens-per-call unchanged. Indirect savings if a
  prefetch reveals a log-pointer-tier output before the agent commits
  to the call - no change today.
- **Latency**:
  - **Cold-start hit**: 200-2000 ms saved per *successful* prefetch
    (depends on which compressor; pytest ~1-2 s, git diff ~100-300 ms,
    grep ~50-200 ms). Honours BASELINE constraint #5 because the saved
    time is on a *future* call, not on the call performing the
    prefetch.
  - **Wrong prediction**: zero added latency on the agent path
    (background thread). ~one-call worth of CPU and disk waste per
    miss. Bounded by `prefetch_max_per_call=1` to avoid amplification.
  - **Session level**: with the back-of-envelope numbers above
    (`~210 ms saved / call`), a 30-call session saves ~6 s wall time.
    A 100-call long-running agent session saves ~20 s. This clears
    the "20% cold-start cut" breakthrough bar only on
    cold-start-heavy sessions; on warm-cache sessions the win is
    smaller because the predicted call is often already cached.
- **Affects**: `redcon/cmd/pipeline.py` (one new optional branch),
  `redcon/cmd/budget.py` (new fields), `redcon/cmd/history.py`
  (read-only consumer), new `redcon/cmd/predictor.py`. Cache layer is
  pure consumer - no new keying scheme, BASELINE constraint #6
  preserved.

## Implementation cost

- **LOC**: ~150 total. Predictor ~80, hint fields ~10, pipeline hook
  ~15, safety filter ~25, CLI ~30, tests/fixtures ~50.
- **New runtime deps**: none. Pure stdlib (`sqlite3`, `threading`,
  `collections`). Honours "no required network / no embeddings".
- **Risks to determinism**: `compress_command` itself remains
  deterministic same-input-same-output because the prefetch path
  *only writes the cache*. The cache is a `MutableMapping`; whether
  an entry is present at call time is observable behaviour (cache_hit
  flag, latency). Two mitigations:
  1. `prefetch_next=False` by default keeps test outputs and
     `cache_hit=False` byte-identical to status quo.
  2. When enabled, document that `cache_hit` may flip from False to
     True if a prefetch from the prior call landed in time. Tests
     that pin `cache_hit` must opt out via the hint.
- **Risks to robustness**: a misbehaving compressor could spawn a
  subprocess that eats CPU. Mitigation: timeout from the hint passed
  to the worker; daemon thread exits with the parent.
- **Risks to must-preserve**: none - we use the existing compressor
  pipeline unchanged; `must_preserve` invariants survive whether the
  call ran on the agent thread or a prefetch thread.

## Disqualifiers / why this might be wrong

1. **First-order Markov is too weak on real agent traces.** Real
   trajectories show second- and third-order dependencies: a
   "test-fail-grep-diff-edit" pattern is conditioned on three prior
   states, not one. A first-order chain assigns the wrong
   distribution after `git diff` because it averages over all the
   contexts in which `git diff` was previously called. Mitigation:
   bump to second-order once `>10k` samples accumulate; param count
   stays manageable (~8k transitions). Until then, expect mispredict
   rate higher than the back-of-envelope claims.
2. **Cache pollution may dominate.** Every wrong prefetch fills a
   cache slot that may evict a real entry under any future bounded
   cache. Today the in-memory cache is unbounded per process (see
   `_DEFAULT_CACHE`), so this doesn't bite immediately - but the
   moment we add an LRU bound (which V76 / SQLite WAL persistent
   cache will), wrong prefetches are negative-value at sub-50%
   accuracy. Mitigation: tag prefetched entries with a "speculative"
   bit and prefer evicting them over agent-driven entries.
3. **The predictor needs warm-up data per repo.** A fresh repo with
   <100 logged calls has high-variance MLEs; smoothed `tau=0.55`
   threshold rarely fires, so the feature does nothing. Mitigation:
   ship a default global prior trained on aggregate
   `redcon/symbols/` corpus (the new uncommitted directory) bundled
   with the package, then drift toward repo-local with a Bayesian
   update. This re-introduces a non-trivial dependency and may be
   considered scope creep.
4. **Already partially implemented in disguise.** The default
   in-process cache (`_DEFAULT_CACHE`) means *any* repeated call hits
   cache for free. A large fraction of the latency wins V25 claims
   may already be captured by re-runs of the same `git status` rather
   than a Markov-predicted next state. Empirical question: what
   fraction of `redcon_run` calls in real agent traces are "next
   state same as current state" (cache hit)? If >70%, V25 is dwarfed
   by the existing cache and the Markov chain is window dressing.
5. **Concurrency hazards in the cache.** `MutableMapping` writes from
   a daemon thread can race with the foreground call's writes. CPython
   dict ops are GIL-protected but the read-then-write pattern on
   line 102 (`cached = cache.get(...)`) followed by line 161 is not
   atomic. A racing prefetch could land a duplicate compute. Low
   correctness risk because both writes produce identical values
   (deterministic), but wasted work. Mitigation: a `threading.Lock`
   guarding the per-digest insert. Adds <5 LOC but breaks the
   existing "cache is a plain dict" type contract.
6. **Privacy / quotas.** Background subprocesses run *commands* the
   user did not explicitly authorise this turn. A `git diff` on
   private code is fine; a `kubectl get pods --all-namespaces` against
   a production cluster might burn API quota or emit audit-log noise.
   The allow-list addresses safety, not quota. Probably fine for
   read-only `git` and local linters, problematic for cloud-CLI
   compressors (kubectl, docker against remote, gcloud). Mitigation:
   default-deny network-touching schemas; opt-in per schema family.

## Verdict

- **Novelty**: low-medium. Speculative prefetch via Markov is a
  textbook OS / DB technique (Patterson et al. on informed prefetching,
  1995; Roy et al. on Markov-based file prefetching, 2002). Applying
  it to MCP-tool sequences is mildly novel because nobody else has
  shipped a deterministic per-call cache + history table that makes
  the predictor cheap. Distinctively cleaner than V21 (speculative
  emit) because it doesn't bloat the *current* tool result with a
  guess - the win and the cost are both deferred to the next call.
- **Feasibility**: high. All required pieces (per-process cache,
  cmd history table, deterministic cache key, argv canonicaliser)
  exist. The new code is ~150 LOC, no deps, no schema migration.
- **Estimated speed of prototype**: 2-3 days for a flag-gated
  diff/grep/pytest prefetcher with offline transition matrix and a
  fixture-based test of "predicted call hits cache on second
  invocation". 1 week to add the daemon-thread runtime path with
  proper concurrency tests, a CLI, and a recorded-trace replay
  harness.
- **Recommend prototype**: **conditional-on** measuring two
  quantities first on an existing `run_history_cmd` table:
  (a) the empirical cache-hit rate of the *next* call without any
      predictor (if already >70%, V25's win is marginal),
  (b) the top eigenvalue gap of the empirical transition matrix
      after smoothing - if the chain is close to uniform (gap < 0.1)
      the threshold rule almost never fires and there is no win.
  If both checks pass (cache miss rate >30% and `argmax P[i,:] >=
  0.55` for at least 25% of states), build it.
