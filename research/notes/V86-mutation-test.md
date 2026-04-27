# V86: Mutation testing on regex patterns themselves

## Hypothesis

Compressors are essentially regex-driven parsers. Each compressor module
declares ~3-13 module-level `re.compile(...)` patterns plus a
`must_preserve_patterns` tuple. If we systematically mutate one character at
a time inside each pattern (drop a char, swap a class, delete a quantifier,
broaden an anchor) and re-run the existing quality harness, every mutation
that *passes* the full harness identifies a regex sub-expression that the
fixture corpus does not exercise. Surviving mutations therefore quantify
test gaps. The prediction: the median compressor will have >=30% mutation
survival on its first run, and the survivors will cluster on
optional/alternation/anchor branches that no fixture reaches. Closing those
gaps lets later vectors (e.g. V72 SIMD regex, V78 prefix-gating audit, V12
canonicalisation) refactor regexes with confidence, which is a precondition
for further token reduction work.

## Theoretical basis

Mutation testing (DeMillo, Lipton, Sayward 1978; Offutt 2011) defines the
mutation score `MS = killed / (total - equivalent)`. A high score
upper-bounds how much a passing test suite can hide a real bug. For regex,
each pattern P is a finite automaton accepting language L(P). A textual
mutation P -> P' yields L(P') with symmetric difference Delta = L(P) XOR
L(P'). The mutation is *killed* iff some fixture x in the corpus C satisfies
x in Delta AND x triggers a difference visible in the compressed output
(reduction floor, must-preserve, determinism, or robustness).

Back-of-envelope. Take a compressor with `n` regexes of average length `L`
characters. Single-position mutation operators of size `k` (e.g. drop, swap
to `.`, swap class `\d` <-> `\w`, drop `?`/`*`/`+`, swap `^`/`$` to absent)
yield ~k*L mutants per regex, n*k*L per compressor. With k=4, L=40, n=8 we
get ~1280 mutants per compressor, ~14000 across 11 compressors. The harness
runs at ~1 ms per fixture per level; with ~3 fixtures, 3 levels, robustness
suite of 5 inputs, that is ~50 ms per mutant => ~12 minutes wall-clock for
the full sweep on a single core. Parallelisable trivially.

Information-theoretically: a regex `r"^=+\s*FAILURES\s*=+$"` has K(r) ~=
the description length of the pattern. The fixture corpus C imposes a
distinguishability metric d(P, P') = 1 if some x in C lies in Delta else 0.
The mutation score is the empirical estimator of P_x~C(d=1), i.e. the
probability that a random nearby pattern P' in the edit-distance ball is
distinguishable from P under C. Low mutation score => C does not span the
neighborhood of P, so any refactor inside that neighborhood is silently
safe-OR-broken with equal probability under C alone.

Three useful corollaries:

1. Anchor mutations (`^` -> nothing, `$` -> nothing) are killed iff the
   corpus contains a *line that contains but is not* the pattern. For
   line-prefix-gated compressors this is often missing (the prefix gate
   already filters non-anchored matches in production).
2. Optional-group mutations (`(?:...)?` -> `(?:...)`) are killed iff the
   corpus contains both presence and absence of the group. Many compressors
   only have presence fixtures.
3. Class mutations (`\d+` -> `\w+`) are killed iff the corpus contains a
   non-numeric token in the slot. For pure-number slots (line numbers,
   counts) the mutation is trivially equivalent on the corpus; surfacing
   it lets us prove tightness rather than guessing.

## Concrete proposal for Redcon

New file: `tests/test_compressor_mutation.py`. Opt-in, gated on env
`REDCON_MUTATION=1` (so it does not run on every PR; the full sweep is too
slow for pre-merge but cheap enough for a nightly).

Reuse the existing fixtures from `tests/test_cmd_quality.py::CASES` and
the existing `run_quality_check` harness. Discover patterns by AST-walking
each `redcon/cmd/compressors/*.py` and grabbing every `re.compile(LITERAL)`
plus the `must_preserve_patterns` tuple from each compressor class. Patch
the module attribute (not the source) in-process via `monkeypatch.setattr`.

```python
# tests/test_compressor_mutation.py
MUTATIONS = [
    ("drop_char",    lambda s, i: s[:i] + s[i+1:]),
    ("swap_dot",     lambda s, i: s[:i] + "." + s[i+1:]),
    ("drop_anchor",  lambda s, i: s[:i] + s[i+1:] if s[i] in "^$" else None),
    ("widen_class",  lambda s, i: _widen(s, i)),   # \d->\w, \s->\S, etc.
    ("drop_quant",   lambda s, i: s[:i] + s[i+1:] if s[i] in "?*+" else None),
]

def enumerate_mutants(pattern: str):
    for name, op in MUTATIONS:
        for i in range(len(pattern)):
            try:
                cand = op(pattern, i)
            except Exception:
                cand = None
            if not cand or cand == pattern:
                continue
            try:
                re.compile(cand)
            except re.error:
                continue   # not a valid regex; skip
            yield name, i, cand

def run_one_mutant(monkeypatch, module, attr, mutant, case):
    monkeypatch.setattr(module, attr, re.compile(mutant))
    check = run_quality_check(case.compressor,
                              raw_stdout=case.raw, argv=case.argv)
    return check.passed   # True => SURVIVED (gap)

@pytest.mark.skipif(not os.getenv("REDCON_MUTATION"),
                    reason="opt-in mutation sweep")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_mutation_score(monkeypatch, case, mutation_log):
    survivors = []
    for module, attr, pattern in iter_patterns_in(case.compressor):
        for name, i, mut in enumerate_mutants(pattern):
            with monkeypatch.context() as m:
                if run_one_mutant(m, module, attr, mut, case):
                    survivors.append((attr, i, name, mut))
    mutation_log.write(case.name, survivors)
    # do not fail; this is a coverage report, not a pass/fail gate.
```

The output is a JSON report `tests/_mutation_report.json` with per-pattern
survivor counts. That report is the actionable artefact: each surviving
mutation is a one-line fixture to add to the corpus.

Integration with the existing harness is intentionally minimal:

- No production source change (per task constraints).
- No new MCP tool. No new CLI flag in `redcon`.
- The harness in `redcon/cmd/quality.py` already surfaces every signal we
  need: must-preserve, threshold, determinism, robustness.
- We piggy-back on `monkeypatch.setattr` of compiled-regex module globals
  rather than rewriting source.

A small helper `redcon/cmd/_mutation.py` (test-time only, but in src so it
can be imported from tests) is acceptable if we want the AST walker
reused; otherwise keep it inside the test file.

## Estimated impact

- Token reduction: zero direct. This is QA tooling.
- Latency: zero. Test-only.
- Indirect: enables safe regex tightening. Anchor-tightening and
  class-narrowing on poorly-tested patterns has historically yielded ~1-3
  pp reduction on individual compressors when combined with prefix gating
  (BASELINE notes that prefix gating already saves on some hot paths).
  Realistic uplift across the suite if we kill 80% of survivors and
  refactor accordingly: ~1-2 absolute pp per compressor on average. This
  is below the >=5 pp breakthrough bar, but it derisks every other vector
  that touches regexes (V12, V72, V78, V89, V96).
- Affects: no production code today. After follow-up refactors, touches
  the per-compressor pattern tuples and possibly `prefix_gate` literals.

## Implementation cost

- Test file: ~150-200 LOC (mutation operators, AST walker, parametrize
  glue, JSON writer).
- Optional `redcon/cmd/_mutation.py` helper: ~80 LOC.
- New deps: none. Uses stdlib `ast`, `re`, `pytest`, `monkeypatch`.
- Cold-start: unaffected (test-only, no import in production path).
- Determinism: enumeration is deterministic (positional sweep, fixed
  operator list, sorted patterns by `(module, attr)`). No randomness.
- Risk to must-preserve: nil; we only mutate in the test process, never
  on disk.
- Robustness risk: a maliciously broad mutation could ReDoS a fixture and
  wedge the test. Mitigation: wrap each `compress()` call in a 5-s timeout
  (e.g. `signal.alarm` on POSIX or `pytest-timeout`).

## Disqualifiers / why this might be wrong

1. **Equivalent mutants dominate.** Many regex mutations produce the same
   recognised language on the corpus by luck, not by undertest. Without
   automated equivalence detection (which is undecidable in general; for
   regex it is decidable but requires DFA minimisation per pair) the
   "survivor count" overestimates the gap. We can mitigate with a small
   set of synthetic adversarial strings auto-generated from each pattern
   (V85's territory) but that adds complexity.
2. **Survivors may be intentional slack.** A pattern like
   `r"^=+\s*FAILURES\s*=+$"` is deliberately tolerant of `=` count. The
   mutation `=+` -> `=` is "killed" for inputs with more than one `=`,
   but the mutation `=+` -> `=*` *survives* because the corpus never
   contains a zero-`=` line. Adding such a fixture is silly: zero-`=`
   never happens in real pytest output. The mutation report needs human
   triage; not every survivor is a bug.
3. **Already covered indirectly by V81 (Hypothesis property fuzz) and
   V89 (coverage-guided fuzzing of regex parsers).** Those vectors
   generate inputs that drive the existing patterns; this vector goes
   the other direction (mutate patterns, hold inputs fixed). The two are
   complementary but overlap in actionable output: if V81 already
   generates a corpus broad enough that mutation score is >95%, V86 adds
   little.
4. **Cost grows with regex count.** `pkg_install_compressor.py` already
   has 13 patterns; if we add a dozen more compressors (V61-V70 propose
   exactly that) the sweep balloons. We need per-pattern budgets and
   shard support for CI.
5. **Pattern-level mutation misses higher-level bugs.** A compressor can
   have a perfect regex set and still drop information at the formatting
   step (`_format`/`_compact_format` etc.). Mutation testing of regexes
   does not exercise those code paths at all.

## Verdict

- Novelty: medium. Mutation testing is decades old; applying it
  specifically to compressor-regex sets in a deterministic
  reduction-floor harness is, as far as I can tell, not done in the
  codebase. The BASELINE lists "differential testing / property-based
  fuzzing of compressors" as not-yet-done; mutation-on-pattern is a
  cousin of that.
- Feasibility: high. Pure stdlib, opt-in, no production changes.
- Estimated speed of prototype: ~1 day for the test file plus AST walker
  plus first JSON report. Another 1-2 days to triage the first survivor
  list and add fixtures.
- Recommend prototype: yes. The cost is low, it produces an actionable
  artefact (the survivor report), and it derisks every later refactor
  that touches regex patterns. The pitch is *not* "this saves tokens";
  the pitch is "this is the smallest possible piece of QA hygiene that
  has to exist before we can safely refactor patterns for V72/V78/V96."
