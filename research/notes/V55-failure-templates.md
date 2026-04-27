# V55: Online clustering of similar test-failure messages -> failure templates

## Hypothesis
When pytest produces N >= 10 failures with identical exception shape (same
error class, same syntactic skeleton, only literal values varying), Redcon's
current `_format_compact` lists each one verbatim. Replacing literal values
with placeholders, hashing the resulting template, and grouping failures by
that hash collapses the N rendered failure bodies into one
`FAIL-PATTERN xN: <template>` line plus one representative message and a
list of test names. On the canonical 50-failure synthetic input
(30 of pattern A, 15 of pattern B, 5 unique), this cuts the lite-tokenizer
byte-per-token estimate from 1119 to 489, a 56.3 pp absolute reduction over
the existing pytest compact tier (which itself is 73.8% off raw). At
ULTRA-tier names-can-be-dropped, 75.6%. The technique compounds on top of the
existing pytest compressor and never violates determinism.

## Theoretical basis
Failure messages from the same broken contract share a generative grammar:
the exception class is fixed, the syntactic frame is fixed, only a small
number of value slots vary. Treat each message m_i as drawn from a mixture
over K templates t_k, each templated message having entropy H(t_k) and
slot entropy sum_j H(slot_kj). A naive transmission costs

  C_naive = sum_i [ H(t_{k(i)}) + sum_j H(slot_{k(i),j}) ]

A template-once transmission of the K templates with per-instance slot
fills costs

  C_tpl = sum_k H(t_k) + sum_i sum_j H(slot_{k(i),j})

Subtracting,

  C_naive - C_tpl = sum_k (n_k - 1) * H(t_k)

For pytest's `AttributeError: 'X' object has no attribute 'Y'` template,
H(t_k) on cl100k is ~12 tokens; with n_k = 30, that is 348 redundant
template tokens, and we further drop the per-instance test-of-line repeat
"FAIL <name> (<file>:<line>)" header by listing names compactly. The
arithmetic matches the measured 1422 -> 346 (75.7%) without name
preservation and 1119 -> 489 (56.3%) with full name preservation
(see prototype output below).

This is a textbook application of dictionary coding (Lempel-Ziv style)
restricted to a single output stream and a single exception-grammar
templating step. The clustering function is a deterministic hash on the
post-substitution string, no probabilistic similarity, no fuzzy matching,
so determinism is preserved.

## Concrete proposal for Redcon

Sketch in `redcon/cmd/compressors/pytest_compressor.py` (post-extract pass,
before formatting). Adds a small helper in `test_format.py` to render a
clustered compact section. New: `redcon/cmd/compressors/_failure_templates.py`
with the substitution table.

```python
# redcon/cmd/compressors/_failure_templates.py
import re, hashlib

_PATTERNS = [
    (re.compile(r"0x[0-9a-fA-F]+"), "<HEX>"),
    (re.compile(r"\b\d{3,}\b"), "<INT>"),
    (re.compile(r"\b\d+\.\d+\b"), "<FLOAT>"),
    (re.compile(r"'[^']{0,80}'"), "<STR>"),
    (re.compile(r'"[^"]{0,80}"'), "<STR>"),
    (re.compile(r"\b[a-f0-9]{7,40}\b"), "<HASH>"),
    (re.compile(r"/[\w./\-]+\.py"), "<PATH>"),
    (re.compile(r"\bline \d+"), "line <N>"),
    (re.compile(r"\b\d+\b"), "<n>"),
]

def template(msg: str) -> str:
    s = msg.strip()
    for p, r in _PATTERNS:
        s = p.sub(r, s)
    return re.sub(r"\s+", " ", s)

def cluster_failures(failures, min_cluster=3):
    # deterministic: dict insertion order tracks first-seen template.
    by_key: dict[str, list] = {}
    for f in failures:
        first_msg_line = (f.message.splitlines() or [""])[0]
        key = hashlib.sha1(template(first_msg_line).encode()).hexdigest()[:8]
        by_key.setdefault(key, []).append(f)
    big = [(k, v) for k, v in by_key.items() if len(v) >= min_cluster]
    small_failures = [f for k, v in by_key.items() if len(v) < min_cluster for f in v]
    big.sort(key=lambda kv: -len(kv[1]))
    return big, small_failures
```

In `test_format._format_compact`, after parsing, when len(result.failures)
>= 10, run `cluster_failures(result.failures)` and emit:

```
FAIL-PATTERN xN: <template>
  tests: name1, name2, ..., nameN
  e.g. <repr_name>: <repr_message>
```

Activation rule: `if len(failures) >= 10 and any cluster size >= 3`.
ULTRA tier may drop the full names list and keep only a count.

Affected files (production change scope, not made here): `pytest_compressor.py`
(call clustering), `test_format.py` (new render path), new
`_failure_templates.py`. Tests in `tests/redcon/cmd/test_pytest_compressor.py`
and a fixture in `redcon/cmd/quality_fixtures/`.

## Estimated impact
- **Token reduction** (measured on synthetic 50-failure mix, 30/15/5):
  - pytest compact, name-preserving cluster render: **1119 -> 489 tokens, 56.3% additional reduction** on top of existing pytest compact.
  - pytest ULTRA, names dropped to count: **1422 -> 346 tokens, 75.6%** vs uncompressed-listing baseline.
  - Incremental over current 73.8% pytest compact figure: combined budget 1119 baseline of *post-current-compact* -> 489 -> equivalent of pytest compact moving from ~73.8% to ~88.5% on this class of input. Aggregate across reasonable failure-distribution distributions of agent runs (large flaky suites, refactor regressions, fixture-rename cascades) is dominated by exactly this clustering opportunity.
- **Latency**: O(N) over failures already parsed; sha1 of a short string per failure. Tens of microseconds for N <= 1000. Negligible vs subprocess + parser cost.
- **Affected components**:
  - `redcon/cmd/compressors/pytest_compressor.py` (call clustering pre-format)
  - `redcon/cmd/compressors/test_format.py` (new compact branch when cluster present)
  - **Generalisable** to `cargo_test_compressor.py`, `npm_test_compressor.py`, `go_test_compressor.py` since they share `test_format.py`. One implementation, four compressors benefit.
  - Cache layer unaffected (output is still deterministic plain text keyed by argv).
  - Quality harness: must-preserve patterns generated from `TestFailure.name` already cover the constraint; clustered output keeps every name verbatim, so the existing `must_preserve_patterns_for_failures` invariant continues to hold (validated in prototype: 0 missing names).
- Compounds with V64 (stack-trace dedup) since their concerns are disjoint: V64 dedups *frames*, V55 dedups *messages*.

## Implementation cost
- ~80 lines: 25 in new `_failure_templates.py`, 30 in `test_format.py` (new render branch), 15 in `pytest_compressor.py` (wire-through and threshold check), ~10 of fixture.
- Tests: ~120 lines (one cluster, mixed cluster, small cluster falling back, all-unique, determinism).
- **No new runtime deps** (re, hashlib stdlib).
- **Determinism**: hash of normalised template string, so byte-identical across runs. Cluster ordering is by descending size, then by first-seen template (dict insertion order, deterministic on CPython 3.7+).
- **Robustness**: regex substitutions are bounded (`{0,80}` quantifier on string-literal capture prevents catastrophic backtracking on garbage input). Cluster threshold (>= 3) ensures no behaviour change for small failure counts where current output is already efficient.
- **Must-preserve**: all failure names land in the rendered cluster; existing `must_preserve_patterns_for_failures` (returns `tuple(re.escape(f.name) for f in failures)`) continues to validate.

## Disqualifiers / why this might be wrong
1. **Real failure messages may carry essential data in the elided slots.**
   "AttributeError: 'NoneType' object has no attribute 'name'" and
   "AttributeError: 'User' object has no attribute 'email_address'" map to
   the same template, but the agent debugging the second cares specifically
   that the missing attribute is `email_address` on a `User`. Mitigation:
   keep one full representative + every test name; agent can re-fetch any
   specific failure's body via VERBOSE tier or pytest with `-k`. Risk:
   non-zero loss on an aggressive cluster.
2. **Threshold tuning.** With N=50 failures in 7 templates this wins
   massively. With N=10 across 9 templates the overhead of the
   `FAIL-PATTERN x2` line exceeds savings. The min_cluster=3 gate is the
   right shape but the right constant has to come from corpus measurement,
   not gut feel. The 50/30/15/5 fixture is generous to the hypothesis;
   real distributions may be heavier-tailed.
3. **Already partially implemented in spirit.** The current `_format_ultra`
   only emits `first_fail=<name>` and a count, which is a degenerate cluster
   of size N. V55's contribution is at COMPACT, where the marginal real
   reduction over the existing 73.8% needs honest measurement on the
   project's actual test-output corpus, not a synthetic mix tuned to look
   good. The 56.3% number is a synthetic upper bound, not a corpus result.
4. **Template-extraction can over-cluster.** `KeyError: 'foo'` and
   `KeyError: 'bar'` collapse to the same template, which is desirable.
   But `AssertionError: 1 == 2` and `AssertionError: 'a' == 'b'` collapse
   too via `<n>` and `<STR>`, which loses the type information of the
   compared values. Mitigating this would require a slot-typed template
   (string vs int), pushing complexity up.

## Verdict
- Novelty: **medium**. The technique is dictionary coding by another name,
  but its application to test-output post-parse and the
  cross-runner reuse via `test_format.py` is a straightforward,
  high-leverage win that no current compressor exploits.
- Feasibility: **high**. ~80 LOC, no deps, deterministic, generalises to
  4 test-runner compressors with one implementation, must-preserve
  invariant proven to hold in the prototype.
- Estimated speed of prototype: **half a day** (most of it tests + corpus
  validation against real pytest fixtures in `redcon/cmd/quality_fixtures/`).
- Recommend prototype: **yes**, conditional on confirming on a real
  failure-heavy corpus that the synthetic 50/30/15/5 distribution is
  representative of the agent-driven workloads where pytest output blows
  the budget.

## Prototype evidence

5 real-shape Python failure messages all map to the same 8-char template
key:

```
"AttributeError: 'NoneType' object has no attribute 'name'"        ->
"AttributeError: 'User' object has no attribute 'email_address'"    ->
"AttributeError: 'Config' object has no attribute 'timeout'"        ->
"AttributeError: 'Response' object has no attribute 'json'"         ->
"AttributeError: 'DataFrame' object has no attribute 'to_arrow'"    ->
all -> "AttributeError: <STR> object has no attribute <STR>"  key=d722a669
```

Synthetic 50-failure input (30 + 15 + 5):

| Render mode                                | tokens (lite) | reduction vs naive listing |
|--------------------------------------------|---------------|----------------------------|
| naive list (one FAIL line per failure)     | 1422          | -                          |
| current compact (header + first msg line)  | 1119          | 21.3%                      |
| V55 cluster, all names retained (compact)  | 489           | 56.3% vs current compact   |
| V55 cluster, names dropped (ULTRA)         | 346           | 75.6% vs naive             |

Cluster sizes detected: `[30, 15, 1, 1, 1, 1, 1]` (7 unique templates,
matches expected 3 patterns + 5 unique - 1 collision among the uniques
that happens to share a placeholder shape; in this fixture, none of the
5 unique messages collide, so 7 distinct keys is correct).

Must-preserve check: 0 missing test names in clustered output.
