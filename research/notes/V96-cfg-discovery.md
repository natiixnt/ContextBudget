# V96: CFG-discovery - detect context-free grammars in command output, switch to structural representation

## Hypothesis

When `detect_compressor` in `redcon/cmd/pipeline.py` returns no match and
the output is currently passed through (or only mildly trimmed by the
generic fallback), an automatic grammar-induction pass could detect that
the output is hierarchical (JSON/YAML/protobuf-text/table-with-header)
or just heavily repetitive, mine a context-free grammar via Sequitur or
Re-Pair in O(n) time, and emit a tree- or rule-shaped representation
that compresses better than passthrough.

The strong form of this claim is: every "structured but unknown" tool
output is implicitly a CFG, and any CFG is compressible to its grammar
size, which is asymptotically smaller than the unfolded text.

The empirical reality, as the brief warned: grammar-induction
algorithms (Sequitur, Re-Pair, LZ78, Lempel-Ziv-Welch) operate on
*symbol* repetition, not on *semantic* structure. They find that
"`+0000] "GET`" appears 16 times and replace it with `R1`. They cannot
abstract over IPs, timestamps, or path-shaped tokens unless those
shapes happen to be byte-for-byte repeated. The resulting grammar
beats raw passthrough on highly-repetitive structured inputs, ties or
loses on prose, and is *crushed* by a 30-line domain-aware compressor
when one exists. The actionable proposal is therefore narrow: a
"grammar fallback" tier engaged *only* when (i) `detect_compressor`
fails, (ii) raw output exceeds a threshold, and (iii) a cheap
repetition probe predicts >=20% reduction. Most often the first
condition fails because we already have a compressor for the format
the user is most likely to invoke.

## Theoretical basis

### 1. Sequitur in one paragraph

Sequitur (Nevill-Manning & Witten, 1997) maintains two invariants on a
linked-list representation of the input:

  - **Digram uniqueness**: no two adjacent symbols appear more than once.
  - **Rule utility**: every grammar rule is referenced at least twice.

Each new symbol triggers digram-uniqueness checks; violations create or
reuse rules; rule-utility violations inline rules with refcount<2.
The whole pass is linear-time amortised, producing a context-free
grammar G with sum of rule body lengths |G| satisfying:

    |G| <= H_k(x) * n / log n + O(n / log n)

for any fixed Markov order k, where H_k is the empirical k-th-order
entropy. So Sequitur is a *universal* source coder for stationary
ergodic inputs (Charikar et al. 2005, "Smallest grammar problem"). In
practice |G| sits within a constant factor (typically 2-5x) of the
optimal grammar.

### 2. The two losses on a BPE channel

The same trap V03 documented applies here. Suppose Re-Pair on
whitespace-tokens reduces a 14,701-token raw input to a grammar of
6,655 cl100k tokens (54.7% reduction, measured below). That gain is in
the same *family* of gains as gzip: it exploits exact-string repetition.
But the BPE tokenizer the agent is downstream of has *already
exploited* most short-range repetition - cl100k merges " GET ",
"HTTP/1.1", common path prefixes, etc. So the marginal gain over the
tokenizer's own inherent compression is what we measured, not the
~70-90% reduction Sequitur achieves over a naive 1-byte-per-symbol
encoder.

Second loss: rule labels are themselves tokens. `R47` is 2 cl100k
tokens. If a rule references 3 other rules, the rule body alone costs
~6-8 tokens, so the rule has to be referenced 4+ times to be
self-financing. This is why Re-Pair on the prose case produces a
*negative* reduction (see numbers below).

### 3. The smallest-grammar bound for log-style input

For an n-symbol input with k distinct symbols and worst-case repetition
structure:

    |G_optimal| = Theta(n / log n) * H_0(x)            (information-theoretic floor)
    |G_repair| <= O(log n) * |G_optimal|                (Charikar bound)

For our nginx fixture: n=3611 ws-tokens, k~600 distinct, H_0~6.0
bits/symbol. Floor in symbols ~= 3611 * 6.0 / 16.6 (cl100k entropy
ceiling) = 1305 cl100k tokens. We measured 6655 cl100k tokens for the
Re-Pair grammar - 5x over the floor. The domain-aware compressor
(NCSA-parser + group-by) emitted 475 cl100k tokens, *below* the
naive entropy floor, because it does the one thing CFG induction
cannot: replace 78 distinct IPs with a *count*, ignoring their
identity. That is information loss, not lossless compression, and that
is exactly where the win lives in this domain.

## Concrete proposal for Redcon

The proposal is a *fallback-only* tier with an aggressive eligibility
gate. It is read-only at runtime and never replaces an existing
compressor.

### A. New file: `redcon/cmd/compressors/grammar_fallback.py` (~180 lines)

```python
# grammar_fallback.py
import collections, re

_MIN_RAW_TOKENS = 800        # below this, header overhead dominates
_MIN_REPETITION = 0.25       # cheap probe: >=25% of digrams must repeat

def repetition_probe(tokens: list[str]) -> float:
    if len(tokens) < 50:
        return 0.0
    c = collections.Counter()
    for i in range(len(tokens) - 1):
        c[(tokens[i], tokens[i + 1])] += 1
    total = sum(c.values())
    repeated = sum(n for n in c.values() if n >= 2)
    return repeated / total if total else 0.0

def repair_compress(tokens: list[str], max_rules: int = 256) -> tuple[list, dict]:
    seq = list(tokens)
    rules: dict[str, tuple] = {}
    rid = 0
    while rid < max_rules:
        c = collections.Counter()
        for i in range(len(seq) - 1):
            c[(seq[i], seq[i + 1])] += 1
        if not c:
            break
        (a, b), n = c.most_common(1)[0]
        if n < 2:
            break
        name = f"R{rid}"
        rules[name] = (a, b)
        new_seq, i = [], 0
        while i < len(seq):
            if i + 1 < len(seq) and seq[i] == a and seq[i + 1] == b:
                new_seq.append(name); i += 2
            else:
                new_seq.append(seq[i]); i += 1
        seq = new_seq
        rid += 1
    return seq, rules

def try_grammar_fallback(raw: str, raw_tokens: int) -> str | None:
    if raw_tokens < _MIN_RAW_TOKENS:
        return None
    tokens = []
    for line in raw.splitlines():
        tokens.extend(line.split()); tokens.append("\\n")
    if repetition_probe(tokens) < _MIN_REPETITION:
        return None
    seq, rules = repair_compress(tokens)
    body = " ".join(seq)
    rule_lines = "\\n".join(f"{rid} = {a} {b}" for rid, (a, b) in rules.items())
    out = f"# grammar-form: {len(rules)} rules\\n{rule_lines}\\nS = {body}"
    # final guard: only return if it is meaningfully smaller in cl100k
    return out
```

### B. Pipeline hook in `redcon/cmd/pipeline.py::compress_command`

After `detect_compressor` returns the generic / passthrough compressor
and the standard pipeline runs, gate on:

```python
# pipeline.py - sketch only, do NOT edit production
report = compressor.compress(raw, ...)
if compressor.schema == "passthrough" and level is CompressionLevel.COMPACT:
    cand = try_grammar_fallback(raw, raw_tokens=report.compressed_tokens)
    if cand is not None and estimate_tokens(cand) < report.compressed_tokens * 0.85:
        report = report.replace(text=cand, schema="grammar-fallback", ...)
```

The 0.85 floor is a hard "must save 15% or revert"; this prevents the
prose-degenerate case (-15% blowup measured below) from ever shipping.
`schema="grammar-fallback"` means the quality harness can declare zero
must-preserve patterns and tier it as advisory.

### C. Quality declaration in `redcon/cmd/quality.py`

The grammar form is *not* human-readable, and may not preserve any
specific must-preserve regex. Declare:

  - `level=COMPACT` only (no ULTRA - if you are at ULTRA, raw is
    already over-budget and the agent should pay a parse cost of
    decoding `R47 = ...` lines).
  - `must_preserve_patterns = ()` - the schema is "structurally
    lossless" but not "regex-pattern lossless".
  - Determinism check: Re-Pair on identical input is deterministic
    *only if* tie-breaking in `Counter.most_common` is stable. CPython
    preserves insertion order; we must rely on that and document it.

## Estimated impact

### Measured numbers (200-line nginx access log, methodology in
`/tmp/v96/`)

| form                          | raw chars | cl100k tok | reduction |
|-------------------------------|----------:|-----------:|----------:|
| 1. raw passthrough            |     32747 |      14701 |       0.0%|
| 2. Re-Pair token-level grammar|     12617 |       6655 |      54.7%|
| 3. domain-aware NCSA parser   |       933 |        475 |      96.8%|

The grammar tier is ~30 percentage points worse than the existing
domain-aware approach, but ~55 pp better than passthrough. So the win
exists *only* relative to passthrough, only on highly repetitive
inputs, and only when no domain compressor is registered.

### Cross-input stress test (Redcon has no compressor for these)

| input class                           | raw cl100k | grammar | reduction |
|---------------------------------------|-----------:|--------:|----------:|
| YAML-ish K8s-style records (80 docs)  |       5692 |    1652 |     71.0% |
| Random prose (200 lines, 19-word voc) |       2734 |    3157 |    -15.5% |
| Table with fixed schema (120 rows)    |       3736 |    2808 |     24.8% |

So:

  - **YAML / hierarchical text without a compressor**: 71% reduction.
    This is the niche.
  - **Random / prose**: negative. The 0.85 floor in proposal B
    correctly rejects it.
  - **Tabular**: modest 25% win, but a 20-line splitter that
    re-emits as `header\\n<repeated-row-template>x<n>` would do better.

### Aggregated across the realistic distribution of unknown-tool calls

Projection (no fixture corpus exists for "tools we have not yet
written a compressor for"; this is qualitative):

  - 20-30% of unknown-tool outputs are hierarchical text (YAML / proto-
    text-format / config dumps) - grammar wins meaningfully.
  - 30-40% are tabular - grammar wins 15-25%, but a generic
    "row-template" detector would do better.
  - 30-50% are prose-like (READMEs piped in, error messages, free-text
    `--help` output) - grammar is neutral or negative.

Expected end-to-end reduction *across the unknown-tool fallback path*:
8-15% absolute on cl100k tokens, weighted by call frequency, gated by
the 0.85 floor on the no-win cases. This is the entire claim.

### Where it sits on the leaderboard

Below the breakthrough bar (>=5pp across multiple compressors). The
existing 11 compressors cover the high-frequency tools; this would be
an additive +1 on a 12th "everything else" pseudo-compressor.

### Latency

Re-Pair is O(n + r * n) where r is the number of rules induced. At
r<=256 and n=3611 we measured ~99 ms (pure Python). Implementing in C
or vectorising digram counting with `numpy.unique` would drop this
to ~10 ms but introduces a runtime dep we do not currently carry.
Cold-start: zero new imports if implementation is pure stdlib.
Warm parse: 99 ms is ~3-10x the typical compressor budget; acceptable
*only* because this path runs only when (i) raw exceeds 800 tokens,
(ii) no compressor matched, (iii) repetition probe passes. All three
conditions are uncommon.

## Implementation cost

  - `grammar_fallback.py`: ~180 lines (Re-Pair + repetition probe +
    serialiser + cl100k-aware reject gate). No new runtime deps.
  - Pipeline integration: ~20 lines in `pipeline.py`.
  - Quality declaration: ~10 lines in `quality.py` registering the
    `grammar-fallback` schema with empty `must_preserve_patterns` and a
    determinism-only check.
  - Tests: ~120 lines covering: probe rejects prose, probe accepts
    YAML, byte-identical determinism, 0.85 floor reject path, no-op
    when raw_tokens < 800, fallback never runs when a real compressor
    matched.
  - Risks to determinism: low. `Counter.most_common` ties resolve by
    insertion order in CPython >=3.7; document it in module docstring
    and add a determinism property test.
  - Risks to robustness: low. Worst case the fallback rejects (returns
    None) and passthrough proceeds. No unbounded memory; max_rules=256
    caps both rule count and runtime.
  - Risks to must-preserve: by construction zero, because the
    grammar-fallback schema declares no patterns. The trade-off is the
    agent reading `R47 R23 R12 ...` instead of plain text - the agent
    may have to mentally expand a few rules. This is the comprehension
    risk that disqualifies the strong form of the proposal (replacing
    every passthrough). Mitigation: only ship behind an explicit
    opt-in flag for users who saw their unknown-tool output get
    truncated; default is OFF.

## Disqualifiers / why this might be wrong

  1. **Already covered by the domain-aware compressors that exist**.
     Redcon ships 11 compressors including `http_log_compressor.py`
     (319 lines, real NCSA parser). For the canonical test input
     (nginx logs), grammar fallback would never engage because
     `detect_compressor` returns the http_log compressor first, which
     beats grammar by ~30 pp. The set of "structured outputs we do not
     have a compressor for" is, by definition, a moving and shrinking
     target - V61 (SQL EXPLAIN), V63 (bundle stats), V65 (JSON-log),
     V67 (k8s events) and others in the index are all preferred over a
     generic grammar fallback for their respective inputs.

  2. **The V03/V05 problem confirmed empirically here**. cl100k has
     already absorbed most short-range repetition. Re-Pair on prose
     blows up by 15%. The 0.85 floor catches this, but the broader
     point is that grammar-induction's theoretical wins assume a 1-
     byte-per-symbol channel, which the agent does not have.

  3. **Rule labels are tokens too**. `R47` is 2 cl100k tokens;
     `R123` is 3. A grammar with 200 rules and 4-character labels
     spends ~600 tokens just naming rules. On smaller inputs (raw <800
     tokens) this overhead alone exceeds any savings, which is why the
     gate exists.

  4. **Comprehension cost on the agent side**. Even when the grammar
     form is byte-smaller, an LLM reading
     `S -> R47 R23 R12 [01/Apr/2026...] R0 R1` must implicitly
     pattern-match each `Rn` against the rule table. Token-counting
     savings can be agent-quality losses. There is no measurement
     framework in Redcon today (no agent-trajectory test harness) to
     verify the comprehension cost stays bounded.

  5. **Sequitur output is not structurally meaningful**. The induced
     rules are *byte-pattern-frequent*, not *semantically coherent*.
     A rule like `R1 = +0000] "GET` straddles the timestamp boundary
     and the HTTP method - a human would never write that grouping.
     This makes the output worse-for-debugging than even raw
     passthrough; the agent reading it cannot point to "line 47" of
     the original log.

  6. **Indistinguishable in scope from V05 (ANS) and V99 (custom BPE)
     for the structured-text case**. All three approaches converge on
     "exploit the source's local entropy"; they all run into the same
     BPE-channel ceiling. V99 (training a custom BPE on Redcon's own
     output corpus) probably dominates V96 because it pushes the gain
     into the tokenizer where it survives the channel-coding loss; V96
     in plaintext does not.

  7. **Streaming incompatibility**. `compress_command` already runs a
     bounded streaming subprocess via `Popen`. Re-Pair is a global
     pass: it requires the full token stream before it can commit to
     a grammar. So this tier cannot interleave with the existing
     streaming reader without buffering the whole output, which
     interacts badly with the log-pointer tier (>=1 MiB spill). Fix:
     hard-cap the input at 1 MiB (which is already the spill threshold).

## Verdict

  - Novelty: **medium-high** as a concept (no Redcon code path attempts
    grammar induction today; no other vector in the index proposes
    Sequitur/Re-Pair specifically), but **low** in practice because
    the niche where it strictly helps - "structured tool output we
    have not yet written a compressor for" - is already being
    methodically eliminated by V61/V62/V63/V65/V67 and the existing
    11 shipped compressors.

  - Feasibility: **low-medium**. The Re-Pair implementation is
    ~180 lines and runs in ~100 ms on 30 KB input. The hard part is
    not the algorithm; it is the gating logic that prevents it from
    ever shipping a regression on prose, and the agent-comprehension
    risk that has no measurement harness in Redcon.

  - Estimated speed of prototype: 2-3 days (Re-Pair, gate, tests,
    integration with pipeline). Plus 1 week to gather a corpus of
    "unknown tool" outputs to verify the win projection beyond the
    three test inputs measured here.

  - Recommend prototype: **no, with one conditional**. Skip in the
    base proposal. The empirical numbers say: against a 30-line
    domain-aware compressor, grammar induction loses by 30 pp; against
    no compressor at all, it wins 25-71% but only on hierarchical
    inputs, and the prose case is actively harmful. The conditional
    is: if a future telemetry capability shows that >=20% of
    `redcon_run` calls hit the passthrough path AND the unknown tool
    list does not converge on a small set (where targeted compressors
    would dominate), revisit. Until then, every hour spent on V96 is
    better spent on V61/V63/V65/V67 (specific-format compressors with
    deterministic 70-95% wins) or on V99 (custom BPE, which is the
    universal-fallback strategy that actually composes with cl100k).

  - The honest one-line summary: **most outputs we know about, we
    already have a compressor for; for the rest, ship a domain
    compressor or a custom BPE, not a grammar miner.**
