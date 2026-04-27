# V79: Compile-time-generated parsers (PEG to static dispatch table)

## Hypothesis

Replace the hand-written, prefix-gated regex chains in
`redcon/cmd/compressors/git_diff.py` and
`redcon/cmd/compressors/pytest_compressor.py` with a formal grammar
compiled by Lark (LALR or Earley) into a generated parser. The
predictions are: (a) faster warm parse on large diffs because a single
DFA-driven lexer beats a per-line `str.startswith` ladder followed by
regex matches, (b) cleaner error recovery on truncated / corrupted
input, (c) less code to maintain. If true, this would also be a
template for adding new compressors in fewer lines.

The empirical result, derived from a complete throwaway prototype
(`/tmp/v79_peg_bench.py`) measuring grammar-compiled `git diff`
parsing against the production hand-written parser on the same
`_huge_diff` fixture used by `tests/test_cmd_quality.py`, is that
**every prediction fails**. The Lark version is 8-9x slower warm,
crashes on the two adversarial inputs the existing harness explicitly
asserts robustness against (truncated mid-stream, garbage prefix), and
adds a hard import-time cost of ~40 ms on cold-start. The 11-line
grammar is shorter than the ~120-line hand parser, but that "win" is
the only one and is paid for many times over by everything else.

## Theoretical basis

The hand-written parser is a single linear pass with first-byte
dispatch. For a diff of `L` lines, where lines split into classes
`{add, del, ctx, hunk_header, file_header, meta, other}` with
empirical frequencies on the test corpus of roughly
`p_add = p_del = 0.30`, `p_ctx = 0.36`, `p_meta = 0.04`, the cost
per line in the production code is:

```
T_hand(line) = 1 byte read + 1 dispatch  if first byte in {' ', '+', '-'}
             = 1 startswith + at most 1 regex match  otherwise
```

Roughly 96% of lines hit the fast first-byte branch and never touch
the regex engine. Total cost on `L` lines is
`T_hand(L) ~= L * c_fast + 0.04 * L * c_regex` with measured
`c_fast ~= 0.5 microseconds`, `c_regex ~= 5 microseconds` on CPython 3.14.

Lark's LALR(1) parser, by contrast, runs every line through:

1. The contextual lexer DFA (built from all terminal regexes; on a
   token alphabet of size `|T|`, transition cost is `O(1)` but with
   a Python-level interpreter loop, not a C state machine).
2. The shift/reduce table with a Python list-of-states stack.
3. Tree construction: every matched line allocates a `Token`
   object and is reduced into a `Tree` node.

Rough cost of the Lark pipeline on the same fixture at `L = 1488`
diff lines:
```
T_lark(L) ~= L * (c_dfa_step + c_reduce + c_alloc)
          ~= L * 4.2 microseconds   (measured 6.24 ms / 1488 lines)
```
Hand-written: `L * 0.54 microseconds` (measured 0.80 ms / 1488 lines).
Ratio ~7.8x; on a 10x larger fixture (`120 files * 20 hunks`) the
ratio holds at 9.1x. The ratio is structural: any Python-hosted
generic parser inherits the per-token allocation overhead, so this
is not a tuning problem.

For Earley the cost is far worse because of the `O(n^3)` worst case
and the chart-construction overhead: measured 207 ms on the same
1488-line fixture, 259x slower than hand-written.

The LOC-savings argument also fails when audited carefully:
- PEG grammar: 11 non-blank lines.
- Hand parser core (`parse_diff` + `_split_into_file_blocks` +
  `_parse_file_block` + `_finalize_hunk` + module-level regex
  constants): ~120 lines.
- BUT: in the Lark version, the grammar replaces *only* the parsing
  step. We still need glue code to turn the Lark `Tree` into the
  `DiffResult` / `DiffFile` / `DiffHunk` dataclasses the rest of the
  pipeline expects, plus a transformer that increments
  insertion/deletion counts. That glue is ~50 lines minimum,
  bringing total to ~70 - a 40% reduction, but with the
  performance, robustness, and dependency penalties below.

## Concrete proposal for Redcon

The proposed change (and what the throwaway prototype implements):
add `lark>=1.3` to dependencies. Replace the body of
`redcon/cmd/compressors/git_diff.py::parse_diff` with:

```python
from lark import Lark, Transformer

_DIFF_PARSER = Lark(_GRAMMAR, parser="lalr")  # module-level, compiled once

class _DiffBuilder(Transformer):
    def file_block(self, items):
        # Walk hunk children, count + lines / - lines, return DiffFile.
        ...
    def start(self, items):
        return DiffResult(files=tuple(items), ...)

def parse_diff(text: str) -> DiffResult:
    return _DiffBuilder().transform(_DIFF_PARSER.parse(text))
```

Same shape for `pytest_compressor.py`. Add a `_meta.redcon` field
indicating which parser variant produced the output, behind a feature
flag, so a/b comparisons survive the cache key.

This is the proposal. It does not work. The remainder of this note
documents why.

## Estimated impact

Measured on `_huge_diff(num_files=12, hunks_per_file=20)`,
32 KB / 1488 lines, the same fixture used by `tests/test_cmd_quality.py`:

| metric                          | hand-written | lark LALR | lark Earley |
|---------------------------------|--------------|-----------|-------------|
| warm parse, median (ms)         | 0.80         | 6.24      | 207         |
| warm parse, p95 (ms)            | 0.84         | 6.36      | 233         |
| ratio vs hand                   | 1.00x        | 7.8x      | 259x        |
| same on 10x fixture (323 KB)    | 8.46 ms      | 77.2 ms   | -           |
| LALR ratio at 10x size          | -            | 9.1x      | -           |
| cold `import lark` overhead     | 0 ms         | +40 ms    | +40 ms      |
| grammar/parser compile (warm)   | 0 ms         | +9.5 ms   | +1.9 ms     |
| recovers from truncated input   | yes (9/12)   | NO        | NO          |
| recovers from garbage prefix    | yes (12/12)  | NO        | NO          |

- Token reduction: 0 absolute pp. Output bytes are unchanged because
  this is a parser swap, not an output change. So the breakthrough
  metric defined in BASELINE.md ("compact-tier reduction by >=5
  absolute points across multiple compressors") is impossible by
  construction.
- Latency, warm: -7.8x to -9.1x regression on the hot path.
- Latency, cold-start: +40 ms regression. BASELINE.md constraint 5
  ("lazy-imports already shaved ~62% off cold-start; new techniques
  cannot regress this") explicitly forbids this.
- Affects: `redcon/cmd/compressors/git_diff.py`,
  `redcon/cmd/compressors/pytest_compressor.py`. Cache layer is
  unchanged since output text is byte-identical when input is valid.
- The robustness guarantees in `redcon/cmd/quality.py` (binary
  garbage / truncated mid-stream / 5000 newlines / random word spam)
  are violated: LALR raises `UnexpectedCharacters` on any input that
  does not match the grammar, and the production parser explicitly
  silently absorbs garbage and emits whatever partial structure
  survives. Lark's `on_error` callback can catch this but only by
  abandoning everything after the error point.

## Implementation cost

- Lines of code: ~70 (11-line grammar + ~50-line transformer +
  glue), down from ~120 - net -50 LOC.
- New runtime deps: `lark>=1.3.1`, ~600 KB installed, pure Python.
  Does not break "no embeddings / no required network", but it is a
  new top-level dep on the hot path of the most-used compressor,
  which raises supply-chain surface and adds 40 ms cold-start.
- Risks to determinism: low. Lark is deterministic given a fixed
  grammar.
- Risks to robustness: severe (see table above). Two of the four
  adversarial cases the quality harness asserts at every commit
  fail outright.
- Risks to must-preserve: medium. Currently must-preserve patterns
  hold because the parser produces structured records and the
  formatter writes paths verbatim. With Lark, paths are tokenised
  through a regex terminal; long paths with shell-special characters
  could trip the lexer where the hand parser is character-by-character
  tolerant.

## Disqualifiers / why this might be wrong

1. **Performance is not a tuning issue, it is a structural Python-host
   issue.** Every line in a Lark parse allocates at minimum one
   `Token` object and one tree node; the hand parser allocates almost
   nothing per line on the fast path. PyPy might close the gap but
   Redcon ships on CPython. Even an optimal C-extension PEG parser
   would still pay token-allocation overhead at the Python boundary
   per file_block.
2. **Cold-start regression directly violates BASELINE.md constraint
   5.** Lazy import of `lark` inside `parse_diff` would defer the
   penalty to the first parse call instead of `import redcon`, but
   the penalty still hits *every fresh invocation of the CLI* on the
   first diff - that is the latency budget that was explicitly shaved
   by 62% in the recent commits referenced in BASELINE.md. We would
   give it back.
3. **Adversarial-input regression directly violates the
   `redcon/cmd/quality.py` robustness invariants.** The hand parser
   is intentionally tolerant: it skips lines it does not understand,
   resyncs at the next `diff --git` header, and emits whatever was
   parseable. A grammar-driven parser is by definition the opposite:
   it rejects any input the grammar does not accept. Adding partial
   recovery requires writing a custom error handler that effectively
   re-implements the prefix-gated dispatch by hand, recovering none
   of the LOC savings.
4. **The "cleaner error recovery" claim in the hypothesis is
   backwards.** PEG / LALR parsers are NOTORIOUSLY bad at error
   recovery on streaming or truncated input, exactly the regime
   Redcon operates in (subprocess output that may be cut by
   `early_kill` or `log-pointer-tier` spillover at 1 MiB). Hand
   parsers with a "skip until I see a known prefix" loop are the
   right tool here - which is what the production code already does.
5. **Already-implemented in disguise.** The prefix-gating in the
   production code (commit `50d2a95`-era and earlier) is exactly the
   discriminating function that a lexer would build automatically,
   except specialised by hand to the actual line-class distribution
   in the corpus. The hand parser is effectively a hand-compiled
   single-state parser specialised on the empirical alphabet. A
   generic parser-generator throws away that specialisation.
6. **Net LOC reduction is small (-50 lines) and changes the bus
   factor in the wrong direction.** The current parser is readable
   line-by-line by anyone who knows regex. A Lark grammar plus a
   transformer requires familiarity with PEG / EBNF / Lark's tree
   API, and the failure modes of LALR contextual lexers (the
   `KeyError: frozenset({'__ANON_7'})` and "zero-width terminal" bugs
   that the prototype hit on first try) are far less obvious than a
   regex that misses a corner case. Worse maintenance, not better.

## Verdict

- Novelty: **low**. PEG / parser-generator alternatives to hand
  parsers are textbook material. The interesting result here is the
  empirical confirmation that the hand parser already sits at a local
  optimum for this workload (Python-hosted, line-streamed, robustness
  required).
- Feasibility: **low**. The performance regression alone (8x warm,
  +40 ms cold) is disqualifying under BASELINE.md constraint 5; the
  robustness regression is disqualifying under
  `redcon/cmd/quality.py`. A version that fixed both would have to
  re-implement the prefix-gated tolerant skip loop on top of Lark,
  which is what the hand parser already is.
- Estimated speed of prototype: **hours** (the throwaway
  `/tmp/v79_peg_bench.py` was complete in under an hour). The fact
  that it was easy to disprove is the contribution: this is a
  cheap-to-rule-out vector and the boundary is now documented.
- Recommend prototype: **no**. The hand-written prefix-gated parser
  in production is, on this workload, both faster and more robust
  than Lark LALR. The only path to a positive result for V79 would
  be a non-Python compile-time generator (something that emits C or
  Rust from a grammar), and that opens a build-tool surface
  disproportionate to the gain.
