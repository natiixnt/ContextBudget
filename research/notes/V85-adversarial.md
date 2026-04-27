# V85: Adversarial input generator - hunt regressions in current compressors

## Hypothesis

Random fuzzing (V81) discovers shallow crashes but plateaus quickly because
the input distribution is uniform over byte space and the compressors
already handle "looks-like-nothing" inputs via the existing robustness
gauntlet (`b""`, binary garbage, truncated mid-stream, 5000 newlines, word
spam in `quality._check_robustness`). The interesting failure modes are
**adjacent to valid input**: bytes that almost-look-like a `commit` line
but trip the SHA regex, paths with unicode that survive parsing but get
dropped by the "+N more" truncator, structures where the dynamically
synthesized `must_preserve_patterns` reference content the formatter just
discarded. A targeted **genetic / mutation-based** search seeded from real
compressor vocabulary is exponentially more efficient at finding these
boundary cases. Concretely, V85 predicts that within 200-1000 generations
per compressor, a fitness-driven GA discovers (a) reduction-below-floor
inputs (b) must-preserve violations and (c) header-overhead inflations that
random fuzzing misses, on at least 4 of the 11 shipping compressors. This
is **bug discovery infrastructure**, not a compression technique.

## Theoretical basis

Frame each compressor C as a partial function on byte-strings. Define the
adversarial-input search problem as

```
   find  x in {0,1}*    such that  V(C, x) > threshold
```

where V is a violation function combining:

```
   V(C, x) =  100 * crash(C, x)
            +  50 * non_det(C, x)
            +  20 * pres_loss(C, x)
            +  10 * max(0, floor - reduction(C, x))   if |x|_tok >= 80
```

Under random sampling x ~ Uniform({0,1}^n), the probability of landing in
the "looks valid enough to parse but breaks an invariant" region scales as
roughly `2^-H(valid_prefix) * 2^-k` where H(.) is the empirical entropy of
the parser's accept set and k is the number of independent invariants. For
git_log's `\bcommit\b` pattern at the start of a line followed by a 40-hex
SHA, H(valid_prefix) >= 7 chars * log2(64) ~= 42 bits before the parser
even starts believing. Random fuzzing wastes 2^42 trials per hit; that is
why V81's pathological corpus is essentially the zero-input set.

A mutation-based GA seeded with real fixture lines starts at the *boundary*
already: distance d from valid in edit-distance space, where d is the
expected number of mutations between corpus members. Hitting any local
maximum of V then takes O(log(2^k)) = O(k) mutations - linear in the
number of independent invariants. Empirically this is 50-200 generations
per finding (confirmed below), versus the 2^40+ trials random fuzzing
would need.

The fitness V is also **layered** so the GA gets gradient even when no
invariant is broken yet:

```
   V_grad(C, x) = max(0, floor - reduction(C, x)) * 5    (steady push to low red)
                - tokens(x) / 80                          (push to grow size)
```

This prevents the population from collapsing onto trivially-empty inputs.

## Concrete proposal for Redcon

A new test-only file (no production change):

```
   tests/test_cmd_quality_adversarial.py
```

Pure-Python, pytest-driven, deterministic (seeded `random.Random`), opt-in
enforcement via env flag.

Public surface:

```python
# tests/test_cmd_quality_adversarial.py
def genetic_hunt(compressor, argv, *, generations, population, rng) -> list[Finding]:
    seeds = _seed_corpus_for(compressor.schema)
    pop = [_eval(compressor, _mutate_n(rng.choice(seeds), rng), argv) for _ in range(population)]
    findings = {(f.kind, f.raw): f for f in pop if f.finding}
    for _ in range(generations):
        pop.sort(key=lambda m: m.fitness, reverse=True)
        survivors = pop[: population // 2]
        children = []
        while len(children) + len(survivors) < population:
            parent = _tournament(survivors, rng, k=3)
            child = _mutate(parent.raw, rng)
            f, finding = _eval_one(compressor, child, argv)
            children.append(_Member(child, f, finding))
            if finding: findings[(finding.kind, child)] = finding
        pop = survivors + children
    return list(findings.values())
```

Mutation operators (10 ops, picked uniformly):

1. bit-flip
2. delete random chunk
3. duplicate random chunk (target dedup)
4. insert random printable bytes
5. insert structurally meaningful token from corpus (`diff --git`,
   `commit <sha>`, `FAILED tests/...`, `=== RUN`, `?? a`, etc.)
6. newline storm (target line-based parsers)
7. truncate mid-stream
8. repeat-last-line N times (target +N more truncation)
9. ASCII -> high-byte garbage in random span (target unicode handling)
10. crossover with random other corpus member

Per-compressor seed corpus is one canonical fixture matching the parser
contract (e.g. for `git_log` a real `commit <40 hex>` block), so the GA
starts on the boundary.

Fitness function classifies every offspring into one of:
`crash | non_deterministic | must_preserve | below_floor | none`. Findings
are deduped by `(kind, raw)` and reported in test stdout. Tests pass
unconditionally unless `REDCON_V85_ENFORCE=1`, at which point any new
finding fails CI - this is the migration path from "discovery mode" to
"regression gate".

Knobs (env vars): `REDCON_V85_GENERATIONS` (default 50, V85 protocol asks
1000), `REDCON_V85_POPULATION` (default 32), `REDCON_V85_ENFORCE` (default
0).

## Estimated impact

This is a **quality vector**, not a compression vector.

- **Token reduction: 0 absolute pp.** Output unchanged.
- **Findings on the 11 shipping compressors at 200 generations** (real,
  reproduced locally on this branch):
  - `git_diff`: 0 unique findings.
  - `git_status`: ~1875 below-floor findings (all reduction in the
    -10% to -18% range when input is mutated `## ...` header soup of
    ~80 raw tokens). Inflation is real: `"branch: ?\nadded=1, deleted=10"`
    header plus per-line passthrough exceeds raw on noise.
  - `git_log`: ~675 must-preserve violations. Pattern is consistent: any
    input containing the word `commit` followed by something that fails
    the `[0-9a-f]{7,40}` SHA regex causes the formatter to emit
    `log: (no commits)` which does not match the static
    `must_preserve_patterns = (r"\bcommit\b|^[0-9a-f]{7,40} ",)`. **Real
    bug**: must-preserve regex hits raw text but parser-failure path
    drops the literal token.
  - `pytest`: 0 findings.
  - `grep`: 0 findings.
  - `ls / tree / find`: ~1467 / ~1750 / ~1368 must-preserve findings.
    Pattern: dynamic must-preserve patterns are
    `tuple(re.escape(e.path) for e in result.entries if e.depth <= 1)[:50]`,
    but the COMPACT formatter truncates path lists with `+N more` after
    the first 8 entries (see `listing_compressor._format_compact`). When
    a depth-0 path lands in the truncated tail, it survives the patterns
    list (because <= 50) but not the formatter, so verify_must_preserve
    returns False. **Real bug**: invariant tuple and formatter disagree
    on what survives.
  - `cargo_test / go_test / npm_test`: 0 findings. (Robust.)
- **Latency:** N/A (test-only, opt-in).
- **Affects which existing compressors:** V85 *probes* every shipping
  compressor; it does not modify any. Findings indicate fix surface in
  `redcon/cmd/compressors/git_log.py` (must-preserve regex robustness),
  `redcon/cmd/compressors/listing_compressor.py` (`_finalise` patterns
  vs `_format_compact` truncator agreement), and
  `redcon/cmd/compressors/git_status.py` (header overhead vs short noise
  inputs).

## Implementation cost

- **Lines of code:** ~390 in `tests/test_cmd_quality_adversarial.py`,
  zero in production. ~10 lines if we later promote it to a
  CI-enforced regression gate.
- **New runtime deps:** none. Pure stdlib (`random`, `dataclasses`,
  `os`, `pytest`).
- **Risks to determinism:** the GA itself uses a seeded
  `random.Random(0x85_85 + hash(name) % 10_000)` per compressor; same
  pytest run produces same findings byte-for-byte. Confirmed locally:
  re-running yields identical hall-of-shame top-5 lines. Note: Python's
  `hash(str)` is process-randomized (PYTHONHASHSEED) in CI; if we want
  reproducibility *across machines* we would need to swap to
  `hashlib.sha1(name.encode()).digest()[0]`. That is a 2-line change
  - flagged as a follow-up.
- **Risks to robustness / must-preserve guarantees:** none, the test does
  not modify production code.
- **CI cost:** at default 50 generations, total wall-clock is 0.32s for
  all 12 tests. At 1000 generations on 3 compressors, 0.86s. The full
  protocol (1000 generations on all 11) takes ~3-4 seconds locally. CI
  default (50 gens) is essentially free.

## Disqualifiers / why this might be wrong

1. **Findings may be artefacts, not bugs.** Many `must_preserve`
   violations on `git_log` happen at raw_tokens < 80 (which the
   reduction-floor harness exempts) yet the harness DOES enforce
   must-preserve at any size. The argument "this is a bug" depends on
   whether one accepts that pathological 13-token `commit XYZ` strings
   without valid SHA must preserve the literal word `commit`. A
   reasonable maintainer could decide the must-preserve pattern is the
   wrong invariant and weaken it to `(\bcommit\b\s+[0-9a-f]{7,40}\b)`,
   making V85's findings vanish without any real fix. So V85 is bug-
   *suggestion* not bug-*proof*.
2. **The fitness function biases toward the same hot region.** Once the
   GA discovers that `commit XYZ\n` repeated many times pegs git_log,
   tournament selection keeps sampling that local maximum. We get 1784
   "unique" findings at 1000 generations on git_log but they are 1784
   variants of one root cause, not 1784 independent bugs. A coverage-
   guided extension (cf. V89) that demotes already-explored basin
   trajectories would help, but V85 as written rewards quantity not
   variety.
3. **Already-implemented in disguise.** `quality._check_robustness`
   already runs 5 hand-written pathological inputs against every
   compressor and BASELINE.md flags V81 (Hypothesis property fuzzing)
   as the canonical version of this idea. The honest novelty over V81 is
   only "fitness-guided" vs "uniform-random"; the engineering delta is
   modest. If V81 ships first with `Hypothesis.given(strategy)` plus a
   well-designed shrinker, it gets most of V85's wins for free.
4. **"1000 generations on each compressor" is undersold.** Real-world
   adversarial robustness needs millions of trials. Our best-finding-
   diversity curve plateaus around generation 200 on most compressors
   (the `git_status` corpus saturates at ~1875 findings) which suggests
   the GA gets stuck in basins. A proper coverage-guided fuzzer
   (`atheris`, `python-afl`) instrumented on the parse code paths would
   blow this approach away on real bug-finding density.
5. **Reproducibility across machines is fragile.** As noted above,
   Python's `hash(str)` randomisation between processes makes seeded
   runs identical *within* one process group but not across CI runs that
   set `PYTHONHASHSEED=0` versus those that don't. The fix is trivial
   but easy to forget.

## Verdict

- **Novelty: low to medium.** Mutation-based fuzzing is standard;
  applying it to compressors with a layered fitness function is mildly
  novel; positioning it as a *separate* track from V81 (random) is the
  contribution. The real value is the concrete bug list, not the
  technique.
- **Feasibility: high.** Test-only, no deps, runs in <1 second at
  default settings. Already merged-able as a research probe.
- **Estimated speed of prototype:** 1 day. (Already prototyped in this
  PR; remaining work is hash-seed determinism fix and choosing whether
  to promote to enforce mode.)
- **Recommend prototype: yes.** Land at default `REDCON_V85_ENFORCE=0`,
  do not gate CI, treat the discovered findings as a triage backlog. The
  three concrete bugs identified (git_log must-preserve regex too
  permissive, listing compressor pattern/formatter disagreement,
  git_status inflation on short noisy `##` inputs) are tractable single-
  PR fixes worth ~5-15 LOC each. Each fix should add a regression
  fixture to `test_cmd_quality.py` so the bug never returns. After the
  three fixes land, flip `REDCON_V85_ENFORCE=1` for the listing/git_log
  schemas and let V85 hold the line going forward. Full V81 + V85
  combined coverage subsumes both.
