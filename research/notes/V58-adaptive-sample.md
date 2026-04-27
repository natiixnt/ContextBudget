# V58: Adaptive sampling rate driven by running info-entropy estimate

## Hypothesis
For high-volume listing-like outputs (`grep`, `find`, `ls -R`, JSON-Lines logs)
the running n-gram entropy of accumulated lines stabilises after a small
prefix K*. Each new line beyond K* adds little marginal information about the
distribution. We therefore propose to read the first K* lines fully and
**stratified-sample** the tail at rate `r = max(r_floor, kept_info_target)`,
producing a representative subsample whose KL divergence from the empirical
full-stream distribution is bounded. The prediction: 80-90% byte reduction
while preserving most distinct path/key tokens, executed *during* streaming
(saves wall-clock as well as tokens, unlike post-hoc compressors).

## Theoretical basis
Let line `L_k` map to a multiset of `n`-grams `G_k`. Build empirical
distribution `p_k(g) = count_k(g) / total_k`. The Shannon entropy of the
accumulated distribution

    H_k = - sum_g p_k(g) log2 p_k(g)

is monotone-increasing-then-stabilising under the natural concentration
of the source (Glivenko-Cantelli on n-gram frequencies). The marginal
information content of line k under the previous model is
`I_k = - sum_{g in G_k} log2 p_{k-1}(g)` (Laplace-smoothed).
By the asymptotic equipartition property, once `H_k` ceases changing
(`|H_k - H_{k+W}| / H_k < eps` for some window `W`), the conditional
distribution `p(L_{k+1} | L_{1..k})` is stationary; sampling at rate `r`
preserves it up to standard reservoir-sampling variance `O(1/sqrt(r * (N-K*)))`.

Back-of-envelope (3 fixtures generated locally; numbers below come from
a real run, not a guess):

    fixture            N    K*  H[K*]    H[N]    H_change_in_tail
    find-5000       5000   165  7.439    7.854   +5.6%
    grep-3000       3000   105  7.578    7.711   +1.8%
    ls-R-4000       4160   100  4.884    4.926   +0.9%

K* is "first k where 50-line forward window changes accumulated H by
<0.5%". So entropy stabilises within the first ~2-5% of stream length
across all three.

## Concrete proposal for Redcon
**Files touched:**
- `redcon/cmd/runner.py` - the only place that streams subprocess bytes.
  Add an opt-in `adaptive_sample: AdaptiveSampleSpec | None = None` to
  `RunRequest`. When set, the `_append_capped` path replaces line-buffered
  capture with a per-line state machine that maintains a 3-gram counter
  and decides keep/drop per line.
- `redcon/cmd/sampling.py` (new, ~120 LoC) - holds `RunningEntropy`
  estimator and `StratifiedTailSampler`.
- `redcon/cmd/registry.py` - declare which schemas opt in
  (`grep`, `find`, `ls`, `tree`, log-pointer fallback for unknown stdout).

API sketch:

```python
@dataclass
class AdaptiveSampleSpec:
    kstar_window: int = 50
    kstar_eps: float = 0.005
    tail_keep_rate: float = 0.10
    tail_strategy: Literal["uniform", "stratified-hash"] = "stratified-hash"
    min_kstar_lines: int = 64
    floor_lines: int = 32  # always keep first N regardless of H

class RunningEntropy:
    def update(self, line: bytes) -> None: ...   # adds 3-grams
    def stable(self) -> bool: ...                 # window check

class StratifiedTailSampler:
    def keep(self, line: bytes) -> bool:
        # hash of last path-segment-like token mod 1/rate == 0
        key = _last_seg(line)
        return (mmh3(key) & 0x7fffffff) % self._mod == 0
```

In runner the loop becomes line-aware (split chunks on `\n`, push lines
through the entropy estimator until `stable()`, then start dropping).
Determinism is preserved because the hash is fixed-seed mmh3 of the
canonical last-segment.

## Estimated impact
Numbers from the simulation (`research/notes/V58-adaptive-sample.md`-driving
script reproduced inline above):

| fixture     | raw bytes | uniform 1-in-10 saving | stratified-hash saving | unique-key coverage uniform / stratified |
|-------------|-----------|------------------------|------------------------|------------------------------------------|
| find-5000   | 161 901   | 87.0pp                 | 87.5pp                 | 13.0% / 12.5%                            |
| grep-3000   | 107 479   | 86.8pp                 | 88.7pp                 | 25.8% / 24.5%                            |
| ls-R-4000   |  47 786   | 88.0pp                 | 86.9pp                 | 100% / 100%                              |

But the Redcon baseline already runs structural compressors on the *full*
stream after capture. Comparing to the **current** COMPACT tier on the
same fixtures (measured with the actual `parse_find` / `parse_grep` /
`parse_ls` plus `_format_compact` from this repo):

| fixture     | compact_tokens vs raw_tokens | reduction | V58 best |
|-------------|------------------------------|-----------|----------|
| find-5000   |    445 / 40 475              | 98.9pp    | 87.5pp   |
| grep-3000   | 12 497 / 26 870              | 53.5pp    | 88.7pp   |
| ls-R-4000   |    976 / 11 947              | 91.8pp    | 88.0pp   |

V58 only **wins on grep-3000** (+35.2pp absolute over the current
`grep_compressor.py` COMPACT formatter). On find and ls -R the
existing structural compressors are already strictly better than
information-theoretic sampling, because they go from "list each path"
to "histogram + first-N per dir" - that is itself a near-optimal
sufficient statistic, far below the n-gram entropy.

Where V58 is genuinely useful is the **streaming/wall-clock** axis,
not the final-token axis: today the runner buffers up to 16 MiB of
output and only then hands to a parser. With V58 we could `_terminate`
the subprocess earlier or drop lines off the wire, cutting subprocess
duration on `grep -r` over giant trees from O(matches) to O(K* +
rate*matches). That is a ~10x latency win on pathological greps.

- Token reduction (final output): +35pp on grep-only vs current COMPACT;
  **regression** on find (-11pp) and ls -R (-4pp). Net: not a win on
  tokens.
- Latency: -50% to -85% wall-clock on streams >100k lines (skip reading
  and decoding bytes we'll drop). Cold start unchanged.
- Affects `runner.py` (touch carefully), `grep_compressor.py`, listings.
  Cache layer unchanged since the cache key is on argv+cwd, and we'd
  still cache the compressed result.

## Implementation cost
- ~150 lines: `sampling.py` + `runner.py` integration + test fixtures.
- New deps: optional `mmh3` (~20 KB wheel) for fast deterministic hash;
  fall back to `hashlib.blake2b(digest_size=8)` for the no-deps path.
  Neither breaks "no required network / no embeddings".
- Risks:
  - **Determinism**: stratified hash + line ordering is deterministic
    iff the subprocess emits identical byte order. It does, but the
    per-line buffering changes capture timing, not bytes.
  - **Robustness**: `must_preserve_patterns` could fail if a critical
    path lands in the dropped tail. Mitigation: union with regex-based
    "always-keep" predicates per schema (e.g. lines containing
    "FAILED" / "error:" / paths matching task keywords).
  - **Outliers in tail**: the *whole risk* of V58. Stratified-hash
    over basename keeps coverage of the basename-vocabulary but loses
    full paths. For `find` where every line is unique that's lethal.

## Disqualifiers / why this might be wrong
1. **Existing structural compressors dominate** on find and ls -R.
   They don't sample - they *summarise* with a sufficient statistic
   (extension histogram, dir-grouping). N-gram entropy sampling is a
   strictly weaker model than parsing-and-grouping.
2. **`find` lines are unique by construction.** Every dropped line
   drops a unique path; in our run, uniform 1-in-10 sampling kept
   only 13% of paths. No amount of stratification fixes "every line
   is its own equivalence class".
3. **The streaming-latency win is fictional in practice.** Redcon
   already has a 16 MiB hard cap, a `_terminate(proc)` path, and a
   log-pointer tier for >1 MiB outputs (BASELINE.md, line 32).
   Subprocesses spitting 50 MB of grep are already cut off, and the
   pointer-tier emits tail-30 cheaply. V58 saves wall-clock only in
   the narrow band 1-16 MiB of clean line-formatted output, which the
   current BASELINE already shows is small.
4. **n-gram entropy is the wrong statistic.** Grep agents care about
   distinct *paths* and which match patterns hit - a token-level set
   sketch (HyperLogLog, V52) is a much better fit, and the current
   `grep_compressor` already does the equivalent by group-by-path
   then "keep first 3 per file".
5. **Determinism via mmh3 is fragile.** If we ever change the hash
   library, every cached output changes. Hash-based stratified
   sampling can be reproduced with python's `hashlib`, but the
   moment a user's terminal pipes through a tee or numbered prefix,
   the "last segment" key shifts and the cache key cannot detect it.
6. **The marginal-info argument breaks for `grep` and `find`.** Our
   run shows tail marginal info is *higher* than head (1.055x for
   find, 1.021x for grep) because new path basenames keep arriving.
   The hypothesis that tail info drops is empirically false on two
   of three fixtures. Only `ls -R` shows tail info < head info,
   and that's the case the existing histogram already nails.

## Verdict
- Novelty: low (subsumed by existing structural compressors + log-pointer
  tier; reservoir-sampling variants explored in V51, HLL in V52,
  rolling-hash dedup in V60, anytime in V57).
- Feasibility: medium (touching `runner.py` is non-trivial; keeping
  determinism under streaming requires care).
- Estimated speed of prototype: 1-2 days for sampler + tests; another
  day to validate quality harness invariants per schema.
- Recommend prototype: **no**. Empirically the tail-info hypothesis
  fails on grep and find in our fixtures; existing compressors beat
  V58 by 11-45pp on two of three target schemas; the only win
  (grep +35pp) is better captured by an HLL distinct-path sketch
  (V52) plus the existing group-by-file formatter, with no risk to
  the deterministic cache key. Mark as instructive boundary case:
  *"adaptive sampling without a structural model loses to a
  structural model with no sampling"*.
