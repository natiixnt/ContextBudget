# V06: Context-Tree Weighting (CTW) line predictor for Markov-structured compressors

## Hypothesis
Some Redcon command outputs (pytest failures, lint rule lists, test summary lines, log lines)
are produced by deterministic formatters with strong local Markov structure: each character or
token is almost fully determined by a small fixed-length prefix. Context-Tree Weighting (CTW,
Willems-Shtarkov-Tjalkens 1995) gives a Bayesian-optimal mixture over all variable-order
Markov models up to depth D and converges to the Shannon entropy rate of any stationary
ergodic source. Two productisation paths exist:

1. **Online entropy coder**: replace the text emit step with a CTW-driven arithmetic coder.
   Predicted to halve bits-per-symbol on lint and test-summary streams.
2. **Offline pattern miner**: run CTW once over a corpus of fixtures, harvest the
   high-probability deterministic suffix-extension paths as regex templates, hand-code those
   into the compressors. Predicted to confirm or extend the existing template set.

The product question (cl100k tokens, not bits) is whether either path actually shrinks the
text the agent sees. Path (1) does not: bit-level arithmetic codes are not text. Path (2) is
just a methodology for designing the regex compressors that are *already shipped*.

## Theoretical basis

CTW maintains, for each context node `s` of depth `<= D`, a Krichevsky-Trofimov estimator
`P_e(s)` and a weighted estimate `P_w(s) = 0.5 * P_e(s) + 0.5 * P_w(0s) * P_w(1s)`.
The root `P_w(eps)` is provably within `O(|S| log N / N)` bits/symbol of the best
order-`<=D` Markov coder. Expected redundancy:

```
R_CTW(N) <= |S|/2 * log_2(N/|S|) + 2 |S| + 2     (Willems et al. 1995, Thm 2)
```

with `|S|` the number of distinct contexts that ever appear and `N` the stream length.
For deterministic formatters where each context has effective alphabet of size 1
(only one character can follow `assert ` - a digit), the conditional entropy collapses
to nearly zero and CTW achieves it.

I measured the order-`d` conditional character entropy on the live `_massive_pytest`
fixture from `tests/test_cmd_quality.py:80` and the `_RUFF_FIXTURE`-style synthetic
ruff stream. Treating the joined stream as one sequence:

| stream  | H_0 (bit/char) | H_2  | H_4  | H_8  | reduction at d=2 |
|---------|----------------|------|------|------|------------------|
| pytest  | 4.56           | 0.60 | 0.37 | 0.24 | -86.8%           |
| ruff    | 4.88           | 0.39 | 0.26 | 0.17 | -92.1%           |

So a depth-2 character predictor already captures ~87-92% of the entropy on these
streams. CTW would push the residual a few more points toward zero - real headroom
exists *in bits*. The headroom in tokens is the open question.

Back-of-envelope on the ruff stream (61 lines, ~3700 chars). Order-0 cost
~ 3700 * 4.88 / 8 = 2257 bytes raw. Order-2 cost ~ 3700 * 0.39 / 8 = 180 bytes.
A perfectly-coded payload would be ~180 bytes (~45 cl100k tokens). The current
COMPACT format on the ruff fixture emits ~16 lines (~120 tokens). The compact
format is therefore already at ~3.7x the entropy floor and at ~0.36 bits/char,
i.e. essentially at the order-2 bound *because the structural representation
discards exactly the redundant fields*. CTW would only beat compact if compact
were emitting per-line text - which (a) it does in VERBOSE, (b) the ULTRA tier
already collapses to a single-line summary for lint.

## Concrete proposal for Redcon

I recommend the **offline analysis path only**. Concrete deliverable:

1. New file `redcon/research/ctw_miner.py` (or under `tools/`, NOT `redcon/cmd/`).
   Out-of-tree. Not imported by the runtime. Standalone.
2. CLI: `python -m redcon.research.ctw_miner --fixtures tests/fixtures --depth 6`.
3. For each command schema, walk over recorded fixtures (we already have
   `tests/test_cmd_quality.py` + `tests/test_cmd_test_runners.py` golden bytes).
4. Train CTW (depth 6, alphabet = bytes). At each line boundary, list the top-K most
   probable suffix paths with `P_w >= 0.95`. Convert them into regex skeletons.
5. Diff against the regex set already declared in each compressor
   (`_FAILURES_HEADER`, `_FAIL_NAME_BLOCK`, `_LOCATION_LINE`, `_FOOTER_PART`,
   `_MYPY_LINE`, `_RUFF_LINE`, ...). Report any pattern CTW found that is not
   yet in code. That report is the only artifact.

Sketch:

```python
class CtwNode:
    __slots__ = ("counts", "children", "log_pe", "log_pw")
    def __init__(self, alphabet_size):
        self.counts = [0] * alphabet_size
        self.children = {}
        self.log_pe = 0.0
        self.log_pw = 0.0

def update(root, context, symbol, depth):
    node = root
    for c in context[-depth:]:
        node = node.children.setdefault(c, CtwNode(ALPHA))
    # Krichevsky-Trofimov: log P(symbol|history) = log((counts[s]+0.5) / (sum+|S|/2))
    ...

def mine_templates(stream, depth=6, threshold=0.95):
    # Walk depth+1 sliding windows; whenever P_w(next | ctx) > threshold over a full
    # line worth of symbols, emit the literal as a candidate template prefix.
    ...
```

5-15 line spirit kept: the runtime impact is **zero new lines in `redcon/cmd/`**.
The output is a markdown report the maintainer reads, then optionally adds a regex
to a compressor by hand. No determinism risk, no latency risk, no new dep on
the hot path.

## Estimated impact

- **Token reduction (online CTW path)**: 0 pp. Arithmetic codes do not survive cl100k.
  Re-encoding bits into ASCII expands by a factor of 8/log2(95)~1.22 on a 95-symbol
  printable alphabet, then cl100k re-tokenises that as essentially incompressible
  high-entropy bytes (~1 token per 4 bytes). Net: probably *worse* than the source.
- **Token reduction (offline miner path)**: bounded above by the gap between
  compact-tier output and the existing regex coverage. Empirically:
  - pytest fixture: 13 hand templates already cover 276/277 = 99.6% of lines.
  - ruff fixture: 2 hand templates cover 61/61 = 100% of lines.
  - mypy fixture: `_MYPY_LINE` matches every issue line; only the footer
    `Found 49 errors in 4 files` is outside the regex (and is captured by the
    compact format anyway via aggregate counts).
  Realistic upside: **0-2 pp** at compact tier on lint, **0-1 pp** on pytest,
  driven entirely by edge cases in the warnings section or session header that
  the current parser drops verbatim. Not a breakthrough.
- **Latency**: 0 (offline tool); not on hot path.
- **Affects**: documentation only. No production scorer/compressor/cache change.

## Implementation cost

- Offline miner: ~250 lines Python (CTW node, KT estimator, sliding-window update,
  threshold harvester, regex pretty-printer, fixture iteration). Standalone.
- Optional: write up findings in `research/findings/` if any new pattern is discovered.
- New deps: none. Pure Python, integer counts.
- Determinism / robustness / must-preserve risks: **none** (no production change).
- Risk to the "no embeddings, no model calls" positioning: **none** (CTW is a
  classical Bayesian mixture, no learned parameters - actually a reinforcement
  of the deterministic-stack story).

## Disqualifiers / why this might be wrong

1. **The bits-to-tokens translation kills it.** Bit-level entropy reduction does not
   translate to text-token reduction. The compressors already exploit the same
   structure CTW discovers, but in the *correct* representation: structured fields
   with named keys, dropped redundant text. Going from order-2 bits to order-6 bits
   does nothing for cl100k.
2. **Hand templates already cover 99-100% of lines** in the live fixtures
   (measured above). Whatever CTW would discover is already in the regex tables
   in `pytest_compressor.py` and `lint_compressor.py`. The miner would mostly
   re-validate existing code. Mark this honestly: this is a low-novelty redo of
   what `must_preserve_patterns` and the parsers already encode.
3. **Stationarity assumption fails.** CTW's optimality assumes a stationary ergodic
   source. Real pytest output mixes session header, dot-progress, FAILURES blocks,
   short summary, and footer - five regimes with different alphabets and structures.
   A single CTW model averages over all five and underperforms a small dispatch
   tree (which is exactly what the current parsers are: a state machine over
   `_FAILURES_HEADER` / `_SHORT_SUMMARY_HEADER` / `_FOOTER_LINE`). CTW with
   regime-conditioning is just a state machine with extra steps.
4. **Not a hot frontier in BASELINE.** BASELINE.md lists open frontiers
   (cross-call dedup, snapshot delta, custom BPE). Entropy-floor characterisation
   appears as V01 ("rate-distortion") and is the same family. CTW is one of many
   ways to estimate that floor; it does not unlock a new product axis.
5. **Subsumed by V99.** A custom BPE trained on Redcon's own output corpus
   directly attacks the bits-to-tokens problem CTW cannot solve. CTW finds the
   entropy; BPE captures it inside the tokenizer the agent actually uses. If
   anyone is going to spend research time, V99 has a real product surface and
   CTW does not.

## Verdict

- Novelty: **low** - CTW is well-known classical work; what would be implemented
  here is essentially a regex-template auditor and the existing parsers already
  encode the high-probability paths.
- Feasibility: **high** as an offline tool; not applicable as an online coder.
- Estimated speed of prototype: **2-3 days** for the miner + writeup; impact
  is the report it produces, which is likely "no new templates discovered".
- Recommend prototype: **conditional-on** an explicit goal of validating the
  existing compressor template coverage with an information-theoretic argument
  (useful for paper-quality write-ups, governance, or onboarding new
  compressors). For shipping token reduction: **no**. The bits-vs-tokens
  tension is fatal for the online direction, and the offline direction
  empirically returns ~0 pp on top of the current regex set.
