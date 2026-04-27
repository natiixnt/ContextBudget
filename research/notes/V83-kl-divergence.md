# V83: KL-divergence between line-distribution before/after compression

## Hypothesis
The current quality harness in `redcon/cmd/quality.py` enforces three things:
must-preserve regexes (presence), reduction floor (size), and determinism
(byte-stability). It cannot detect a class of silent regression where a
compressor preserves *one* representative of a line category but drops
the rest, distorting the *frequency* the agent uses to reason. Concretely:
a pytest output of 10 fails / 100 passes / 5 warnings can collapse to
"1 fail / passes / 5 warnings" and still satisfy must-preserve (every
failure name appears once) while losing the fact that failures are >1.
Similarly a docker log compressor can keep one ERROR and drop nine more.

The claim: if we classify every line of raw and compact output into a
small categorical alphabet C (e.g. `{pass, fail, warn, error, info,
trace, blank, other}`), and compute D_KL(p_raw || p_compact) over that
alphabet, a regression that drops a whole frequency mass shows up as a
spike in D_KL even when must-preserve still passes. Threshold X is
calibrated empirically per compressor on the existing fixture corpus.
Above X is a quality fail, same severity as a must-preserve miss.

This composes with must-preserve, it does not replace it: must-preserve
catches *presence* of named facts; KL catches *frequency drift* of
unnamed-but-classified line types. Both are needed because COMPACT is
allowed to drop counts but not allowed to misrepresent ratios beyond a
budget. ULTRA is exempt (already exempt from must-preserve per
BASELINE.md).

## Theoretical basis
For categorical distributions p (raw) and q (compact) over a finite
alphabet C with |C|=k,

```
D_KL(p || q) = sum_{c in C} p_c * log(p_c / q_c)        (nats)
```

Because compressors are intentionally lossy on count, q_c can be 0 even
when p_c > 0, which sends D_KL to infinity. Standard fix: Laplace
smoothing with pseudocount alpha,

```
p_c' = (n_p_c + alpha) / (N_p + alpha*k)
q_c' = (n_q_c + alpha) / (N_q + alpha*k)
```

Choose alpha = 0.5 (Jeffreys prior) so D_KL is bounded by log((N+0.5*k)/0.5)
which for N=200, k=8 gives a hard ceiling of ~6.0 nats. Threshold X is
set per-compressor as the 95th percentile of D_KL observed on the
existing fixture corpus, plus a safety margin. A regression that pushes
the metric past the calibrated X is statistically a tail event.

Worked example. Pytest fixture, 10 fail / 100 pass / 5 warn / 0 other,
N=115. Raw distribution after smoothing (alpha=0.5):

```
p_fail = 10.5/117 = 0.0897
p_pass = 100.5/117 = 0.8590
p_warn = 5.5/117  = 0.0470
p_othr = 0.5/117  = 0.0043
```

Suppose compressor A correctly emits the count line (compact retains
proportions in summary): q ~= p, D_KL ~ 0.0006 nats (numerical noise).

Suppose compressor B drops the count and emits one fail + 5 warns +
"...": n_q = (1, 0, 5, 0), N_q=6, after smoothing:

```
q_fail = 1.5/8 = 0.1875
q_pass = 0.5/8 = 0.0625
q_warn = 5.5/8 = 0.6875
q_othr = 0.5/8 = 0.0625
```

```
D_KL = 0.0897*log(0.0897/0.1875) + 0.859*log(0.859/0.0625)
     + 0.047*log(0.047/0.6875) + 0.0043*log(0.0043/0.0625)
     ~= -0.0660 + 2.249 - 0.126 - 0.0115
     ~= 2.045 nats.
```

That is a 4-bit-wide change in the categorical posterior, which is
exactly the regime an agent would mis-read. Calibrated X around 0.30 nats
flags B but not A.

Connection to the existing must-preserve check: must-preserve is the
indicator `1{q_c > 0 whenever pattern matches >=1 line in raw}` for a
hand-picked subset of C. KL is the *quantitative* extension over a
larger, automatic C. The two are dual: must-preserve is a presence
hard-floor; KL is a frequency soft-budget.

## Concrete proposal for Redcon
Add a check to the harness, not to runtime. Scope is `redcon/cmd/quality.py`
and a small helper alongside it. Production path is unchanged.

Files affected:
- `redcon/cmd/quality.py` - add `_line_distribution()`, `_kl_divergence()`,
  per-compressor calibrated threshold table, new `LevelReport` field
  `kl_nats: float`, new aggregate field `kl_ok: bool`, surface in
  `failures()`. Default threshold loaded from `KL_THRESHOLDS` keyed by
  schema. New helper does not touch any compressor.
- `redcon/cmd/compressors/base.py` - optional `line_classifier`
  attribute on the `Compressor` Protocol (default: shared
  `default_classifier` returning `{pass, fail, warn, error, info,
  trace, blank, other}`). Compressor authors override only when their
  semantics diverge (e.g. git_diff would use `{add, del, ctx, hunk_hdr,
  file_hdr, blank, other}`).
- `tests/test_cmd_quality.py` - add 5 fixtures (pytest, git_diff, grep,
  docker, lint) and assert `kl_ok` and `kl_nats < threshold`. Existing
  fixtures already cover the corpus; we only add KL assertions.

Pseudo-code (~40 lines, drop into `quality.py`):

```python
DEFAULT_CATEGORIES = ("pass", "fail", "warn", "error", "info",
                      "trace", "blank", "other")

# Calibrated 95th-percentile KL on current fixtures, plus 0.10 nats margin.
KL_THRESHOLDS: dict[str, float] = {
    "pytest":   0.40, "git_diff":  0.55, "grep":      0.35,
    "docker":   0.50, "lint":      0.40, "kubectl":   0.45,
    # default for un-tuned compressors
    "_default": 0.60,
}

_PASS = re.compile(r"\bPASS(ED)?\b|::.*PASSED|\bok\b|\bgreen\b", re.I)
_FAIL = re.compile(r"\bFAIL(ED|URE)?\b|::.*FAILED|\bAssertionError\b", re.I)
_WARN = re.compile(r"\bWARN(ING)?\b", re.I)
_ERR  = re.compile(r"\bERROR\b|\bERR\b|^E\s|Traceback", re.I)

def default_classifier(line: str) -> str:
    s = line.strip()
    if not s: return "blank"
    if _ERR.search(s):  return "error"
    if _FAIL.search(s): return "fail"
    if _WARN.search(s): return "warn"
    if _PASS.search(s): return "pass"
    if s.startswith(("DEBUG", "TRACE")): return "trace"
    if s.startswith(("INFO", "[INFO]")): return "info"
    return "other"

def _distribution(text: str, classifier, alpha: float = 0.5) -> dict[str, float]:
    counts = {c: 0 for c in DEFAULT_CATEGORIES}
    for line in text.splitlines():
        counts[classifier(line)] = counts.get(classifier(line), 0) + 1
    k = len(counts); n = sum(counts.values())
    return {c: (v + alpha) / (n + alpha*k) for c, v in counts.items()}

def kl_nats(p: dict[str, float], q: dict[str, float]) -> float:
    return sum(pv * math.log(pv / q[c]) for c, pv in p.items() if pv > 0)

# Wired in _check_level just after the determinism block:
classifier = getattr(compressor, "line_classifier", default_classifier)
p = _distribution(raw_stdout.decode("utf-8", "replace"), classifier)
q = _distribution(first.text, classifier)
kl = kl_nats(p, q)
threshold_kl = KL_THRESHOLDS.get(compressor.schema, KL_THRESHOLDS["_default"])
kl_ok = (level == CompressionLevel.ULTRA) or (kl <= threshold_kl)
```

Surface in `QualityCheck.passed`:

```python
if level.level != CompressionLevel.ULTRA and not level.kl_ok:
    return False
```

And in `failures()`:

```python
if level.level != CompressionLevel.ULTRA and not level.kl_ok:
    out.append(f"{self.schema}/{level.level.value}: KL drift "
               f"{level.kl_nats:.2f} nats > floor {level.threshold_kl:.2f}")
```

Calibration step (one-shot, recorded in repo):
1. Run benchmark fixture corpus, collect `(schema, level, kl_nats)` for
   every passing run.
2. For each schema, take 95th percentile across (fixture x level) pairs
   at COMPACT and VERBOSE.
3. Add 0.10 nats margin. Commit table.
4. Add a script `redcon/cmd/quality_calibrate.py` to regenerate the
   table when fixtures change. Strictly opt-in, never runs in tests.

## Estimated impact
- Token reduction: 0. This is a quality vector, like V81/V82/V85/V86,
  not a compression vector.
- Quality coverage:
  - On the synthetic pytest example above (B drops counts), must-preserve
    passes (one fail name survives). KL=2.05 nats, well past 0.40 -> caught.
  - On a synthetic git_diff regression that emits all `+` lines but drops
    all `-` lines: must-preserve loses (file headers may survive but
    pattern `^-{3} ` for deletions fails too, so already caught). KL
    flags it earlier, with `D_KL(p_diff || q_diff) ~ 1.8 nats`, useful
    as a more legible failure signal.
  - On a regression where the docker compressor drops half the ERROR
    lines but keeps one: must-preserve (which only matches the *presence*
    of "ERROR") passes; KL flags `p_err=0.30, q_err=0.10`,
    contribution ~0.33 nats by itself, total well past floor.
- Latency: O(L) per fixture per level where L = total lines, with a
  single regex pass per line. On a 5000-line raw fixture, ~1-2 ms.
  Quality harness already takes seconds per fixture; this is negligible
  and only runs in CI / dev tooling.
- Affects: `quality.py` only. No production code path changes, no cache
  changes, no `must_preserve` semantics changes.
- Composability with existing checks: orthogonal. A regression can fail
  any of {must-preserve, threshold, determinism, robustness, KL}
  independently.

## Implementation cost
- ~80-120 LOC: classifier + helpers (~40), wiring into `_check_level`
  (~15), threshold table + calibration script (~30), tests (~30).
- New runtime deps: none. `math.log` and `re` are stdlib. Does not
  break "no embeddings, no required network".
- Risks to determinism: classifier must be a pure function. The above
  uses only stdlib `re`, no randomness. Safe.
- Risks to robustness: pathological inputs (binary garbage, 5000 newlines)
  must not crash the classifier. `default_classifier` guards on
  `line.strip()` and matches only printable patterns; any decode error
  is handled by `decode("utf-8", "replace")`. Already covered by the
  existing robustness loop in `_check_robustness`.
- Risks to must-preserve guarantees: none. KL is additive, not
  subtractive: a compressor that passes must-preserve and reduction can
  fail KL, which is a *new* failure mode, but a compressor that passes
  must-preserve, reduction, and KL is at least as preserving as before.
- Threshold calibration risk: thresholds are corpus-derived. If the
  fixture corpus shifts (new failure shapes), thresholds drift. Mitigation:
  recalibration script under `redcon/cmd/quality_calibrate.py`,
  opt-in, regenerates `KL_THRESHOLDS` table when run with `--update`.

## Disqualifiers / why this might be wrong
1. The classifier is the metric. A bad classifier produces meaningless
   KL. Default-categories work for log-shaped output (pytest, lint,
   docker, kubectl), but break for git_diff (no PASS/FAIL there, all
   lines fall into "other"). Mitigation requires per-schema classifiers,
   which doubles the implementation scope. Without them, the metric
   silently goes to ~0 for git_diff because both p and q collapse to
   `other=1.0`, giving false confidence. So the proposal only really
   pays off for log-shaped compressors and needs a separate diff-shaped
   classifier (`{add, del, ctx, hunk, hdr, blank}`) to be useful for
   git_diff.
2. Smoothing choice influences threshold. alpha=0.5 (Jeffreys) is
   defensible but arbitrary; alpha=1 (Laplace) gives different KL
   numbers and would reshuffle thresholds. As long as one alpha is
   pinned in code, results are deterministic - but this is a knob with
   no first-principles answer, which makes the chosen X harder to defend
   in review.
3. KL is asymmetric. D_KL(raw || compact) and D_KL(compact || raw) are
   different and pick up different regressions. The proposal uses
   forward-KL (raw || compact), which penalises compact for putting
   mass where raw has none (false positives in category attribution).
   Reverse-KL would penalise compact for missing categories raw has
   (mode dropping). Mode dropping is the failure mode we actually care
   about most for a compressor, so reverse-KL might be the better
   choice. Or use Jensen-Shannon (symmetric, bounded by log 2 ~= 0.69).
   This is a legitimate design concern and the answer is empirical.
4. May be subsumed by V81 (Hypothesis property fuzzing) if the property
   "frequency of category X in compact is within 2x of raw" is
   expressible as a Hypothesis invariant. V81 with that invariant is
   strictly more powerful (it generates inputs); KL only checks fixtures.
   But V81 is much higher cost and KL is a cheap drop-in - composable,
   not redundant.
5. Compressors are *supposed* to drop counts when reducing 100 PASS
   lines to "passes: 100". The KL metric punishes this even though
   it's correct behaviour (compact has 0 PASS lines vs raw's 100, q_pass
   collapses to alpha-only). Mitigation: count the *summary line itself*
   as N copies of its category when it matches a `count: N <category>`
   pattern. This is doable (~10 LOC of regex) but adds another
   compressor-specific knob. Without it, every healthy summarising
   compressor will trip the KL floor and we calibrate the threshold so
   high that regressions slip through. The first-cut threshold of 0.40
   nats is barely tight enough; with un-expanded summary lines it might
   need to climb to 1.5 nats, defeating the purpose.
6. Already partially solved by reduction floor + must-preserve. A
   compressor that loses 90% of fail-class lines without losing the
   names will pass must-preserve and fail reduction (because reductions
   that drop the right things tend to be more uniform). The narrow gap
   where KL adds signal is "kept some count, dropped others, names all
   preserved" - real but not gigantic.

## Verdict
- Novelty: medium. KL between source and compressed-source distributions
  is a textbook lossy-compression diagnostic (rate-distortion under a
  KL-divergence distortion measure, Cover & Thomas ch. 10). Applying it
  as a *gate* in the quality harness alongside must-preserve and
  threshold-floor is not in BASELINE.md, and the dual presence/frequency
  framing is clean. Not a token-reduction breakthrough by the BASELINE
  bar (>=5 pp on multiple compressors), but it's a legitimate quality
  axis. Pure micro-improvement on the harness, comparable to V81/V86.
- Feasibility: high. ~100 LOC, stdlib only, deterministic, drops in
  next to existing harness, doesn't touch production. Calibration is
  straightforward against existing fixtures.
- Estimated speed of prototype: 0.5 - 1 day for the log-shaped
  classifier path; +0.5 day for per-schema classifiers (git_diff,
  http_log) and counted-summary handling.
- Recommend prototype: conditional on (a) per-schema classifiers for
  the non-log-shaped compressors (otherwise git_diff and others get a
  free pass and the metric is misleading), and (b) handling
  count-summary lines so that healthy summarising compressors don't
  trip the gate. Without those two pieces, the metric reports a number
  but doesn't reliably catch regressions.
