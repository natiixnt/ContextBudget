# V07: Algorithmic mutual information between consecutive tool calls in the same agent turn

## Hypothesis

Consecutive tool calls in an agent turn are not independent. When `git status`
is followed by `git diff`, when `grep foo` is followed by reading one of the
matched files, or when `pytest -v` is followed by `pytest <nodeid> -v`, the
second output is partly determined by the first. If we can characterise the
mutual information `I(A; B)` and it is large in token-space, a *prior-conditioned*
compressor for the second call could elide the part that is already in the
agent's context, yielding a cross-call dimension of compression that
compounds on top of the existing single-output tiers.

The real measurement (below) shows the picture is more subtle than the
hypothesis assumes: gzip-surrogate MI is large for some pairs, but
**Redcon's compact tier already extracts most of the path-overlap MI**, so
the *residual* MI available to a conditional compressor is concentrated
in a different place than naive intuition suggests.

## Theoretical basis

Take outputs A and B as discrete random variables drawn from a joint
distribution determined by the agent's task and the repo state. By the
chain rule for entropy,
```
H(A, B) = H(A) + H(B | A)
I(A; B) = H(B) - H(B | A) = H(A) + H(B) - H(A, B) >= 0
```
A token-level upper bound on what a conditional compressor can save on B is
`H(B) - H(B | A)`. We can lower-bound this with an algorithmic surrogate
(Bennett-Gacs-Li-Vitanyi-Zurek, 1998): for any computable compressor `C`,
```
I_C(A; B) := |C(B)| - |C(B | A)| <= H(B) - H(B | A) + O(1)
```
where `|C(B | A)|` is the length when A is supplied as side information
(approximated by `|C(A . sep . B)| - |C(A)|`, the conditional gzip trick).

Back-of-envelope on the three target pairs (paths counted as ~5
cl100k tokens each on average):

```
P1 (status -> diff): in a typical agent turn touching k files,
    diff repeats each path twice (header line + summary). Status mentions
    each path once. Naive token-MI per shared path = 2 path-tokens
    in B. With k=6 files and 5 tokens/path: 6*2*5 = 60 token upper bound
    on raw-form savings.

P2 (grep -> read): grep output names the path *m* times (one per match
    line, and the agent typically Reads only one file). Read output
    starts with one path header. So shared path-tokens in B = 1*5 = 5.
    The bulk of B is code, which is *not* in A.

P3 (pytest -v -> pytest <nodeid>): every line of the focused rerun is a
    structural duplicate of a line in the full run (session header,
    rootdir, traceback frames). Empirically 80%+ of B's lines appear
    verbatim in A.
```

So a-priori we expect P3 to dominate, P1 to give a moderate
path-elision win, and P2 to give a small per-call alias win.

## Empirical measurement on this repo

I built the pairs from this repo's live state plus one synthetic pytest
fixture. Each pair was passed through the actual Redcon compressors at
the COMPACT tier; I also recorded RAW token counts so the experiment also
covers VERBOSE / passthrough scenarios. Two conditional compressors were
tested:
- **alias-cond**: replace any path mentioned in A and re-mentioned in B
  with `#1`, `#2`, ... and emit a one-line legend. Only emit if it
  strictly reduces token count.
- **line-drop**: drop every B-line whose stripped form appears verbatim
  in A (upper-bound oracle - drops too much in general but useful as a
  ceiling).

Plus one negative control (P4: `git log` then `kubectl get pods` -
unrelated families).

Token counts use Redcon's `_tokens_lite.estimate_tokens` (cl100k
chars/4 surrogate, identical to `redcon.core.tokens.estimate_tokens`).

| Pair | Tier | T_A | T_B | T_B\|A alias-cond | T_B\|A line-drop | gzip-surrogate I(A;B) |
|---|---|---:|---:|---:|---:|---:|
| P1 status->diff | RAW     |   17 |  396 |  396 (0.0%)  |  395 (+0.3%)  |  +8.4% |
| P1 status->diff | COMPACT |   19 |  112 |  112 (0.0%)  |  112 (0.0%)   |        |
| P2 grep->Read   | RAW     |   12 |  275 |  275 (0.0%)  |  275 (0.0%)   |  +9.3% |
| P2 grep->Read   | COMPACT |   19 |  275 |  275 (0.0%)  |  275 (0.0%)   |        |
| P3 pytest full->nodeid | RAW     | 1770 |  244 |  235 (+3.7%) |   46 (+81.1%) | +90.4% |
| P3 pytest full->nodeid | COMPACT |   60 |   59 |   59 (0.0%)  |   12 (+79.7%) |        |
| P4 git-log->kubectl (-) | RAW    |   22 |   33 |   33 (0.0%)  |   33 (0.0%)   | +24.7% |

Aggregates across the three positive pairs:
```
RAW:     total T_B=915  alias-cond=906 (+1.0%)  line-drop=716 (+21.7%)
COMPACT: total T_B=446  alias-cond=446 (+0.0%)  line-drop=399 (+10.5%)
```

### What this tells us

1. **The gzip surrogate confirms that I(A; B) is large for P3 (90%) and
   moderate for P1, P2 (8-9%).** The hypothesis that consecutive-call MI
   exists is correct.
2. **But Redcon's compact tier already destroys most of that MI in
   token-space for P1 and P2.** The diff compressor reformats status's
   `M path` into `M path: +N -M`, and the read-window only repeats one
   path (the file header) - the redundancy is concentrated in path
   strings, and the compact format already minimises path repetition.
3. **The remaining cross-call redundancy at compact tier is
   pytest-shaped**: header chrome (`platform darwin --`, `rootdir: ...`,
   `collected N items`) and exact traceback lines that are duplicated
   between a full run and a focused rerun. Line-drop saves ~80% of B's
   compact tokens on P3.
4. **P2 (grep -> Read) has *zero* token-MI in either tier under our
   alias-cond rewrite.** The path appears once in B's header line and
   once in A; replacing it with `#1` saves 5 tokens but the legend costs
   8 tokens, so it's negative. To win on P2 the conditional compressor
   would need to drop the path header from Read entirely (relying on A
   for provenance), saving ~6 tokens. That's small in absolute terms
   but consistent: 1-3% per Read after a grep.
5. **Negative control P4 has 25% gzip MI but 0 token-overlap** -
   gzip-MI is dominated by surface tokens like newlines and English
   that re-appear across any tool output, so it overstates the
   exploitable signal. The token-level measurement is the honest one.

## Concrete proposal for Redcon

Add a *prior-conditioned* compression hook. The unit is `CompressionReport`
(already returned by `compress_command`); we extend it with a low-cost
`prior_index` that summarises the parts of its formatted output a
follow-up compressor can elide.

### Files

- `redcon/cmd/types.py`: add `PriorIndex` dataclass.
- `redcon/cmd/pipeline.py`: thread an optional `prior: CompressionReport | None`
  through `compress_command(...)`; pass it into `CompressorContext`.
- `redcon/cmd/compressors/base.py`: add `prior: PriorIndex | None`
  field to `CompressorContext`.
- `redcon/cmd/compressors/{git_diff,pytest_compressor,grep_compressor,listing_compressor}.py`:
  use `ctx.prior` to elide redundant chrome.
- `redcon/cmd/cache.py`: extend cache key with `prior.digest` so a
  conditioned-compression of the same B does not collide with the
  unconditioned one.

### API sketch

```python
@dataclass(frozen=True, slots=True)
class PriorIndex:
    schema: str                           # "git_status", "pytest", ...
    paths: frozenset[str]                 # paths the agent already saw verbatim
    chrome_lines: frozenset[str]          # session-header lines, etc.
    digest: str                           # 16-hex prefix of sha256

def build_prior_index(report: CompressionReport) -> PriorIndex: ...

# pipeline.py
def compress_command(command, *, prior: CompressionReport | None = None, ...):
    ...
    prior_index = build_prior_index(prior) if prior else None
    ctx = CompressorContext(..., prior=prior_index)
    cache_key = build_cache_key(argv, cwd_path, prior_digest=getattr(prior_index, "digest", ""))
    ...

# compressors/git_diff.py - inside _format(...)
def _format(result, level, prior):
    lines = []
    for f in result.files:
        path_repr = f"#{prior.paths_alias[f.path]}" if prior and f.path in prior.paths else f.path
        ...
```

### Per-compressor exploitation rules (each is small + deterministic)

| Compressor | Conditioning rule when prior matches |
|---|---|
| `git_diff`   | If prior is `git_status` and a path appears in both, emit `M #k: +N -M` referring to status's index. Saves ~5 tok per shared file. |
| `pytest`     | If prior is `pytest` (any argv variant), drop session-header lines (`platform`, `rootdir`, `collected N items`) and any FAILURES section line that is byte-identical. Drops 30-80% of B compact tokens on focused-rerun pattern. |
| `grep`       | If prior is `git_status`/`find`/`ls` and the search root is a subset, suppress paths in B that already appeared in the prior listing (`group: <listing-id>`). |
| `listing`    | If prior is `find`/`ls` covering same root, emit only path delta. |
| Generic      | If `prior.chrome_lines` covers >=50% of B's first 5 lines, drop them. |

### Rejection policy

The conditional compressor must produce a strict reduction or fall back
to the unconditioned formatter. Quality harness is extended: each
compressor declares `prior_compatible_schemas` and the harness runs
the prior+B path against fixtures, asserting (a) must-preserve still
holds in the conditioned form *with the prior text concatenated*, and
(b) `T_B|A < T_B`.

### Determinism / cache

`cache.build_cache_key` becomes a strict superset: when `prior_digest`
is empty, behaviour is bit-for-bit identical to today (BASELINE
constraint #6).

## Estimated impact

Empirical on the 3 measured pairs:
- **P3 (pytest follow-up)**: token reduction `-80% absolute` on B at
  both RAW and COMPACT tiers when the prior is the previous pytest run.
  This is the dominant case. (Compact baseline already at 73.8%
  reduction on raw->compact; conditioning takes B from 59 to ~12
  tokens, so the *full pipeline* reduction on B vs raw becomes
  ~95% in this scenario.)
- **P1 (diff after status)**: 0% in current compact form. To realise
  the small (~5%) wins predicted by the gzip surrogate, the diff
  compressor's compact format would have to be *reshaped* to put paths
  on dedicated lines so they can be aliased. Marginal absolute, ~3-5
  tokens per shared file - **not breakthrough**.
- **P2 (Read after grep)**: 1-3% per call from dropping the path
  header. Trivial in absolute terms.
- **Aggregate session win**: depends entirely on what fraction of an
  agent's turns include a pytest-followup pattern. If 20% of turns hit
  P3, total session reduction `~ 0.2 * 0.8 * compact_pytest_share ~
  3-5pp` of the agent's turn-level token spend.

Latency: one extra hash pass over the prior's formatted text per
follow-up call, plus an O(|paths|) set lookup. <1ms in practice. Cold
start unaffected.

Affects: `pipeline.py`, `cache.py`, `git_diff.py`,
`pytest_compressor.py`, `grep_compressor.py`, `listing_compressor.py`,
quality harness.

## Implementation cost

- Lines of code: roughly 250 (60 in pipeline/types/cache, ~30 per
  conditional compressor, ~50 in quality harness).
- New runtime deps: none. Pure stdlib + existing tokeniser.
- Risks:
  - Determinism: easily preserved (deterministic prior digest in cache key).
  - Must-preserve: needs harness extension to verify patterns still
    hold *given* the prior text is in context. Without that change,
    a compressor that drops a chrome line could violate must-preserve
    for downstream regex-checkers. Concretely: the test harness must
    score `prior.text + "\n" + b.text` against
    `must_preserve_patterns`, not just `b.text`.
  - Robustness: if the agent didn't actually retain the prior text
    (e.g. it was truncated by an outer budgeter), the conditioned B is
    incomprehensible. Mitigation: cap savings so B remains
    self-explanatory at the expense of 1 line of chrome (e.g. always
    keep the FAILED test header line in B).
- Cache: new key flavour, but a strict superset; no migration needed.

## Disqualifiers / why this might be wrong

1. **Three of the four real-world pairs already have ~0 exploitable
   token-MI at compact tier.** Only the pytest-follow-up pattern is
   genuinely lucrative, and that's a special case of an idea already
   listed in the "open frontier" of BASELINE.md: "Snapshot deltas vs
   prior `redcon_run` invocations of the same command on the same
   repo" (V47). What I'm proposing is a generalisation, but the
   empirical win concentrates on V47's exact pattern (`pytest -v` then
   `pytest <subset> -v` is approximately a same-command-on-same-state
   snapshot).
2. **Gzip-surrogate MI overstates the win.** P4 (negative control) has
   25% gzip MI but 0 token-overlap. The chars/4 token estimator is
   coarse; on a real cl100k tokenizer the savings could shift by
   10-20% in either direction. We must validate with `redcon.core.tokens`
   (tiktoken) before claiming a real reduction.
3. **The agent may not actually have A in context any more.** Outer
   budgeters can evict prior tool results; if B is conditioned on
   evicted A, comprehension breaks. This requires either (a) coupling
   to the outer budget so we know whether A is retained, or (b) keeping
   B self-contained at the cost of forfeiting most of the savings.
   Option (a) is out of scope for Redcon (we don't see the LLM's
   context). Option (b) caps the realisable win.
4. **Cache key explosion.** If we key on `(argv, cwd, prior_digest)`,
   any agent that varies its prior calls dilutes cache reuse. For
   pytest-followup specifically the prior is "pytest -v on this repo
   at HEAD", which is itself cache-stable, so this risk is manageable
   - but for generic conditioning it's a real cost.
5. **Already partly subsumed by V47 (snapshot deltas) and V25 (Markov
   call sequences). My measurement says the value of V07 boils down to
   the pytest-followup case, which V47 covers more directly. V07 only
   adds value beyond V47 for the small wins on P1/P2, which are 1-5%.

## Verdict

- Novelty: medium. The information-theoretic framing is novel for this
  codebase and the measurement is informative (it bounds the residual
  MI after compact-tier compression - a result that is interesting
  even as a negative). But the *implementable* win is largely captured
  by V47.
- Feasibility: high for the pytest-followup case (a pytest-aware
  delta-compressor is a 100-LOC change). Medium for the general
  prior-threading API (250 LOC, harness changes).
- Estimated speed of prototype: 2-3 days for the pytest-followup
  delta-compressor + quality fixtures; 1 week for the full
  prior-threading API across the four target compressors.
- Recommend prototype: conditional-on-X. Build the pytest-followup
  delta compressor (subsumed by V47 anyway, and the empirical case
  for it is strong: 80% additional reduction on B). Skip the general
  prior-threading API for git_diff and grep until we have session
  trace data showing those patterns dominate enough turns to matter
  - the aggregate reduction at compact tier is +0.0% on P1 and P2,
  which fails BASELINE's >=5pp breakthrough bar.

### Honest takeaway

The most important finding from this vector is *negative*: Redcon's
existing compact tier extracts the bulk of cross-call token-MI for
status->diff and grep->read pairs *without* needing prior conditioning,
because the compressor format itself eliminates the redundant surface
forms (e.g. dropping path repetition inside the diff hunk header by
reshaping to one-line-per-file). The dimension of compression that
*does* compound across calls is the case where the second call's
output is *structurally a subset* of the first's (focused pytest
rerun). That is V47's territory. V07's contribution is to put a
number on how little the "general" cross-call dictionary buys after
compact has run, and to redirect effort toward V47 / V25.
