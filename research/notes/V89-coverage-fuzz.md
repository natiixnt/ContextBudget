# V89: Coverage-guided fuzzing of regex parsers

## Hypothesis
The eleven shipped compressors (`pytest_compressor.py`, `git_diff.py`, `git_log.py`, `git_status.py`, `grep_compressor.py`, `lint_compressor.py`, `docker_compressor.py`, `npm_test_compressor.py`, `cargo_test_compressor.py`, `go_test_compressor.py`, `listing_compressor.py`, plus `http_log_compressor.py`, `kubectl_compressor.py`, `pkg_install_compressor.py`) carry 82 `re.compile` callsites. Each is a small recogniser at the line / block boundary of structured tool output. The current quality harness (`redcon/cmd/quality.py::_check_robustness`) feeds five hand-picked pathological blobs (`b""`, binary garbage, mid-stream truncation, 5000 newlines, "random words " * 5000) and only asserts "no crash". It does not exercise the *interior* state space of those regexes - the alternations, the `(?:...)` optional groups, the lazy `.*?` runs, the `\S(?:.*\S)?` ambiguous core. **Coverage-guided fuzzing (atheris / libfuzzer-for-Python) wraps each `parse_*` entry point and mutates inputs whose corpus footprint hits new branches**. Predictions:
1. We will find at least one input that exposes catastrophic backtracking (super-linear time scaling) in a compiled regex - because alternations mixed with optional groups and `.*?` are well-known ReDoS surface and Redcon has not audited for them.
2. We will find at least one parser path that `_check_robustness`'s five fixtures don't reach (e.g. a `FAILURES` block that opens but never closes before EOF, a footer line whose byte-count is parseable but float-cast fails, a mypy entry whose `[code]` bracket runs to end-of-input).
3. Token reduction unchanged. Quality (no-crash, bounded-time, must-preserve invariants under fuzzed inputs) improves.

A 30-second smoke run already confirms (1) on the live source: feeding `lint_compressor._MYPY_LINE` an input shaped `"a:1: error: " + "[" * N` produces match times `5 ms` at N=1000, `21 ms` at 2000, `83 ms` at 4000, `333 ms` at 8000 - **quadratic in N**. At the runner's 120s default `timeout_seconds`, an attacker (or an unlucky tool) producing a single ~16 KB line of bracket spam would burn ~5 seconds of pure regex backtracking before any output is emitted to the agent. This is below the timeout but well above the budget for a "fast local cache".

## Theoretical basis
AFL / libFuzzer's coverage-guided model treats a program as a function `f: bytes -> set[BranchID]` and uses the size of `union(f(corpus_i))` as the fitness signal. A genetic mutator (bit flips, splices, dictionary inserts) keeps inputs that *strictly increase* the union; the rest are discarded. Theorem (Bohme & Pham, "Coverage-based Greybox Fuzzing as Markov Chain", TSE 2017): under reasonable Markov assumptions about transitions between code branches, AFL-style mutation reaches a stationary distribution that visits each branch with frequency proportional to `1 / #ancestors_to_that_branch`. Rare branches (deep nested optionals, error paths) take exponential expected time but **finite**; uniform random fuzzing without coverage feedback takes time exponential in `|state machine|` even to reach the first error path.

Back-of-envelope for our case. Take `pytest_compressor._FAIL_NAME_BLOCK = re.compile(r"^_{3,}\s+(?P<name>\S(?:.*\S)?)\s+_{3,}$")`. Compiled NFA states (counted via `sre_parse`):
- `^` 1, `_{3,}` 1 (with min/max repeat), `\s+` 1, `\S` 1, `(?:.*\S)?` 2, `\s+` 1, `_{3,}` 1, `$` 1 = ~9 states with 2 alternation points (the `?` and the `\s+` at boundaries).
- Branches reachable in `_parse_failure_blocks`: `in_section in {True,False}` x `first in {'=', '_', other}` x `current_name in {None, set}` = 12 product states, of which only ~6 are reachable. Quality harness's 5 fixtures touch maybe 3 of those 6.

Coverage to find: one extra branch. Random uniform sampling at 256 bytes of input has Pr[hit] = (1/256)^k where k is the number of literal anchor bytes we need to align (`==FAILURES==` plus `___`). For pytest's failure header alone, k >= 14; uniform reach probability ~10^-34. AFL with a corpus seeded by one real pytest log reaches it in O(seconds) because each mutation preserves most anchors and only perturbs the tail.

The asymptotic claim: for a parser with `B` branches and `S` literal anchor bytes per branch, coverage-guided fuzzing reaches each branch in expected time `O(B * 2^S / |corpus|)` with corpus seeding, vs `O(2^(B*S))` without. Concretely, B ~ 80 across all compressors, S ~ 8, |corpus| seedable to 50: expected time per branch ~ minutes; full coverage in ~hours of CPU.

## Concrete proposal for Redcon

Files (proposed, no production source touched):
- `tests/fuzz/fuzz_pytest.py` - atheris harness around `redcon.cmd.compressors.pytest_compressor.parse_pytest`.
- `tests/fuzz/fuzz_lint.py`, `fuzz_grep.py`, `fuzz_git_diff.py`, `fuzz_docker.py`, `fuzz_http_log.py` - one harness per heavyweight regex parser.
- `tests/fuzz/corpus/<schema>/` - seed corpora extracted from `tests/fixtures/cmd/<schema>/`; the existing fixtures already cover happy paths.
- `tests/fuzz/runner.py` - timeboxed CI driver that invokes each harness for N seconds (default 60 in CI, 600 nightly), saves any new crash / timeout to `tests/fuzz/crashers/`, fails the run if a crash is fresh.
- (Optional) `redcon/cmd/quality.py::_check_robustness` extended to load up to K crashers from `tests/fuzz/crashers/` and replay them on every quality run, so once a fuzz finding lands in tree it becomes a permanent regression seed.

Sketch:
```python
# tests/fuzz/fuzz_pytest.py
import atheris, sys, time
with atheris.instrument_imports():
    from redcon.cmd.compressors.pytest_compressor import parse_pytest

def TestOneInput(data: bytes) -> None:
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return
    t0 = time.perf_counter_ns()
    parse_pytest(text)
    elapsed = time.perf_counter_ns() - t0
    # Treat super-linear time as a finding, not just crashes.
    if elapsed > 50_000_000 and len(data) < 16_384:  # 50 ms for <16 KB
        raise RuntimeError(f"slow parse: {elapsed/1e6:.1f} ms on {len(data)} B")

atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
```
The interesting twist is the super-linear-time guard. atheris's vanilla mode flags only crashes; we want to flag latency cliffs because Redcon's whole pitch is "fast". A 10 ms parse of a 1 KB log is fine; a 5 s parse of an 8 KB log is a regression even if it eventually returns the right answer.

For per-regex unit fuzzing (more focussed than whole-parser fuzzing), an even smaller harness:
```python
# tests/fuzz/fuzz_regex_lint.py - target a single compiled pattern
import atheris, sys, time
from redcon.cmd.compressors.lint_compressor import _MYPY_LINE, _RUFF_LINE

def TestOneInput(data: bytes) -> None:
    s = data.decode("utf-8", errors="replace")
    for pat in (_MYPY_LINE, _RUFF_LINE):
        t0 = time.perf_counter_ns()
        pat.match(s)
        if time.perf_counter_ns() - t0 > 20_000_000:
            raise RuntimeError(f"redos: {pat.pattern!r} {len(data)} B")

atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
```

## Estimated impact
- Token reduction: **0 absolute pp**. Vector picked the wrong axis.
- Latency: in the *typical* hit path, zero. For the *adversarial* path (a tool that emits long unbalanced bracket runs in a lint message, a docker buildkit log with 100 KB unterminated quote, a grep result with a path containing 5000 colons), eliminating the ReDoS regressions saves between 0.1 s and the full 120 s `timeout_seconds`. The bracket-bomb finding above already shows a 333 ms slowdown on an 8 KB line - on a 50 KB line that becomes ~15 s.
- Affects which existing layers: `quality.py` gains a "replay crashers" hook; the eleven compressor parse functions each get audited and (in prediction) one to three of them gain anchor-tightening or non-greedy bounds (`.*?` -> `[^\]]{0,256}?` or similar). The cache, scorers, and pack-side stay untouched.

## Implementation cost
- Lines of code: ~50 LOC per fuzz harness x ~8 harnesses = 400 LOC. ~80 LOC for `tests/fuzz/runner.py`. ~30 LOC for the `_check_robustness` crasher-replay extension. Total ~500 LOC, all under `tests/` and dev-only.
- New runtime deps: **atheris** is the obvious choice but it's a dev-only dep (gate behind `pip install redcon[fuzz]`). Caveat: atheris ships pre-built wheels for CPython 3.8-3.11 only (PyPI as of 2024). The project Python is **3.14.3** (this repo's interpreter); atheris will not import. Two fallbacks:
  1. **Hypothesis** with `hypothesis.strategies.binary()` + `@settings(deadline=50)` - no coverage instrumentation, but works on 3.14, finds ~30% of what atheris finds in similar wallclock, and is already a Python-ecosystem-native dep.
  2. **Custom mutation loop** keyed on `sys.settrace`-derived line coverage of the compressors. ~150 LOC, no external dep, ~5x slower than atheris but works on any Python.
- No network, no embeddings, no determinism risk to *production* code (the fuzzer is offline). The `crashers/` corpus committed back to repo serves as a *deterministic* regression seed for `_check_robustness`.

## Disqualifiers / why this might be wrong

1. **This is engineering, not a research vector** (the prompt itself admits this). The breakthrough bar in BASELINE.md is "+5 pp compact-tier reduction across multiple compressors, OR -20% cold-start, OR a new compounding compression dimension". V89 hits none of those. It plugs leaks, it doesn't move the bar. Marketing this as a research finding is a category error; it belongs as a CI task. **The honest framing: it's a quality-engineering task wearing a research hat because Redcon's quality harness happens to be sparse.**
2. **The existing `_check_robustness` already addresses the highest-leverage 80%.** Empty input, binary garbage, truncated mid-stream, all-newlines, all-words: those five blobs are the textbook adversarial class. Anything finer-grained that fuzzing finds is probably a 50-200 ms latency wart in a regex, not an actual crash or wrong-answer bug. The eight compressors that ship today all explicitly use line-by-line `match()` (not full-input `search()`), and they all bound work via `lines[:n]` slicing in the formatters. Catastrophic backtracking is bounded per *line*, not per *input*; the worst case is one slow line per output, and the runner's wall-clock timeout caps that anyway.
3. **atheris does not run on Python 3.14.** This repo's interpreter is 3.14.3 (verified). atheris's current wheels stop at 3.11 and the C-extension-wrapped libfuzzer integration is non-trivial to forward-port. A 3.14 fuzzer would have to be Hypothesis-based or hand-rolled, which **degrades the methodology to property-based testing - which is exactly V81's territory**. So V89 collapses into V81 absent a Python downgrade or a vendored atheris build.
4. **Coverage feedback on a regex is mostly redundant when the regex source is short.** AFL is valuable when the program has 10^4+ branches and you don't know the structure. A 60-character regex has ~10 nodes; you can synthesise an adversarial input in your head from `sre_parse.parse(pattern).dump()`. Hand-written ReDoS-checker tools (`re-redos`, `regexploit`, `safe-regex`) work statically on the regex AST and are sound for a useful subclass. They would find the bracket-bomb in `_MYPY_LINE` in milliseconds without ever running it.
5. **A finding does not equal a fix.** Even if fuzzing flags `_MYPY_LINE` as quadratic on `[` runs, the fix (anchor `[^\]]+` to `[^\]]{1,128}` or pre-validate line length) has its own correctness implications: now a 200-character mypy code is silently rejected. The bug-to-fix ratio in regex hardening is roughly 1:5 in code-review pain. Spending an engineer-week to find ten such bugs is rational only if those ten bugs are actually firing in production telemetry, which Redcon does not yet collect.
6. **Determinism interaction is real but solvable.** A fuzzer that mutates inputs is by definition non-deterministic *during the fuzz run*. The output (the `crashers/` corpus) is deterministic - committed bytes - so the *replay* path stays deterministic. No constraint violated.

## Verdict
- Novelty: **low**. Coverage-guided fuzzing of parsers is a 2014 idea (AFL's first wins were on libpng, libxml2, openssl). Applying it to small line-regex recognisers is mechanical. The nearest ambitious framing - "use coverage-derived branch entropy as a signal for compressor budget allocation" - is V10 (information-bottleneck) and is on a different shelf.
- Feasibility: **medium**. Atheris-on-3.14 is a real blocker. Hypothesis fallback works but renames the vector to V81. Hand-rolled coverage tracer is feasible but expensive (~3 days).
- Estimated speed of prototype: **3-5 days** of engineering for a Hypothesis-based equivalent + crasher corpus + `_check_robustness` replay hook. **0 days** for the standalone "find one ReDoS in `_MYPY_LINE`" demonstration - that's already done in this note (8 KB bracket bomb -> 333 ms; quadratic scaling confirmed).
- Recommend prototype: **conditional-on-X**. Do it if and only if (a) Redcon ships a public `redcon_run` MCP service that takes adversarial input from untrusted tools, OR (b) production telemetry shows >=1% of `redcon_run` calls hitting `timeout_seconds`. Otherwise: file the one observed bracket-bomb finding as a GitHub issue, harden `_MYPY_LINE` with `[^\]]{1,256}`, add the offending input to `_check_robustness`'s pathological list, and close. **Issue, not vector.**

### Concrete actionable byproduct of writing this note
Even though V89 itself is "not breakthrough", the audit *as a side effect* surfaced one real regression candidate worth filing as a GitHub issue immediately, independent of any fuzzing infrastructure:

```
title: ReDoS-y backtracking in lint_compressor._MYPY_LINE on unbalanced bracket runs

repro:
  python3 -c "
  import re, time
  pat = re.compile(r'^(?P<path>[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<severity>error|warning|note):\s*(?P<message>.*?)(?:\s*\[(?P<code>[^\]]+)\])?\s*$')
  for n in (1000, 2000, 4000, 8000):
      s = 'a:1: error: ' + '[' * n
      t0 = time.time(); pat.match(s); print(n, time.time()-t0)
  "
  # 1000 0.0054 / 2000 0.0210 / 4000 0.0836 / 8000 0.3333 - quadratic
fix: bound the trailing optional group, e.g.
  r"(?:\s*\[(?P<code>[^\]\n]{1,128})\])?\s*$"
or pre-reject lines longer than e.g. 4096 chars at parse time.
```
This single observation is more valuable than the proposed 500-LOC fuzz harness.
