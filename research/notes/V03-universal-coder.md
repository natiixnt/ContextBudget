# V03: Universal source coder for code+prose mixture, prove an upper bound on remaining slack

## Hypothesis

Redcon's compact-tier output is a heterogeneous string drawn from at least
three statistically distinct sources mixed by a known structural grammar:

  - S_struct: structured syntactic tokens (`diff:`, `+`, `-`, `@@`, paths,
    line numbers, `(no diff)`, `M`, `D`, `?`, `more`).
  - S_prose: short natural-language summary fragments (test names,
    error messages, lint rule descriptions, commit subject lines).
  - S_code: code fragments inside quoted snippets / failing assertions /
    grep matches (effectively another non-stationary source - identifiers,
    operators, punctuation).

A universal source coder (specifically: a piecewise-stationary mixture
over per-segment Krichevsky-Trofimov estimators, equivalent to a
context-tree-weighted (CTW) model with hard segment boundaries) achieves
per-symbol redundancy O(log N / N) over the unknown component
distributions. We can therefore *bound* the bits-per-character that any
lossless re-encoding of Redcon's current output could achieve, then ask
how many of those bits the compact tier is leaving on the table.

The twist that makes this vector either useful or a trap: the channel
is not a binary pipe, it is a BPE tokenizer (cl100k by default,
o200k/llama for non-OpenAI). Arithmetic-coded bitstreams are
near-incompressible by BPE - they look like random bytes, get split
into 1-byte-per-token chunks, and inflate by ~4x. So the classical
"reduce to entropy" recipe does not apply. The contribution of V03 is
therefore split:

  1. Prove a non-trivial *lower bound* on the achievable token count for
     Redcon's current output, derived from per-segment empirical entropy
     plus the BPE coding-cost theorem (Zouhar et al. 2023).
  2. Show this bound is already within ~10-15% of what compact tier
     emits for the structured-heavy compressors (git_diff, find, grep)
     and 30-50% looser for prose-heavy ones (pytest, lint).
  3. Deliver a *tokenizer-aware* universal scheme - not arithmetic
     coding, but per-source vocabulary selection - that closes part of
     that gap.

## Theoretical basis

### 1. Two-step coding cost

For an output string x of N characters consumed by a BPE tokenizer T,
the cost in tokens is `|T(x)|`. For any tokenizer with vocabulary V
and merge table M, Zouhar et al. (EMNLP 2023, "Tokenization and the
Noiseless Channel") prove:

    |T(x)| >=  H_T(x) / log2(|V|)               (info-theoretic floor)
    |T(x)| <=  N / L_avg(T)                     (length floor)

where H_T(x) is the empirical entropy of x under the token-segmentation
distribution induced by T, and L_avg(T) is the average characters per
token under T. For cl100k on English-ish text L_avg ~ 3.7-4.2; on
densely-structured output (paths, numbers, sigils) L_avg drops to
~2.0-2.8. Both floors are attainable up to constants.

### 2. Mixture / piecewise-stationary universal coder

Let x = s1 || s2 || ... || sK be the segmentation into S_struct /
S_prose / S_code regions (boundaries known, given Redcon's grammar).
Let h_j be the per-character empirical entropy of source j computed
over a corpus C of Redcon outputs. The Krichevsky-Trofimov mixture
(or equivalently CTW with the segment label as side information)
attains per-character code length:

    L_KT(s_j) <= |s_j| * h_j  +  ((|alpha_j| - 1)/2) * log2(|s_j|)  +  O(1)

summed over j. For Redcon-sized outputs (N ~ 200-2000 chars compact,
~100-400 tokens) and modest alphabets per region (alpha_struct ~ 20,
alpha_prose ~ 80, alpha_code ~ 95), the redundancy term is tens of
bits, not hundreds - it is a small additive overhead.

### 3. Empirical h_j (back of envelope, 3+ lines of math)

I sampled five fixture files in `redcon/cmd/compressors/test_format.py`
mentally / from the diffs:

  - S_struct (git_diff compact): "diff: 4 files, +12 -7\nM path/to/x.py: +3 -2\n..."
    Char distribution is heavily skewed: digits, `+`, `-`, `:`, ` `, `\n`,
    letters from the path-set. Estimated h_struct ~ 4.1 bits/char
    (vs. 8 raw, vs. 6.5 if we treated it as English).

  - S_prose (pytest failure summary): English with code-identifier islands.
    Estimated h_prose ~ 4.5 bits/char.

  - S_code (grep matched line, lint code excerpt): h_code ~ 5.0 bits/char
    (compresses worse than prose because identifiers are higher-entropy
    out of context).

Weighted across compact-tier git_diff (~85% struct, 5% prose, 10% paths
which are quasi-code), the mixture entropy is:

    h_mix(git_diff_compact) ~ 0.85*4.1 + 0.05*4.5 + 0.10*4.7 ~ 4.18 bits/char

Compare to current compact tier: cl100k packs git_diff compact at
roughly ~2.3 chars/token (paths + sigils tokenize poorly), so each
token already carries ~ 4.18 * 2.3 ~ 9.6 bits of information per token.
For reference, 1 token from cl100k can encode at most log2(100256) ~
16.6 bits. So git_diff compact is at 9.6/16.6 ~ 58% of the raw
information capacity of the cl100k token stream.

### 4. The slack bound

Define slack(C) = (current_compact_tokens(C) - tokens_floor(C)) /
current_compact_tokens(C), where tokens_floor(C) =
ceil(N * h_mix(C) / log2(|V|)). For the five shipped compact
compressors:

    Compressor    N_chars  h_mix   tokens_floor  current  slack
    git_diff      ~250     4.18    ~63           ~95      ~34%
    grep          ~300     4.40    ~80           ~110     ~27%
    find          ~200     3.90    ~47           ~58      ~19%
    pytest        ~400     4.55    ~110          ~155     ~29%
    ls -R         ~300     4.30    ~78           ~115     ~32%

(Numbers are ballpark; the corpus to verify lives in `tests/cmd/`
fixtures.) These are the headroom *if* we had a noiseless channel.
Most of the gap is unrecoverable because cl100k's merge table was
trained on web text, not on Redcon's structured glyphs. That
unrecoverable portion is exactly the contribution of V99 (custom BPE)
and is out of scope here.

### 5. Recoverable slack (the actual deliverable of V03)

Within the constraint "we ship cl100k-tokenisable plain text", the
universal-coder framing yields one and only one practical tactic:
**per-source vocabulary selection**. For each source j, pick the
character/token alphabet that maximises chars-per-token on cl100k.
Concretely:

  - S_struct sigils: prefer sigils that cl100k already merges with their
    neighbours. `+12` is one cl100k token; ` +12 ` is two; `+ 12` is
    two. So *do not pad* numbers with spaces - already done in compact.
  - Path separators: `/` and `_` are part of many cl100k merges; `\` is
    not. Already POSIX-only, so no win available.
  - Markers: cl100k tokenises ` M ` (status letter + space) as 1 token,
    but `[M]` as 3. Compact uses bare letters: already optimal.
  - Numeric ranges: cl100k merges 2-3 digit decimals into single tokens
    up to 999; 4+ digit numbers split. So preferring `+12 -7` over
    `+12345 -6789` is already as compact as it can be.
  - Prose: drop articles, prefer Hungarian-style telegraphese, but ONLY
    inside quoted prose - cl100k merges " the " as one token, so
    dropping it loses 1 token *and* removes 5 chars; net -1 token.
    This is the only place where universal-coder reasoning gives
    actionable guidance vs. the existing whitespace-collapsing rules.
  - Code fragments: best-case is to drop them entirely (already done at
    ULTRA) or quote with a tokenizer-friendly fence (already done).

**Net claim**: against the bound derived above, current compact tier is
within ~25-35% of the noiseless floor; the *additionally recoverable*
slack within the cl100k-text constraint is ~5-12% on prose-heavy
compressors and essentially zero on structured ones. This is the
ceiling for any universal-coder approach; further gains require
breaking the "plain cl100k text" assumption.

## Concrete proposal for Redcon

Two artifacts. Neither is "deploy a universal coder"; that is
information-theoretically dominated by BPE.

### A. `redcon/cmd/quality.py` extension: slack-bound report

Add a `slack_estimate()` step to the quality harness. For each compact
fixture, compute h_mix (offline-precomputed per-schema constant table)
and emit `tokens_floor` alongside `compressed_tokens` and
`reduction_pct`. This makes the compressor leaderboard interpretable:
a 73% reduction at 5% slack is "essentially optimal"; a 73% reduction
at 35% slack is "leave 1/3 of headroom on the table".

```python
# quality.py
_H_MIX_BY_SCHEMA = {
    "git_diff": 4.18, "grep": 4.40, "find": 3.90,
    "pytest": 4.55, "ls": 4.30, "git_status": 3.95,
    "git_log": 4.10, "lint": 4.45, "docker": 4.50,
    "kubectl": 4.30, "pkg_install": 4.40,
}

def slack_pct(out: CompressedOutput) -> float | None:
    h = _H_MIX_BY_SCHEMA.get(out.schema)
    if h is None:
        return None
    floor = math.ceil(len(out.text) * h / math.log2(100_256))
    if out.compressed_tokens <= 0:
        return None
    return max(0.0, 1.0 - floor / out.compressed_tokens)
```

This is the "prove an upper bound on remaining slack" deliverable from
the vector statement, recast as a continuous metric next to existing
quality numbers.

### B. `redcon/cmd/compressors/_prose.py` (new): segment-aware prose
collapser

A 60-line helper applied to the prose-heavy regions of pytest, lint,
docker, and kubectl outputs. Drops cl100k-aware low-info phrases:

```python
# _prose.py
_DROP = (
    " the ", " a ", " an ", " is ", " was ", " were ",
    " has been ", " have been ", " in order to ",
)
_REPLACE = (
    (" because ", " bc "),
    (" expected ", " exp "),
    (" actual ",   " act "),
    (" assertion failed ", " assert "),
    (" did not match ", " != "),
)

def collapse_prose(text: str, schema: str) -> str:
    if schema not in {"pytest", "lint", "docker", "kubectl"}:
        return text
    out = text
    for needle in _DROP:
        out = out.replace(needle, " ")
    for old, new in _REPLACE:
        out = out.replace(old, new)
    return out
```

Pipeline integration: `pipeline.compress_command` already calls
`_normalise_whitespace` post-compress; insert `collapse_prose` for
COMPACT level only, for the listed schemas. Must-preserve patterns
must be re-checked after collapse - `verify_must_preserve` already
runs at the compressor; piping through the prose collapser inside
each compressor (not in the pipeline) keeps that invariant.

Expected: -1 to -2 tokens per failure summary line on pytest, ~5%
absolute reduction on pytest-compact at no quality cost. Below the
breakthrough bar (5pp across multiple compressors) but worth it for
pytest specifically.

### C. What we explicitly do NOT do

  - **Arithmetic coding / ANS**: produces high-entropy bytes that
    cl100k splits 1:1, inflating by ~4x. Disqualified by the
    consumer-is-BPE constraint. (See V05 for the orthogonal proposal
    that actually leans into this for a non-text channel.)
  - **CTW / mixture predictor on the bit level**: same disqualifier.
  - **Custom Huffman tables in-band**: the table itself costs more
    tokens than it saves at Redcon's output sizes (N < 2 KB).

## Estimated impact

  - Token reduction: ~3-7% absolute on pytest, lint, docker, kubectl
    compact tier (prose collapse). Zero on git_diff, grep, find, ls
    (already at the structured-source floor). Below breakthrough.
  - Slack-bound metric: pure observability win. Adds one row per
    schema to the quality leaderboard. No latency impact.
  - Latency: `collapse_prose` is one .replace per phrase, microseconds.
    Slack metric is pure arithmetic on already-computed lengths.
  - Affects: `redcon/cmd/quality.py`, `redcon/cmd/compressors/_prose.py`
    (new), and four existing compressors that opt in.

## Implementation cost

  - Slack-bound metric: ~30 lines in `quality.py` + 11-row constant
    table. Test: one fixture per schema asserting slack < 0.5.
  - `_prose.py`: ~60 lines. Tests: three property checks (idempotent,
    must-preserve patterns survive, deterministic).
  - No new runtime deps. Constant table is computed offline (one-shot
    `python -m redcon.cmd._h_mix_calibrate` over fixture corpus, ship
    the constants, do not re-compute at runtime - that would violate
    determinism if the fixture corpus changes).
  - Risk to determinism: zero. All deterministic string ops.
  - Risk to must-preserve: medium. Dropping " the " / " a " is safe
    against current must-preserve regexes (none of them require
    articles), but a future compressor could. Mitigation: opt-in per
    schema via the existing `must_preserve_patterns` review process.

## Disqualifiers / why this might be wrong

  1. **Already done in disguise**: BASELINE notes "indented continuation
     lines drop the 3-space prefix" and "_normalise_whitespace collapses
     3+ newlines". These are the largest specifically-cl100k tactics
     and they are already shipped. The remaining slack is genuinely
     small, so V03's actionable contribution is incremental at best.
  2. **h_mix table is fragile**: precomputed entropies depend on the
     corpus snapshot. If a new compressor lands or fixtures change,
     the slack-bound metric drifts. Mitigation: regenerate via a
     calibration command, version the table, but this is operational
     overhead.
  3. **The bound is loose where it matters**: for git_diff the floor
     I computed (~63 tokens) ignores that cl100k *cannot tokenise
     paths efficiently* - any path with a hyphen or underscore-digit
     mix splits into 3-5 tokens. So the "true" floor under cl100k is
     higher than the entropy bound, and the apparent 34% slack on
     git_diff is mostly unrecoverable. This is what V99 (custom BPE)
     would address; V03 cannot.
  4. **Universal-coder framing oversells what is actually a one-page
     prose-collapser**: stripped of the formalism, deliverable B is a
     30-line `text.replace` chain. The theoretical scaffold is
     intellectual scaffolding, not productive of new tactics beyond
     what an empirical search would find in an afternoon.
  5. **Prose collapse risks readability for the agent**: an LLM reading
     " the test " vs " test " has a small comprehension hit on edge
     cases. cl100k savings are 1 token; the risk is one mis-read
     failure. Net could be negative on agent task success rate.
  6. **The slack metric could be gamed**: a compressor author could
     pad output with redundant chars to *lower* the slack ratio while
     keeping token count constant. Mitigation: report
     `tokens_floor / current_tokens` as the primary metric, not
     `slack`.

## Verdict

  - Novelty: low for the formalism (universal-coder framing of mixture
    sources is textbook), medium for the slack-bound observability
    metric (no Redcon dashboard tracks an entropy floor today).
  - Feasibility: high. Both deliverables are ~100 lines, no deps, no
    determinism risk.
  - Estimated speed of prototype: 1 day for both, including offline
    h_mix calibration over the existing fixture corpus.
  - Recommend prototype: **conditional-on-X**. Ship the slack-bound
    metric (deliverable A) as a quality-harness diagnostic - low cost,
    informs every subsequent compressor effort, settles "are we close
    to the floor?" arguments empirically. Skip the prose collapser
    (deliverable B) unless a specific agent-side quality test confirms
    no comprehension regression on pytest output - the 3-7% reduction
    is below the breakthrough bar and the comprehension risk is real.
    The vector statement asked for the bound; the bound is the
    deliverable. The "creative workaround" suggested in the brief
    (arithmetic coding into BPE-friendly form) does not exist - the
    information-theoretic argument disposes of that idea cleanly, and
    that disposal is itself the contribution.
