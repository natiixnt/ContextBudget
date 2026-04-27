# V53: T-digest for log-line latency distribution summarisation

## Hypothesis

Several command outputs Redcon already wraps emit a *vector* of
per-event latencies that today either survive untouched (pytest
`--durations=N`, docker BuildKit step times) or are thrown away
entirely (per-line `elapsed_ms` in JSON access logs / API logs that
agents pipe through `redcon_run`). For these outputs the agent's
follow-up question is almost always distributional ("which P99 step is
slow?", "is the tail getting worse than yesterday?") not enumerative.
A streaming t-digest (Dunning, 2019) summarises an unbounded latency
stream with O(delta) memory and gives accurate quantiles - especially
extreme ones (P99, P99.9) where an evenly-binned histogram fails. We
claim that replacing the raw "top-N slowest plus floor" rendering with
a fixed 6-7-line quantile table (count, mean, P50, P90, P99, max,
optionally a histogram sparkline) saves ~90-95% of tokens *on the
latency-shaped portion of those outputs* and, importantly, gives the
agent a strictly more useful object than "first 10 by max". Net
compact-tier reduction across pytest+docker+a future
`access_log`/`json_log` compressor: ~3-5 absolute points (mostly
pulled by docker_build and a new JSON-log compressor; pytest gain is
small because Redcon already drops its body).

## Theoretical basis

A t-digest of compression parameter `delta` (typical `delta = 100`)
maintains an ordered list of weighted centroids `(m_i, w_i)` with the
scale-function constraint that the cumulative weight `q_i = (sum_{j<=i}
w_j) / W` satisfies

```
k(q_{i+1}) - k(q_i) <= 1                                    (1)
```

for the standard scale function `k(q) = (delta / (2 pi)) * arcsin(2q -
1)`. Equation (1) packs centroids tightly near `q -> 0` and `q -> 1`
and sparsely in the middle, which is *exactly* the regime latency
agents care about. Memory is bounded by the number of centroids:

```
|C| <= ceil(pi * delta / 2)                                 (2)
```

For `delta = 100`, `|C| <= 157`. Per-quantile error bound
(Dunning 2019, Theorem 1) for any `q` in (0,1):

```
|q_hat - q| <= (1 / delta) * (q (1-q))^{1/2}                (3)
```

so at `q = 0.99` and `delta = 100` the error is at most
`sqrt(0.0099)/100 ~= 9.95e-4` in quantile space. For `n` samples this
is empirically much tighter than equal-width bucket histograms which
are O(1/n_buckets) in *value* space and bias against tails.

Token-cost back-of-envelope. A pytest `--durations=20` block emits
roughly:

```
============= slowest 20 durations =============
0.42s call     tests/test_a.py::test_x
0.39s setup    tests/test_b.py::test_y
...   (20 lines, ~12-18 cl100k tokens each)
```

Twenty lines * ~15 tokens/line = ~300 tokens. The proposed digest
rendering ("count=N mean=M P50=p P90=p P99=p max=p") is a single line
of ~25 tokens; with one line of "slowest 3" preserved for grounding,
that's ~70 tokens. Saving on this *block alone*: ~230 tokens, ~77%.
This compresses *only* the durations block; over the entire pytest
output (often 2k-20k tokens raw) the contribution is smaller in
percentage terms but compounds with the existing pytest reductions.

For docker BuildKit a 30-step build emits ~30 step lines averaging
~14 tokens. Today Redcon shows first-6 + last-4 plus a "+N more"
ellipsis (~10 step lines). A digest table (count, mean, P50, P90, max,
"slow-3 named") sits at ~7 lines, ~80 tokens. Saving on the steps
block: ~70-80 tokens (~50%) plus the agent gets P90 step time, which
the current view does not surface at all.

For a hypothetical JSON-log compressor reading `{ts, path, status,
elapsed_ms}` lines: with N = 10k lines averaging ~30 tokens each = 300k
tokens raw. A digest replacement (count, P50, P90, P99, P99.9, max,
slow-5 paths) is ~50 tokens: 99.98% reduction. (This is the V53 win
that actually shows up; it is also where vector-spec honesty starts to
bite - this depends on shipping that compressor.)

Determinism. The standard k_1 / k_2 / k_3 t-digest variants are
*order-dependent*: the centroid set produced by `add(x_1) ; add(x_2)`
differs from `add(x_2) ; add(x_1)` near the boundaries. For BASELINE
constraint #1 (deterministic same-input-same-output) we therefore use
the `merging_digest` variant: buffer all input, sort by value, then
single-pass merge under the scale-function bound. The digest is then
a *pure function of the multiset of values*. The centroids and any
emitted quantiles become byte-identical across runs.

## Concrete proposal for Redcon

Three additions; only the first one is unconditionally a win, the
other two are explicitly conditional on later compressor work.

**1. New utility module: `redcon/cmd/_tdigest.py` (new, ~120 LOC)**

Pure-Python, stdlib-only `merging_digest`. No new dep:

```python
import math
from typing import Iterable

class MergingDigest:
    """Deterministic t-digest. Sort-then-merge; no incremental update."""
    __slots__ = ("delta", "_centroids")

    def __init__(self, delta: float = 100.0) -> None:
        self.delta = float(delta)
        self._centroids: list[tuple[float, float]] = []   # (mean, weight)

    @classmethod
    def from_values(cls, xs: Iterable[float], delta: float = 100.0) -> "MergingDigest":
        d = cls(delta)
        sorted_xs = sorted(float(x) for x in xs if x == x)  # drop NaNs
        if not sorted_xs:
            return d
        n = len(sorted_xs)
        out: list[tuple[float, float]] = []
        cum = 0.0
        cur_m, cur_w = sorted_xs[0], 1.0
        cum += 1.0
        for x in sorted_xs[1:]:
            q_left = (cum - cur_w) / n
            q_right = (cum + 1.0) / n
            if d._k(q_right) - d._k(q_left) <= 1.0:
                cur_m = (cur_m * cur_w + x) / (cur_w + 1.0)
                cur_w += 1.0
                cum += 1.0
            else:
                out.append((cur_m, cur_w))
                cur_m, cur_w = x, 1.0
                cum += 1.0
        out.append((cur_m, cur_w))
        d._centroids = out
        return d

    def _k(self, q: float) -> float:
        # Standard Dunning scale; clamp at the open endpoints.
        q = min(max(q, 1e-12), 1 - 1e-12)
        return (self.delta / (2.0 * math.pi)) * math.asin(2.0 * q - 1.0)

    def quantile(self, q: float) -> float | None:
        if not self._centroids: return None
        W = sum(w for _, w in self._centroids)
        target = q * W
        cum = 0.0
        for m, w in self._centroids:
            if cum + w >= target:
                return m
            cum += w
        return self._centroids[-1][0]

    def summary(self) -> dict:
        n = sum(w for _, w in self._centroids)
        if not self._centroids: return {"n": 0}
        return {
            "n": int(n),
            "mean": sum(m * w for m, w in self._centroids) / n,
            "p50": self.quantile(0.50),
            "p90": self.quantile(0.90),
            "p99": self.quantile(0.99),
            "max": max(m for m, _ in self._centroids),
        }
```

Footprint and cost: 0 deps, deterministic, ~30us for n=10k on a laptop.

**2. Hook into the pytest compressor (`redcon/cmd/compressors/pytest_compressor.py`)**

Today this compressor does not parse the `--durations` block at all -
the regex set jumps from FAILURES to short-summary to footer. Add a
small parser that detects a header matching
`^=+\s+slowest\s+\d+\s+durations?\s+=+$`, accumulates lines matching
`^(?P<dur>[\d.]+)s\s+(?P<phase>setup|call|teardown)\s+(?P<name>\S.*)$`
until a blank line or new `=+` divider, and feeds the durations into
`MergingDigest.from_values`. At COMPACT level emit:

```
slowest: n=20 mean=0.18s p50=0.14s p90=0.41s p99=1.32s max=1.40s
top: tests/test_a.py::test_x 1.40s, tests/test_b.py::test_y 0.84s, tests/test_c.py::test_z 0.71s
```

(Two lines, ~55 tokens.) ULTRA collapses to one line. VERBOSE keeps
the original block (current behaviour). Must-preserve patterns: at
least the first slow test name (so the agent can grep), plus the `n=`
count.

**3. New compressor stub: `redcon/cmd/compressors/json_log_compressor.py`**

This is where the vector pays its rent. Triggered when stdin/stdout is
`jq`/`tail`/`cat` over a `.jsonl` or `.log` file *and* >=80% of the
non-blank lines parse as JSON objects with a numeric field whose name
matches `r"^(elapsed|duration|latency|response_time|took)(_ms|_us|_ns|_s)?$"`.
Strategy:

```python
def compress_json_log(text, ctx):
    rows = [json.loads(l) for l in text.splitlines() if l.startswith("{")]
    keys = _detect_latency_key(rows)        # returns (key, unit)
    durs = [r[keys[0]] * keys[1] for r in rows if keys[0] in r]   # to ms
    digest = MergingDigest.from_values(durs, delta=100.0)
    s = digest.summary()
    # group-by path/status if present; one digest per group, top-K only
    groups = _topk_by_path(rows, k=5)
    return _format(s, groups, level=ctx.level)
```

Output shape (example, status quantiles per route):

```
json_log: n=12,840 mean=42.1ms p50=18ms p90=110ms p99=412ms max=2.1s
hot: GET /api/foo n=4,201 p50=21 p99=480 ; POST /api/bar n=1,202 p99=1.8s
status: 2xx=12,400 4xx=380 5xx=60
```

Must-preserve: the `n=` count, the `5xx=*` field if non-zero, and the
top route name when n_route > 5% of total.

## Estimated impact

- **Token reduction**:
  - On the pytest `--durations` block alone: ~75% (300 -> ~70 tokens).
    Whole-pytest impact: <2 absolute pp because the durations block is
    a small fraction of typical pytest output, and many pytest runs
    don't include `--durations`.
  - On docker_build steps block: ~50% (140 -> 70 tokens) for builds
    >12 steps, with a *capability* gain (P90 step time wasn't visible
    at all before).
  - On a JSON-log compressor (does not exist yet): the headline gain.
    For latency-line-dominated logs, raw inputs of 50k-500k tokens
    drop to ~80 tokens. This is the V53-shaped breakthrough but it
    rides on shipping (3) above; without the compressor, the saving
    is on inputs Redcon doesn't currently see.
  - Compact-tier reduction floor across the existing 11 compressors:
    moves by maybe 0.5-1 absolute pp (pytest, docker). To clear the
    "5 absolute pp across multiple compressors" breakthrough bar
    requires the JSON-log compressor.
- **Latency**: parsing+merging ~30us per 10k samples. Parsing a 10k-row
  jsonl through the proposed compressor is dominated by `json.loads`
  not the digest; the t-digest itself is free at this scale.
  Cold-start unaffected: `_tdigest.py` is import-on-use only inside
  the affected compressors, lazy under existing pattern.
- **Affects**:
  - `redcon/cmd/compressors/pytest_compressor.py` (~60 LOC added).
  - `redcon/cmd/compressors/docker_compressor.py` (~30 LOC: digest
    over `step.duration_seconds` already collected).
  - New `redcon/cmd/compressors/json_log_compressor.py` (~150 LOC),
    plus registry entry.
  - `redcon/cmd/_tdigest.py` (~120 LOC).
  - Quality harness gets new must-preserve patterns; no schema
    changes to `CompressedOutput`.

## Implementation cost

- **LOC**: ~360 total (utility 120 + pytest hook 60 + docker hook 30 +
  json_log 150). Tests: ~150 more.
- **New runtime deps**: none. No tdigest C library is required;
  mainstream pure-Python implementation is 120 LOC. Honours
  "no required network / no embeddings".
- **Risks to determinism**: the order-dependence of incremental
  t-digests would violate BASELINE #1. Using the merging variant
  (sort-then-single-pass) is the explicit fix - quantiles depend
  only on the multiset of inputs, not insertion order. Float
  arithmetic associativity must still be respected: we sort
  ascending, accumulate `cur_m * cur_w + x` left-to-right, never
  fold subtotals. Property-based test: shuffle input, assert
  byte-identical digest summary.
- **Risks to robustness**: NaN / infinity in latency fields (a
  malformed JSON log with `"elapsed_ms": "N/A"`). Filter at
  `from_values` time; treat parse failures as "skip row". Garbage
  binary input never reaches the compressor (the JSON-log gate
  requires >=80% JSON-parseable lines).
- **Risks to must-preserve**: the digest *deletes* per-row
  identification by design. Any agent task that needs "give me row
  with elapsed_ms = X" loses. Mitigation: at COMPACT level always
  echo the top-3 slow rows verbatim alongside the digest, and
  declare those as must-preserve. ULTRA may drop them.

## Disqualifiers / why this might be wrong

1. **Most existing compressors don't emit per-event latencies.** Be
   honest: of the 11 shipped compressors, only docker_build has a
   true vector of per-step latencies; pytest emits a single global
   duration unless the user opts in to `--durations=N` (probably
   <30% of runs); pkg_install / cargo_test / npm_test / go_test only
   record a *total*. Lint, grep, find, ls, git_*, kubectl have no
   timing at all. So the t-digest is a hammer in search of nails on
   today's surface area; it pays only when you *also* ship the
   JSON-log compressor (which is V65 territory under Theme G, not
   V53).
2. **For docker, n is small.** A typical 30-step build has only 30
   samples; `delta=100` produces a digest with up to ~160 centroids
   - i.e. one centroid per sample, no compression in centroid space.
   The t-digest mathematics degrade to "a sorted list of all values".
   The token win comes purely from the fixed-shape rendering, not
   from algorithmic compression. A simple "min/median/P90/max"
   computation over the sorted list is equivalent and much simpler.
   t-digest is overkill for n<200.
3. **Agents may want per-step grounding more than quantiles.** "P99
   step time = 14.2s" doesn't tell the agent *which step*. Any
   serious follow-up still needs the slow-step name. The proposed
   "top-3 by latency" line covers the common case but a cunning
   adversarial test could ask "what is the median?" and expect the
   median *step* not the median *time*. The digest answers the
   second; agents may want the first.
4. **Already partially covered by the log-pointer tier.** When raw
   stdout >1 MiB, Redcon spills to a file and emits a tail-30
   pointer. For real "JSON access log" inputs that the JSON-log
   compressor would target, the log-pointer tier already kicks in
   *and* the agent can re-run a `grep` against the spilled file at
   their leisure. So the V53 win is squeezed between (a) small
   inputs where t-digest is overkill (disqualifier #2) and (b)
   large inputs where log-pointer already protected the budget.
   The remaining sweet spot is 10k-1MiB raw, latency-shaped logs
   - real but narrow.
5. **Determinism subtlety: float reductions are not associative.**
   Computing `mean = sum(m*w) / sum(w)` left-to-right is
   deterministic *given a fixed input order*. We sort ascending
   before merging, so the order is fixed - but a NaN in the input
   would have been silently dropped. We must commit to a single
   well-defined NaN policy and test it. Otherwise two callers with
   the same multiset (one with NaN injected by a flaky parser)
   produce different digests.
6. **Distribution-summary outputs may break must-preserve.** The
   compact-tier quality harness checks regex patterns *survive*. A
   digest summary will not contain the literal of the slowest line
   the original output had. Today's pytest must-preserve patterns
   are derived from failures, not durations, so the gate passes;
   but the moment we declare "the top-3 names must survive" we
   reintroduce per-row content and the digest is no longer the only
   thing emitted. The hybrid is fine but it caps the saving at
   "digest + top-3" which is what the impact estimate already
   assumes.

## Verdict

- **Novelty**: medium. T-digest itself is well-known (Dunning 2019,
  used in Datadog, M3DB, Apache Druid). Applying it deterministically
  inside a *context-budget* shim for AI agents - in particular the
  insight that quantile rendering is the right shape for an LLM that
  will only consume a few tens of tokens of latency information - is
  mildly novel. The deterministic merging variant is the load-bearing
  detail; off-the-shelf t-digest implementations would violate
  BASELINE #1 silently.
- **Feasibility**: high for the utility module and the docker hook;
  medium for pytest (need a small new section parser); the JSON-log
  compressor is a separate scope (V65-flavoured) - not unfeasible but
  not strictly V53.
- **Estimated speed of prototype**: 2 days for the utility +
  property-based determinism tests + docker hook integration.
  +1 day for the pytest `--durations` parser. +3-5 days if we also
  ship the JSON-log compressor. Quality harness updates ~half a day.
- **Recommend prototype**: **conditional-on** (a) docker_build users
  agreeing P90 step time is more useful than first-6/last-4 (cheap
  preference survey on existing benchmark fixtures) AND (b) the
  JSON-log compressor being on the roadmap (V65). If both hold, V53
  is a clean deterministic 120-LOC dependency that pays per
  compressor that needs it. If neither holds, V53 is window dressing
  on docker and the recommendation is **no**.
