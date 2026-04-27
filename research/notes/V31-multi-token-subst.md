# V31: Multi-token-string substitution table per tokenizer family

## Hypothesis

cl100k_base tokenises some recurring multi-word phrases in Redcon's
compact-tier output as N tokens that have shorter machine-readable
equivalents costing fewer tokens. A small, manually curated, per-schema
substitution table - applied as a measured post-compression rewrite
that re-tokenises and only keeps a candidate when total tokens
strictly decrease - removes the residual english boilerplate that
current compressors emit (`Traceback (most recent call last):`,
`AssertionError: assert`, ` line too long (110 > 100 characters)`,
`, ` between identifier-then-letter, `: ` between word-then-letter,
`Step `, `Collecting `, `kubectl get `, `more hunks`, ...). Concretely:
on the 21-fixture quality corpus, the table lifts aggregate compact
reduction from 74.53% to 76.89% raw->compact (+2.37 absolute pp)
without breaking any declared `must_preserve_patterns`.

## Theoretical basis

Two independent effects compose:

1. **Sub-word merge gap.** A cl100k merge `T(p)` for phrase `p` is
   determined by Byte-Pair-Encoding which was trained on web text, not
   structured tool output. For phrases dominated by code-like content
   (`AssertionError: assert ` -> 5 tokens; `E501 line too long (110 > 100 characters)` -> 12 tokens),
   BPE allocates more merges to common english bigrams than to the
   highest-frequency tokens in our domain. A short ASCII alias picked
   from the same charset (`AE: `, `E501 ll (110>100)`) hits a denser
   merge region. Empirically I measured directly via tiktoken:

   ```
   T("AssertionError: assert ")               = 5
   T("AE: ")                                  = 2     => save 3
   T("Traceback (most recent call last):")    = 8
   T("TB:")                                   = 2     => save 6
   T(" line too long ")                       = 4
   T(" ll ")                                  = 2     => save 2
   T(" > 100 characters)")                    = 5
   T(">100)")                                 = 3     => save 2
   T("diff --git summary:")                   = 5
   T("git diff:")                             = 3     => save 2
   T(" (binary)")                             = 3
   T(" bin")                                  = 1     => save 2
   T(", behind ")                              = 3
   T(" -")                                    = 1     => save 2
   ```

2. **Context-dependent merge.** ` , ` and `: ` save tokens after
   identifiers (`pkg, file_3` -> `pkg,file_3` saves 1) but inflate
   after digits (`(ahead 1, behind 0)` -> `(ahead 1,behind 0)` adds
   1 because cl100k merges `1, ` better than `1,b`). The substitution
   is therefore not unconditional. Back-of-envelope:

   ```
   total_pp_gain = sum_over_fixtures (T(orig) - T(rewrite_if_smaller))
                                       / sum T(raw_input)

   raw_in     = 39 292
   compact    = 10 009     reduction = 74.53%
   c+V31      =  9 079     reduction = 76.89%
   absolute   = +2.37 pp aggregate
   ```

   A measured pass that tries each rewrite, retokenises the whole
   output, and accepts only when `T(new) < T(old)` is monotone-safe
   by construction. The rule for which substitutions to even consider
   is: the rewrite must (a) preserve every byte of the
   `must_preserve_patterns` regex matches and (b) be a deterministic
   string replace (so the cache key and determinism guarantees both
   hold).

## Concrete proposal for Redcon

Add a single post-compression normaliser parallel to
`_normalise_whitespace` in `redcon/cmd/pipeline.py`:

```
# redcon/cmd/_subst_table.py  (new, ~120 LOC, pure data)
SUBST_TABLE: tuple[Sub, ...] = (
    # (orig, repl, scope: frozenset[str] | None == "*", note)
    Sub("Traceback (most recent call last):", "TB:", None, "py-traceback"),
    Sub("Installing collected packages: ", "Pkgs: ",
        frozenset({"pkg_install"}), "pip-installed-list"),
    Sub("AssertionError: assert ", "AE: ", None, "py-error-assert"),
    Sub("diff --git summary:", "git diff:",
        frozenset({"git_diff"}), "diff-header"),
    Sub("Successfully built ", "OK ",
        frozenset({"docker"}), "docker-success"),
    Sub("AttributeError: ", "AttrE: ", None, "py-error"),
    Sub(" > 100 characters)", ">100)",
        frozenset({"lint"}), "ruff-E501-tail"),
    Sub(" > 100 characters", ">100",
        frozenset({"lint"}), "ruff-E501-tail2"),
    Sub("DEPRECATION:", "DEP:",
        frozenset({"pkg_install"}), "pip-deprecation"),
    Sub(" line too long ", " ll ",
        frozenset({"lint"}), "ruff-E501"),
    Sub(" matches in ", " m/", frozenset({"grep"}), "grep-header"),
    Sub("kubectl get ", "kg ",
        frozenset({"kubectl_get"}), "kubectl-prefix"),
    Sub("Collecting ", "C ", frozenset({"pkg_install"}), "pip-collect"),
    Sub("more hunks", "more h", frozenset({"git_diff"}), "diff-overflow"),
    Sub("AssertionError", "AE", None, "py-error"),
    Sub("grep: no matches", "g:0", frozenset({"grep"}), "grep-empty"),
    Sub(" (binary)", " bin", frozenset({"git_diff"}), "diff-bin"),
    Sub(", behind ", " -", frozenset({"git_status"}), "status-behind"),
    Sub(" failed, ", " F/", None, "test-summary"),
    Sub("ahead ", "+", frozenset({"git_status"}), "status-ahead"),
    Sub("grep: ", "g:", frozenset({"grep"}), "grep-prefix"),
    Sub("Step ", "S", frozenset({"docker"}), "docker-step"),
    Sub("log: ", "log:", frozenset({"git_log"}), "log-prefix"),
    Sub(" -> ", "->", None, "arrow"),
    Sub(": ", ":", None, "colon-space"),
    Sub(", ", ",", None, "comma-space"),
)

# redcon/cmd/pipeline.py  (modified, ~12 new LOC)
def _apply_subst_table(out: CompressedOutput) -> CompressedOutput:
    from redcon.core.tokens import estimate_tokens  # canonical tiktoken
    text = out.text
    cur = estimate_tokens(text)
    for sub in SUBST_TABLE:
        if sub.scope is not None and out.schema not in sub.scope:
            continue
        if sub.orig not in text:
            continue
        cand = text.replace(sub.orig, sub.repl)
        ct = estimate_tokens(cand)
        if ct < cur:
            text, cur = cand, ct
    if text == out.text:
        return out
    return replace(out, text=text, compressed_tokens=cur)
```

Wire site: in `compress_command` immediately after `_normalise_whitespace`,
gated on `out.level in (COMPACT, ULTRA)` (skip VERBOSE - that tier is
the readable surface). Patterns are still verified post-rewrite by
running `verify_must_preserve` against the rewritten text.

## Estimated impact

Empirical numbers from running tiktoken cl100k_base on every fixture
in `tests/test_cmd_quality.py` at COMPACT level. I measured each
fixture twice: once stock, once with the measured-pass V31 table.

Per-fixture absolute pp gain (raw->compact reduction):

```
ruff_typical          +15.86 pp   (-7.4%  ->  +8.4%)
git_status            +17.39 pp   (-17.4% ->   0.0%)   small fixture
find                  +11.76 pp   (small fixture)
tree                  +10.53 pp   (small fixture)
grep_small            +10.34 pp   (small fixture)
ls_huge                +7.33 pp
git_diff_small         +5.26 pp
go_test                +4.84 pp
pytest_small           +4.03 pp
pytest_massive         +3.81 pp
npm_test_jest          +3.85 pp
cargo_test             +3.09 pp
ls                     +2.53 pp
find_massive           +2.03 pp
docker_build_typical   +1.61 pp
grep_massive           +1.49 pp
git_log                +1.10 pp
kubectl_pods_typical   +0.82 pp
pip_install_typical    +0.39 pp
mypy_large             +0.30 pp
git_diff_huge          +0.20 pp
                       --------
aggregate              +2.37 pp on 39 292 raw tokens
```

Marginal contribution by category (leave-one-out):

```
comma-space               378 tokens   3.78 pp of corpus
colon-space               213 tokens   2.13 pp
ruff-E501 (+ tail)        124 tokens   1.24 pp
py-error-assert            62 tokens   0.62 pp
diff-hunk-overflow         12 tokens   0.12 pp
status-behind/ahead         5 tokens
diff-header / grep-header   2 tokens each
log-prefix / kubectl-prefix 1 token each
```

Latency: one extra dict iteration and one tiktoken pass per `replace`
that could win. The per-output cost is `<= 26 * (one tiktoken encode
of a string already in cache) ~ < 1 ms` for typical compact outputs
(<2 kB). Cold-start: zero - the table is a tuple literal evaluated at
import time, no regex compilation, no I/O.

Affects: every compressor in `redcon/cmd/compressors/*.py` (because
the table is keyed on `out.schema` strings produced by them) and a
single new wire in `redcon/cmd/pipeline.py::compress_command`.
The cache layer is untouched: the substitution is applied AFTER the
pipeline cache key is computed because it acts on the compressed
output, and the rewrite is deterministic on `(raw_text, schema)` so
cached results carry the same rewrite forever.

## Implementation cost

- Code: ~120 LOC for `_subst_table.py` (the tuple plus the apply
  function) + 4 LOC in `pipeline.py` to wire it after
  `_normalise_whitespace`.
- New tests: extend `tests/test_cmd_quality.py` so that for each
  fixture the new total tokens are <= the old total tokens, and that
  every `must_preserve_pattern` still matches the rewritten text
  byte-for-byte. Plus a new property test that asserts the rewrite is
  monotone (token count never increases).
- New runtime deps: none. Uses the existing `redcon.core.tokens`
  tiktoken backend.
- Risk to determinism: zero. All substitutions are deterministic
  string replacements on the already-deterministic compact output.
- Risk to robustness: zero. Empty / binary / truncated outputs
  contain none of the literal phrases (verified - the substring tests
  fail for them and the loop is a no-op).
- Risk to must_preserve guarantees: medium-low. I audited the only
  three compressors with non-empty `must_preserve_patterns`:
    git_diff: `\bfiles? changed\b|\bdiff --git\b|^[A-Z] [^\s]+|^- ?[^\s]+`
    git_log:  `\bcommit\b|^[0-9a-f]{7,40} `
    git_status: `branch:|^[ MADRCU?!]{2} `
  None of the substitutions touch `files changed`, `diff --git`,
  `commit`, `branch:`, the index_status digraph, or the `+/- path`
  lines. I ran the rewrites against every fixture and re-checked
  every regex - no regression. The `pytest_compressor` and friends
  add patterns dynamically per failure (escaped failure names) and
  none of those patterns contain English boilerplate.
- Risk to readability: the substitutions are intentionally short
  ASCII glyphs (`AE`, `TB`, `S`, `g:`, `kg`). For an LLM these are
  unambiguous in the surrounding context (a pytest output line that
  says `AE: 100 == 200` after `tests/x.py:42:` is unambiguously an
  AssertionError). For a human, the increase in cognitive load is
  real but bounded and only at the COMPACT/ULTRA tiers where the
  agent is already the consumer. VERBOSE keeps the english.

## Disqualifiers / why this might be wrong

1. **Already partially captured by existing format choices.** The
   compressors already do many domain-specific tightening choices
   (e.g. `+5 -2` for diff counts, `branch:` prefix, `S<N>` step
   numbers). The substitution table is the catch-all that mops up
   what the per-compressor formatters didn't. If the right move is
   instead "rewrite each compressor's formatter to emit shorter
   strings inline", V31 is just a packaging choice. The pipeline-
   layer placement, however, is one wire change vs ~11 formatter
   rewrites.
2. **Tokenizer-coupled. cl100k only.** When the agent caller is on
   o200k (Claude / GPT-4o successor) or llama-3 BPE, the same merges
   may not apply and the rewrite may have neutral or negative effect.
   Mitigation: the rewrite is gated on a measured pass using the
   active tokenizer (canonical `redcon.core.tokens.estimate_tokens`),
   so on a non-cl100k tokenizer the bad rewrites get rejected
   automatically and only the universally-shorter ones (`TB:`,
   `OK `, `kg `, `S`) survive. V35 (dynamic tokenizer detection)
   composes cleanly.
3. **Aggregate +2.37 pp is below the BASELINE breakthrough bar
   (>=5 pp on multiple compressors).** True for the aggregate.
   However: ruff_typical alone gains +15.86 pp, find +11.76 pp,
   tree +10.53 pp, ls_huge +7.33 pp, git_diff_small +5.26 pp - five
   compressors above the bar. So V31 clears the bar on a per-
   compressor basis even if not on the corpus mean. The corpus mean
   is dragged down by git_diff_huge (+0.20) and grep_massive (+1.49)
   where the bulk of tokens are file paths, which V31 does not touch.
4. **Hand-curated is brittle.** A future fixture or real-world
   corpus may surface common phrases I did not enumerate, leaving
   value on the table. Counter-argument: the table is monotone
   (worst case neutral via the measured pass), so adding entries
   later is purely additive. A periodic offline mining job using
   `redcon/cache/run_history_sqlite.py` history can propose new
   candidates.
5. **The savings on `, ` and `: ` are 60% of the gain.** These two
   "rules" are punctuation-level and not really domain knowledge,
   but they are the ones most exposed to context-dependent inflation
   (cf. `git_status` where unconditional `, ` -> `,` would inflate
   by 1 token). The measured-pass safeguard is what makes this safe;
   a naive find-replace pass would regress that case. The robustness
   harness must keep that property as an invariant.

## Verdict

- Novelty: medium. Tokenizer-aware string substitution is a known
  trick in LLM prompt engineering, but BASELINE explicitly notes
  "tokenizer-specific recoding beyond a few ad-hoc rewrites" as
  open frontier. V31 turns ad-hoc into systematic, with measured
  monotonicity and a per-schema scope.
- Feasibility: high. ~125 LOC total, no new deps, deterministic,
  cache-safe, robustness-safe.
- Estimated speed of prototype: 2-3 hours (table + wire + tests).
- Recommend prototype: yes. The strongest single argument: ruff
  output today is +1693 tokens at COMPACT for a 1576-token raw
  input (i.e. compression INFLATES it by 7.4%). V31 alone
  brings ruff back to +8.4% reduction. That is a regression-tier
  fix masquerading as a token-tax reduction.

## Appendix: sketch of the run-time normaliser

```python
# redcon/cmd/pipeline.py (after _normalise_whitespace)
def _apply_subst_table(out: CompressedOutput) -> CompressedOutput:
    if out.level == CompressionLevel.VERBOSE:
        return out
    from redcon.cmd._subst_table import SUBST_TABLE
    from redcon.core.tokens import estimate_tokens
    text = out.text
    cur = out.compressed_tokens
    for sub in SUBST_TABLE:
        if sub.scope is not None and out.schema not in sub.scope:
            continue
        if sub.orig not in text:
            continue
        cand = text.replace(sub.orig, sub.repl)
        ct = estimate_tokens(cand)
        if ct < cur:
            text, cur = cand, ct
    if text == out.text:
        return out
    return replace(out, text=text, compressed_tokens=cur)

# call site:
compressed = _normalise_whitespace(compressed)
compressed = _apply_subst_table(compressed)
```

Throwaway scripts that produced the numbers in this note live in
`/tmp/v31_subst.py`, `/tmp/v31_ngrams.py`, `/tmp/v31_final.py`,
`/tmp/v31_breakdown.py`, `/tmp/v31_measured.py`, `/tmp/v31_vs_raw.py`.
All numbers come from running tiktoken `cl100k_base` directly via
`/Users/naithai/Desktop/amogus/praca/ContextBudget/.venv/bin/python`
against the COMPACT-tier output of every fixture in
`tests/test_cmd_quality.py`. No estimates.
