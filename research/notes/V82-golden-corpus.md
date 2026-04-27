# V82: Differential testing - golden corpus byte-for-byte across implementations

## Hypothesis

Redcon's 11 command compressors are now under active churn (Tier 2 additions in
commit a44993b, format tweaks in commit 50d2a95, must-preserve refactors in
commit 257343). Every refactor PR carries silent risk: a "harmless" rewrite of
`git_diff.py` flips the order of two lines in compact output, the existing
quality harness still passes (must-preserve regex still matches, reduction
floor still met, robustness still holds), but downstream caches are
invalidated, agent prompts subtly drift, and snapshot-style tooling (V16, V47)
that assumes encoding stability silently breaks. The claim: maintain a
versioned, hand-curated golden corpus of (raw bytes, schema, level) ->
expected compressed bytes, run it byte-for-byte after every change to the
compressor tree, and refuse to merge PRs whose compressor output drifts unless
the diff is explicitly accepted into the corpus via a marked update commit.
This is engineering hygiene, not an algorithmic advance: it does not add a
single token of compression. What it buys is a sharp regression surface so
that future algorithmic vectors (V47 snapshot-delta, V16 test-delta, V41
session aliases) can rely on encoding stability as a correctness invariant
rather than a hope. Predict: zero token-reduction impact, ~1 day to land,
catches ~2-5 silent-drift regressions per quarter at current PR cadence,
unlocks downstream vectors that need byte stability.

## Theoretical basis

This is software engineering hygiene, not information theory. The relevant
formal frame is approval testing / characterization testing (Feathers,
*Working Effectively with Legacy Code*, ch. 13: "I Don't Have Much Time and
I Have to Change It") combined with the Beck/Hunt notion of a "test fence"
around a refactor. Three points justify the framing:

### 1. Refactor safety as an invariant-preservation property

Let C_old and C_new be two implementations of the same compressor (pre and
post refactor). The refactor is *behaviour-preserving* on a fixed input
distribution D iff

    forall x in supp(D): C_old(x) == C_new(x)

For Redcon's compressors the relevant equivalence is *byte equality* on the
formatted output text at a pinned `CompressionLevel`, not just regex
preservation. A regex-level invariant (must-preserve patterns) is strictly
weaker: it admits silent permutations, format-noise edits, header rewordings,
all of which break byte stability without tripping the existing quality
harness. The corpus is the empirical realisation of D - a finite,
representative sample whose byte-equality on every (x, level) tuple gates
merges. Coverage isn't proven, it's curated.

### 2. Coding-cost interpretation of drift

Every uncontrolled byte change in compressor output causes:

  - **Cache invalidation** (KL-divergent old/new outputs share no cache hit).
    Cost: replays the subprocess. For a `pytest` re-run on a 200-test repo,
    ~3-30 s wasted.
  - **Agent context drift**. If turn t-1 received compressor v1's output and
    turn t receives compressor v2's output for "the same" raw input, the
    model's internal continuity is broken. For format-stable agents
    (Claude Code's tool-use loop) this is a soft regression: behaviour
    works, latency degrades (model re-reads the "new" version).
  - **Downstream snapshot-delta breakage** (V47). V47's whole premise is
    that a delta against the prior absolute encoding is shorter. If the
    encoding format itself drifts between calls, V47 must always fall back
    to absolute, costing the entire 41% session reduction projected in V47.

So encoding stability is a *load-bearing* contract for the upper-layer
vectors. Pricing the cost of a drift event:

    cost_drift_event >= cache_miss_subprocess_cost + delta_layer_fallback_cost

For a session with 60 tool calls and a single drift in the middle, the cache
miss alone is ~5-30 s of re-execution wall-clock (depending on which command
churned). Across 4 PRs/week and ~2 silent drifts per quarter, the integrated
cost is dwarfed by the engineering cost of debugging "why is my V47 delta
suddenly empty" without a witness.

### 3. Sample-size argument: how big must the corpus be?

For a compressor with a parser that contains B branches (in the
control-flow-graph sense; for Redcon's regex/dispatch chains, B is between
~12 for git_status and ~80 for git_diff), the minimum corpus to exercise
each branch at least once at COMPACT level is just B inputs. To bias against
adjacent-branch drift (one regex flip leaves all branches still
"reachable" but different combinations fire), aim for branch-pair coverage:

    |corpus| >= O(B^2 / log B)   for pair coverage on dense graphs
                                   (cf. random-graph cover, Erdős-Rényi)

For B=80 (git_diff), this is ~80 * (80/log 80) ~= 1500 inputs - prohibitive.
The pragmatic compromise is *targeted* coverage: B "happy-path" branches
plus the 30-50 pathological cases the quality harness already exercises
(`b"\x00\x01\x02..."`, truncated streams, 5000 newlines, etc.). For 11
compressors that is ~11 * (40 + 15) ~= 600 (raw, schema, level) goldens.
Stored as gzipped fixtures, this is ~2-5 MB on disk. Manageable.

Empirical corpus-size guideline from the approval-test literature
(Llewellyn Falco's `ApprovalTests` library, used in JUnit-class projects of
similar scope): 30-60 goldens per "module under approval" yields >90%
catch-rate on accidental refactors in observed industrial codebases. Redcon
sits comfortably in that range.

### 4. Why byte equality, not similarity

A "fuzzy" diff (allow whitespace flips, allow reordered lines) is tempting
but defeats the purpose: V47 needs *exact* prior encodings as deltas
baselines, V41 session aliases need *exact* path-token reuse for the
tokenizer, and the cache key digest is over raw byte content. Byte equality
is the only invariant that all upstream and downstream layers can rely on
without coordination. The cost of strictness is one ergonomic burden:
intentional format updates require an explicit "update goldens" step. This
is desirable, not undesirable.

## Concrete proposal for Redcon

### Files touched / created

  - **`tests/golden/cmd/<schema>/<case>/raw_stdout.bin`** (NEW, ~600 files):
    raw bytes from the subprocess. Stored as `.bin` so git treats them as
    binary and avoids LF/CRLF surgery on Windows hosts. Filenames are
    deterministic (`case_001_short.bin`, `case_002_renamed_files.bin`, ...).
  - **`tests/golden/cmd/<schema>/<case>/raw_stderr.bin`** (NEW): may be empty.
  - **`tests/golden/cmd/<schema>/<case>/argv.json`** (NEW): canonical argv as
    a JSON string array, e.g. `["git", "diff", "HEAD"]`.
  - **`tests/golden/cmd/<schema>/<case>/expected_<level>.txt`** (NEW): the
    expected compressed text at each of `verbose`, `compact`, `ultra`. UTF-8.
  - **`tests/golden/cmd/<schema>/<case>/manifest.json`** (NEW): metadata -
    `{"schema": "git_diff", "case": "renamed_files", "origin": "synthesized
    from real repo abc123 commit hash 4f5e...", "captured_at": "2026-04-26",
    "tokens_at_compact": 142, "notes": "rename detection edge case"}`. The
    `tokens_at_compact` is included so a token-count regression is also
    visible.
  - **`tests/test_cmd_golden.py`** (NEW, ~120 LOC): the harness. Walks the
    corpus, instantiates the compressor by schema, runs `compress(...)`
    at each level, asserts byte-equality with `expected_<level>.txt`. On
    mismatch, dumps a unified diff and the absolute paths of both files so
    a developer can `cp` to update the golden.
  - **`tests/golden/cmd/_update.py`** (NEW, ~40 LOC): opt-in helper.
    Re-runs every compressor against every `raw_stdout.bin` and overwrites
    `expected_<level>.txt`. Guarded by `REDCON_UPDATE_GOLDENS=1` env var so
    it never fires in CI. Prints a summary diff so the developer can review
    what changed before committing. Intentionally not a pytest fixture -
    updating goldens should be a deliberate, reviewable commit.
  - **`tests/golden/cmd/README.md`** (NEW, ~30 lines): policy doc. "If your
    PR changes any `expected_*.txt`, the commit message must include the
    line `golden-update: <reason>` and the PR description must call out the
    drift." Reviewers gate on this.
  - **`.github/workflows/ci.yml`** (existing, +5 lines): add a step
    `pytest tests/test_cmd_golden.py -x` so the corpus runs on every PR.

### Sketch of the harness

```python
# tests/test_cmd_golden.py
import json, pathlib, pytest
from redcon.cmd.compressors.base import CompressorContext
from redcon.cmd.budget import BudgetHint
from redcon.cmd.types import CompressionLevel
from redcon.cmd.registry import detect_compressor

GOLDEN_ROOT = pathlib.Path(__file__).parent / "golden" / "cmd"
LEVELS = (CompressionLevel.VERBOSE, CompressionLevel.COMPACT, CompressionLevel.ULTRA)

def _cases():
    for schema_dir in sorted(GOLDEN_ROOT.iterdir()):
        if not schema_dir.is_dir() or schema_dir.name.startswith("_"):
            continue
        for case_dir in sorted(schema_dir.iterdir()):
            if (case_dir / "manifest.json").exists():
                yield schema_dir.name, case_dir

@pytest.mark.parametrize("schema,case_dir", list(_cases()),
                          ids=lambda v: v.name if hasattr(v, "name") else v)
def test_golden_byte_equal(schema, case_dir):
    raw_stdout = (case_dir / "raw_stdout.bin").read_bytes()
    raw_stderr = (case_dir / "raw_stderr.bin").read_bytes() \
                 if (case_dir / "raw_stderr.bin").exists() else b""
    argv = tuple(json.loads((case_dir / "argv.json").read_text()))
    compressor = detect_compressor(argv)
    assert compressor is not None and compressor.schema == schema, \
        f"detect_compressor({argv}) = {compressor!r}, expected schema={schema}"
    for level in LEVELS:
        hint = _force_level_hint(level)   # mirrors quality.py helper
        ctx = CompressorContext(argv=argv, cwd=str(case_dir),
                                returncode=0, hint=hint)
        actual = compressor.compress(raw_stdout, raw_stderr, ctx).text
        expected_path = case_dir / f"expected_{level.value}.txt"
        if not expected_path.exists():
            pytest.fail(f"missing golden: {expected_path}")
        expected = expected_path.read_text(encoding="utf-8")
        if actual != expected:
            _dump_diff(actual, expected, expected_path)
            pytest.fail(f"golden drift: {schema}/{case_dir.name}/{level.value}")
```

### Sketch of the updater

```python
# tests/golden/cmd/_update.py
"""Run with REDCON_UPDATE_GOLDENS=1 to regenerate every expected_*.txt.
Reviewers MUST eyeball the resulting diff before committing.
"""
import os, sys, json, pathlib
if os.environ.get("REDCON_UPDATE_GOLDENS") != "1":
    sys.exit("refusing to update without REDCON_UPDATE_GOLDENS=1")
# walk the same _cases() generator, write expected_<level>.txt fresh,
# print a unified diff per file changed.
```

### Initial corpus seeding

  - **Phase A** (~0.5 day): For each of the 11 compressors, capture 4-8
    real-world raw outputs from the same agent-test scripts that the
    benchmark suite (`tests/test_cmd_benchmark.py`, `redcon/cmd/benchmark.py`)
    already uses. Strip private paths, normalise temp-dir prefixes,
    commit. Total: ~50-90 golden cases.
  - **Phase B** (~0.5 day): Add the 5 pathological inputs already exercised
    by `_check_robustness` in `quality.py`: empty bytes, binary garbage,
    truncated mid-stream, 5000 newlines, random-word spam. These ensure
    the *graceful-degradation* path is also frozen byte-for-byte (today
    nothing checks that empty input produces "no output\n" vs "(empty)\n"
    consistently across refactors).
  - **Phase C** (~0.5 day): Add the 30 hand-crafted edge cases per schema
    that BASELINE.md hints at (rename detection in git_diff, --json mode
    in grep, large-blob log-pointer-tier triggers, etc.). These come from
    issue-tracker bug history. Total: ~330 cases.

### Integration with M8 quality harness

The existing `tests/test_cmd_quality.py` already runs every compressor at
every level on synthetic inputs and checks must-preserve / reduction /
determinism. V82 adds the *byte-equality* axis, orthogonal to those three.
Concrete integration: the golden corpus inputs become the *primary* corpus
for the quality harness too. `test_cmd_quality.py` parametrizes over
`_cases()` from V82, then runs both the V82 byte-equality assertion and the
existing M8 assertions (must-preserve, floor, determinism, robustness)
against the same input. One corpus, four assertions per case. This avoids
divergence between "what the quality harness tests" and "what the byte-
equality fence tests".

```python
# proposed: tests/test_cmd_quality.py imports _cases from test_cmd_golden,
# replacing the synthetic _huge_diff() / _short_status() generators with
# real fixtures. Synthetic generators stay only for stress-volume cases
# (10000-hunk diff) where storing the raw bytes would bloat the repo.
```

This is V82's M8 hook: V82 supplies the corpus, M8 runs four invariants on
each entry, the union is what the CI gate checks.

## Estimated impact

### Token reduction
  - **0 absolute pp.** This vector ships zero compression improvement. Its
    job is hygiene.
  - Indirect: by pinning byte stability, V82 unblocks V47's ~41% session-
    aggregate reduction (which today silently degrades the moment any
    compressor format drifts). V82 is therefore a prerequisite for the
    upper-layer vectors but not a contributor on its own.

### Latency
  - Cold: zero (test-only artefacts).
  - Warm: zero (test-only artefacts).
  - CI wall clock: +2-5 s for the full byte-equality pass on ~330 goldens.
    Reading 600 small files + running 11 compressors x 3 levels = ~990
    compress() calls, each ~1-3 ms. Comfortable.

### Affects
  - All 11 existing compressors gain a byte-frozen contract.
  - `redcon/cmd/quality.py`: no API change; the existing harness gains a
    parametrized corpus source via shared fixture.
  - `redcon/cmd/pipeline.py`, `redcon/cmd/cache.py`: untouched.
  - V47 (snapshot delta), V16 (test delta), V41 (session aliases),
    V49 (symbol cards): all rely on byte stability of the prior turn's
    output. V82 makes that contract explicit and enforced.

## Implementation cost

  - **LOC**: ~120 (harness) + ~40 (updater) + ~30 (README/policy doc).
    No production source changes. ~330 fixture files (raw bytes + expected
    text + manifest), ~3-5 MB total on disk after gzip-friendly storage
    (text fixtures compress well in pack files even if not pre-gzipped).
  - **New runtime deps**: zero.
  - **Determinism risks**: zero. The harness is read-only; the updater is
    opt-in via env var. Determinism is *strengthened*: a refactor that
    introduces a non-deterministic byte sequence (timestamp, randomly
    ordered set iteration on Python <3.7-style dict) trips the corpus
    immediately rather than weeks later.
  - **Robustness risks**: one - if a fixture itself contains environment-
    specific paths (e.g. captured `git diff` output mentioning `/Users/
    naithai/...`), the golden bakes in that path and fails on other
    machines. Mitigation: capture against a temp-repo with stable paths
    (`/tmp/redcon_corpus_<n>/...`) and document the convention in
    `tests/golden/cmd/README.md`. The capture script (Phase A) does this
    rewrite at the moment of capture, not at test time.
  - **Must-preserve guarantee**: untouched. V82 is strictly stronger than
    must-preserve (byte-eq implies regex-eq).

## Disqualifiers / why this might be wrong

  1. **Maintenance tax outpaces catch rate.** Every legitimate format
     improvement now requires a `golden-update:` commit touching potentially
     hundreds of `expected_*.txt` files. If Redcon's compressor format
     evolves rapidly (it has, recently: commits 50d2a98, a44993b, 257343
     all touched format), the update churn dominates the catch rate. The
     ApprovalTests literature warns about this: corpora that are not
     curated rot into "rubber-stamped" updates that catch nothing. Counter:
     the CI gate plus the policy doc force every update to be reviewed,
     which is the value the gate is supposed to provide. Mitigation: ship
     a tight initial corpus (~330 cases) rather than a sprawling one
     (~3000), so updates are tractable.

  2. **Byte equality is too strict for valid format-noise refactors.**
     A refactor that reorders two `must_preserve` regex matches in the
     output (genuinely equivalent semantically, both still satisfy the
     regex contract) trips V82 spuriously. The author then either (a)
     spends an hour reverting, (b) accepts a 200-file golden update, or
     (c) starts to resent the gate. This is the standard objection to
     approval testing. Counter: the resentment is a feature, not a bug -
     it forces the question "is this reorder *actually* equivalent for
     downstream consumers?", and 1-in-5 times the answer is no (the
     reorder breaks V47's delta). The other 4-in-5 times, accepting the
     update is the right call. The friction is calibrated.

  3. **This is engineering hygiene, not research.** BASELINE.md's
     "breakthrough" criterion is >=5 pp compact-tier reduction or >=20%
     cold-start latency or a new compounding compression dimension. V82
     hits none of these. It is therefore Novelty: low *as a research
     vector*, even though as engineering it is high-leverage. Honest
     framing: V82 is the load-bearing test fence that V47/V16/V41
     algorithmic vectors need to ship safely. It belongs in a research
     index because someone has to identify it; it doesn't belong in a
     "novelty" pile. The vector should be acknowledged as low-novelty
     and high-feasibility, then prioritised on the engineering roadmap
     not the research one.

  4. **Existing quality harness already covers this.** This is the most
     important rebuttal because it's almost true. `test_cmd_quality.py`
     plus `_check_robustness` already verify (a) must-preserve regex,
     (b) reduction floor, (c) determinism (run twice, same output). The
     gap is precisely: same compressor twice in one process is checked,
     but same compressor across two *commits* is not. Determinism inside
     a process is a different invariant from byte-stability across code
     changes. V82 adds the second invariant. Without V82, the quality
     harness will pass for any refactor that is internally deterministic,
     even if it has gratuitously rephrased the format. Empirically, this
     has happened twice in the last six commits (50d2a93 changed
     "compact" tier output for argv display; 257343 added `_meta.redcon`
     fields that show up in serialised tool results). Both were
     intentional - both would have produced loud golden updates that
     the reviewer could rubber-stamp in seconds. So the gap is real but
     narrow.

  5. **Corpus capture is laborious and easily becomes stale.** Real-world
     `git diff` output captured in 2026-Q2 may not represent 2027-Q1
     usage if the typical Redcon-targeted repo profile changes (more
     LSP-style projects, more multi-language monorepos, more docker
     compose). Mitigation: re-capture quarterly via a documented script,
     check the new captures into a sibling directory, retire the old
     ones. This is similar to how `pytest-benchmark` handles baseline
     drift. Cost: 1-2 hours quarterly.

  6. **Cross-platform line endings.** A golden captured on macOS with LF
     line endings will differ from the same compressor's output on
     Windows where the subprocess spawned by Redcon may emit CRLF.
     Mitigation: byte-equality is enforced after a normalisation pass
     (`raw_stdout.replace(b"\r\n", b"\n")`) baked into the harness, with
     a comment explaining why. This is one place where byte equality is
     relaxed to "byte equality after canonical line-ending
     normalisation". The relaxation is documented and load-bearing.

  7. **Goldens are a worse signal than property tests.** V81's hypothesis-
     style property fuzzing finds bugs that the corpus would never include;
     V82's curated cases find bugs that the fuzzer would never trigger.
     They are complementary, not substitutes. Shipping V82 alone misses
     the property-based win; V81 alone misses the regression-fence win.
     The honest recommendation: ship both, treat V82 as the regression
     fence and V81 as the bug-discovery instrument. Pairing matches
     industry practice (e.g., Hypothesis + golden-snapshot in Python
     codebases like `attrs`, `cattrs`).

## Verdict

  - **Novelty: low.** This is approval testing applied to a known surface.
    BASELINE.md flags differential testing as "not done yet" so within
    Redcon it has positive value, but the technique itself is textbook.
    Honest scoring: low novelty, high engineering value.
  - **Feasibility: high.** ~190 LOC of test code, ~330 fixture files,
    no production source changes, no new runtime deps, no determinism
    risk. CI cost +2-5 s.
  - **Estimated speed of prototype: 1 day.** Phase A (capture from
    benchmark suite) is half a day; Phase B (pathological inputs from
    `quality.py`) is two hours; Phase C (curated edge cases) is half a
    day. The harness itself is two hours.
  - **Recommend prototype: yes - but as engineering hygiene, not research
    breakthrough.** Specifically:
      - **Tie to M8 quality harness.** The corpus seeded by V82 should
        be the *primary* input source for `test_cmd_quality.py` going
        forward, replacing the synthetic `_huge_diff()` / `_short_status()`
        generators for non-stress cases. One corpus, four invariants
        (byte-eq, must-preserve, reduction floor, determinism). This
        avoids the divergence trap where the byte-eq corpus and the
        quality harness corpus drift into different idioms.
      - **Land before V47.** V47's ~41% session reduction depends on
        prior-turn output being byte-stable. V82 makes that contract
        machine-checkable. Shipping V47 without V82 risks silent V47
        regressions (delta layer falls back to absolute) that nobody
        notices because the reduction is a steady leak, not a test
        failure.
      - **Pair with V81.** V81 (property-based fuzz) + V82 (curated
        corpus) is the canonical regression-prevention combination.
        Either alone leaves a measurable gap. Roadmap them together.
      - **Sample the corpus from real agent traces.** The `run_history`
        SQLite (`redcon/cache/run_history_sqlite.py`) has captured raw
        outputs over real Claude Code sessions on this repo. The
        capture script for Phase A should pull from there to seed
        realism, then anonymise paths.

  V82 is unglamorous. It does not move the compact-tier reduction by a
  single percentage point. What it does is freeze the contract that the
  next round of reduction work (V47, V16, V41) depends on. As an isolated
  research vector its novelty is low. As a precondition for the algorithmic
  vectors that *are* breakthrough-shaped, it is mandatory. Recommend
  shipping in the same iteration as V81 and prior to V47.
