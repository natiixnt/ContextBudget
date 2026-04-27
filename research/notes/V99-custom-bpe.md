# V99: Custom BPE tokenizer trained on Redcon output corpus

## Hypothesis

cl100k_base is a generic tokenizer trained on web text and source code
at large. Redcon's compact-tier outputs have a much narrower
distribution: paths like `redcon/cmd/compressors/git_diff.py`, headers
like `git diff: 12 files, +96 -24`, ruff lines like
`src/api/handlers.py:78:5: E501 line too long`, kubectl pod-name
patterns, pytest summary tails. Train a custom byte-pair encoder on a
corpus of these outputs and the per-token information density should
rise: highly-recurring spans become single tokens. The KICKER: any BPE
trained narrowly will inflate generic English/code, so the AGENT only
nets a win if Redcon outputs are a substantial fraction of its input.
And: shipping a custom tokenizer requires a backend change in the LLM
serving stack, which we do not control. So V99 is best framed as a
_North Star ceiling_ measurement: how much pure-tokenizer headroom do
we still have above the format choices already in the compressors?

## Theoretical basis

Per-bit cross-entropy bound. cl100k achieves around `H_cl(X) bits/byte`
on Redcon's output distribution X. A BPE retrained on samples from X
will, in the limit of vocabulary size and corpus size, approach the
unigram entropy `H_unigram(X) bits/byte` over the unit chosen by the
pre-tokeniser; any improvement is bounded by `H_cl(X) - H_X(X)` where
`H_X` is the unigram entropy under the X-trained merges.

Empirically on our 21-fixture corpus:

```
chars/token under cl100k (eval = compact outputs): 2.93
chars/token under custom BPE (gpt_split, vocab 4734): 3.09
ratio = 2.93/3.09 = 0.949
=> custom BPE is 5.14% denser per token on Redcon output.
```

Generic-text inflation (probes outside the training distribution):

```
chars               cl100k     custom    delta
BASELINE.md   6286   1505       2405    +59.8%
pipeline.py   11907  2699       4258    +57.8%
INDEX.md      9690   2292       4092    +78.5%
```

Mixing model: agent input has Y tokens of Redcon output (savings 5.1%)
and X tokens of generic text (inflation ~60%). Total under custom is
`X*1.60 + Y*0.949`. Break-even with cl100k:

```
X + Y = 1.60*X + 0.949*Y
0.60*X = 0.051*Y
Y/X = 11.76
=> Redcon outputs must be > 92.2% of total agent input
   for V99 to net-win at the agent level.
```

That ratio is unrealistic for a real coding agent: prompts, code files,
and prose dominate context, with tool outputs being typically
20-50% of the budget.

## Concrete proposal for Redcon

There is no production change. V99 is operationalised as:

1. A standalone offline script (`/scripts/research/train_redcon_bpe.py`,
   or kept under `research/`) that:
   - Walks every fixture in `tests/test_cmd_quality.py` and runs each
     compressor at all three tiers.
   - Optionally augments with `compress_command(...)` invocations on
     the Redcon repo itself (git, grep, ls, find).
   - Trains a `tokenizers.Tokenizer(BPE)` with a cl100k-style
     pre-tokeniser regex on the resulting corpus.
   - Reports `chars/token` against cl100k for every fixture,
     `delta_pct` for generic probes, the implied break-even ratio,
     and writes the merges to `research/artifacts/redcon_bpe.json`.

2. A new "compression ceiling" gauge surfaced in
   `redcon/cmd/quality.py` (optional, opt-in via env var) that, given
   a fixture, reports the gap between current cl100k token count and
   the ideal-tokenizer floor. If a compressor's compact output sits at
   3.0 chars/token under cl100k but the custom-BPE ceiling is 3.2,
   the residual is real and a target for V31 / V32 / V40-style
   tokenizer-aware rewrites. If the gap is <2%, no further format
   tightening will pay - move to a different compression dimension
   (cross-call dedup V41-V50, semantic compression V11-V20).

```
# scripts/research/train_redcon_bpe.py  (offline, no production wire)
def train(corpus_path: Path, vocab_size: int) -> Tokenizer:
    tok = Tokenizer(models.BPE(unk_token="<UNK>"))
    tok.pre_tokenizer = pre_tokenizers.Sequence([
        pre_tokenizers.Split(Regex(CL100K_REGEX), behavior="isolated"),
        pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False),
    ])
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=vocab_size,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        special_tokens=["<UNK>"],
        show_progress=False,
    )
    tok.train([str(corpus_path)], trainer)
    return tok

def evaluate_ceiling(per_fixture_text: dict[str, str], tok) -> dict:
    cl = tiktoken.get_encoding("cl100k_base")
    return {k: {"cl100k": len(cl.encode(v)),
                 "ceiling": len(tok.encode(v).ids)}
            for k, v in per_fixture_text.items()}
```

Production code is untouched. Redcon continues to count tokens with
cl100k. The trained merges live in `research/artifacts/` for
benchmarking only.

## Estimated impact

- Token reduction (operational, end-to-end agent): expected
  approximately zero. The BPE cannot ship to the LLM, and even if it
  could, the mixture analysis says Redcon outputs would need to be
  >92% of agent input to net-win. Any realistic agent session sits
  far from that.
- Token reduction (theoretical, per-fixture compact tier vs cl100k):
  measured -5.1% aggregate, with per-fixture wins ranging from 0%
  (path-dominated outputs like `find_massive`, `grep_massive`,
  `ls_huge`) up to +15.8% (`docker_build_typical`). Real-world
  Redcon-on-Redcon self-runs (where the corpus closely matches
  training) win consistently +5% to +20% on a 10-command sample.
- Latency: zero in production (BPE never loaded). For the offline
  benchmark: ~30 seconds to train on a 1 MB corpus.
- Affects: nothing in production. The `redcon.core.tokens`
  cl100k-backed estimator stays canonical. The "compression ceiling"
  gauge would touch `redcon/cmd/quality.py` only when an opt-in env
  var is set.

## Implementation cost

- Lines of code: ~150 LOC for the training script;
  ~30 LOC for the optional quality.py gauge.
- New runtime deps: `tokenizers >= 0.19` (HuggingFace). About 5 MB
  installed. Does not break "no required network": only used at
  benchmark time. Does not introduce embedding models.
- Risks to determinism: zero - production unchanged.
- Risks to robustness: zero - production unchanged.
- Risks to must-preserve guarantees: zero.
- Cold-start: zero - training script is invoked manually, not from
  any hot path. The tokenizer file is JSON, ~200 kB at vocab=8000.

## Disqualifiers / why this might be wrong

1. **The single biggest issue is the deployment story.** Tokenizers
   are baked into the model weights of every commercial LLM I know
   of - Anthropic, OpenAI, Google, Meta. There is no public API
   surface to "swap tokenizer for this turn". Anthropic's prompt
   caching is keyed by token sequence, not configurable per-tokenizer.
   So even if our BPE achieves a -50% reduction on Redcon outputs,
   the agent cannot consume it. V99's value is purely as a CEILING
   measurement.

2. **The training corpus is small and saturates the merge graph.**
   With ~1 MB of Redcon-output text I cannot drive vocab past ~5k
   actual merged tokens (the byte-level / gpt-split BPE trainer
   exhausts the merge potential well below the 32k or 100k targets).
   To match cl100k's vocab budget (100k), we'd need on the order of
   100s of MB to 10s of GB of in-distribution Redcon output, which
   only exists if Redcon is run at scale across many repos for
   months. Currently we have 21 quality fixtures plus some
   parameter-sweep variants; that's it. A larger corpus might widen
   the per-fixture wins, but it cannot fix the deployment
   disqualifier (1).

3. **The custom BPE specialised hard, which is exactly the failure
   mode described in the brief.** Inspecting the longest learned
   tokens the trainer produced shows merges like
   `' KubectlGetCompressor'` (one token for an entire 21-character
   identifier), `' LocalFileSummaryCacheBackend'`,
   `' BudgetPolicyViolationError'`, `'ResolvedBuiltinTokenEstimator'`.
   These exist because `grep -rn 'def compress' redcon/cmd/compressors`
   on the Redcon repo emits these names hundreds of times. The
   tokenizer overfits to the Redcon source tree itself - useful for
   self-runs (+19.8% on `selfrun__grep_-rn_def compress...`) but
   the same merges are noise on any other repo. So the win on the
   self-run subcorpus does not generalise to other users.

4. **Pre-tokenisation matters more than vocab.** With a naive
   `ByteLevel` pre-tokeniser the BPE LOSES to cl100k by +15.7%
   even on the in-distribution eval. Switching to a cl100k-style
   regex split reverses that to a +5.1% win. So most of the
   "win" reproduces what cl100k already has; we are scraping the
   margin, not creating new compression. V31 (multi-token-string
   substitution) and V32 (token-boundary-aware whitespace) extract
   this same margin _without_ a backend change.

5. **Path-dominated outputs benefit minimally.** The largest
   compact-tier outputs in the harness (`find_massive`,
   `grep_massive`, `ls_huge` - thousands of tokens each) all show
   `+0.0%` delta. cl100k already tokenises common path components
   (`./`, `/`, `.py`, `src/`, `tests/`) near-optimally, and the long
   tail of unique filenames cannot be merged further without
   memorising specific filenames - which would generalise to
   exactly nobody.

## Verdict

- Novelty: medium. The training is mechanical (prior art:
  byte-pair encoding, Sennrich 2015), but the framing as a
  per-tokenizer compression ceiling is useful. The mixture analysis
  showing the >92% break-even point is a real (and quotable) result.
- Feasibility: low for production deployment (would require
  controlling the LLM serving stack); high as an offline benchmark.
- Estimated speed of prototype: 4-6 hours (corpus collection, BPE
  training, mixture analysis, ceiling gauge). I built the prototype
  during this research session; the numbers below are real.
- Recommend prototype: conditional. As production code: no. As an
  offline ceiling measurement to validate that V31/V32/V40 are
  squeezing the right margin: yes, ~half a day of work, useful as
  a kill-switch for the entire "tokenizer-aware rewrite" theme. If
  the ceiling gap is <5%, stop investing in tokenizer tricks and
  pivot to V41-V50 (cross-call dedup) where the wins compound.

## Appendix: empirical numbers

All numbers from
`/Users/naithai/Desktop/amogus/praca/ContextBudget/.venv/bin/python`
running the throwaway scripts under `/tmp/v99_*.py`. Corpus written to
`/tmp/v99_corpus/`. Compact-only training is `corpus_compact_only.txt`
(44 503 chars after augmentation), full training is `corpus_all.txt`
(1 062 171 chars after parameter-sweep augmentation). Both eval'd on
the 21-fixture compact-only set (15 205 cl100k tokens).

Vocab sweep (best scheme = `gpt_split` regex pretokenisation):

```
scheme        vocab_target  vocab_actual  custom  cl100k  ratio  delta    chars/tok
bytelevel       4 000        4 000         17832   15205   1.173  +17.3%   2.50
bytelevel       8 000        5 456         17598   15205   1.157  +15.7%   2.53
bytelevel      16 000        5 456         17598   15205   1.157  +15.7%   2.53
gpt_split       4 000        4 000         14689   15205   0.966   -3.4%   3.03
gpt_split       8 000        4 734         14423   15205   0.949   -5.1%   3.09
gpt_split      32 000        4 734         14423   15205   0.949   -5.1%   3.09
```

Vocab saturates at 4 734 actual merges; further budget yields no new
useful merges given this corpus size. cl100k-style regex pre-tokeniser
beats raw byte-level by ~20pp.

Per-fixture compact tier (best tokenizer, 21 cases):

```
docker_build_typical    cl100k=  95  custom=  80  delta=+15.8%  win
kubectl_pods_typical    cl100k= 132  custom= 112  delta=+15.2%  win
tree                    cl100k=  22  custom=  19  delta=+13.6%  win
pytest_small            cl100k=  63  custom=  56  delta=+11.1%  win
find                    cl100k=  28  custom=  25  delta=+10.7%  win
pip_install_typical     cl100k=  56  custom=  52  delta= +7.1%  win
pytest_massive          cl100k= 740  custom= 711  delta= +3.9%  win
ls                      cl100k=  30  custom=  29  delta= +3.3%  win
git_diff_huge           cl100k= 394  custom= 382  delta= +3.0%  win
mypy_large              cl100k= 356  custom= 352  delta= +1.1%  win
ruff_typical            cl100k= 348  custom= 345  delta= +0.9%  win
cargo_test              cl100k=  35  custom=  35  delta= +0.0%  tie
find_massive            cl100k=1135  custom=1135  delta= +0.0%  tie
git_diff_small          cl100k=  50  custom=  50  delta= +0.0%  tie
git_log                 cl100k=  18  custom=  18  delta= +0.0%  tie
git_status              cl100k=  27  custom=  27  delta= +0.0%  tie
go_test                 cl100k=  18  custom=  18  delta= +0.0%  tie
grep_massive            cl100k=2259  custom=2259  delta= +0.0%  tie
grep_small              cl100k=  38  custom=  38  delta= +0.0%  tie
ls_huge                 cl100k=2105  custom=2105  delta= +0.0%  tie
npm_test_jest           cl100k=  39  custom=  39  delta= +0.0%  tie
                                                           ----
aggregate (15205 cl tokens -> 14423 custom tokens): -5.14%
```

Per-fixture self-runs (10 real `compress_command` invocations on the
Redcon repo, sorted by win):

```
selfrun__grep_-rn_def_compress_redcon_cmd_compressors  +19.8%
selfrun__grep_-rn_import_redcon_cmd                    +11.7%
selfrun__git_status                                    +10.5%
selfrun__git_log_--oneline_-n_30                        +9.7%
selfrun__git_diff_HEAD~5_HEAD                           +9.4%
selfrun__find_redcon_-name_*.py_-type_f                 +9.1%
selfrun__grep_-rn_def_test__tests                       +9.0%
selfrun__ls_-l_redcon_cmd_compressors                   +6.6%
selfrun__ls_-l_redcon_cmd                               +6.5%
selfrun__git_diff_HEAD~3_HEAD                           +5.8%
```

Self-runs win much harder than synthetic fixtures because they include
many repeated Redcon-internal symbol names that the BPE memorised
(see disqualifier 3). On a non-Redcon repo, those merges are dead
weight.

Generic-text inflation (best tokenizer, vocab 4 734):

```
BASELINE.md   chars=  6286  cl100k= 1505  custom= 2405  +59.8%
pipeline.py   chars= 11907  cl100k= 2699  custom= 4258  +57.8%
INDEX.md      chars=  9690  cl100k= 2292  custom= 4092  +78.5%
                                                       average ~+65%
```

Mixture break-even derivation:

```
let X = generic-text tokens, Y = Redcon-output tokens
total_cl     = X + Y
total_custom = 1.60 * X + 0.949 * Y     (using +60% and -5.1%)
break-even => 1.60 X + 0.949 Y = X + Y
            => 0.60 X = 0.051 Y
            => Y / X = 11.76
            => Y / (X + Y) = 92.2%
```

So V99 nets-positive only when 92.2% of the agent's context window is
Redcon output. In a normal coding agent session (system prompt + task
+ codebase + diff + test results) Y/(X+Y) is closer to 0.20-0.50,
which under V99 would inflate total tokens by approximately
`(1 - 0.30) * 0.60 - 0.30 * 0.051 = 0.42 - 0.015 = +40.5%`. A net
LOSS of 40% on the agent is the actual operational impact if V99 ever
shipped naively.

## Appendix: longest learned tokens (evidence of overfitting)

Top 10 longest entries in `redcon_bpe.json` vocab:

```
id 4722  ' TokenEstimatorDescribeCallable'
id 4480  ' LocalFileSummaryCacheBackend'
id 4487  ' AgentRunDatasetBuilderConfig'
id 4503  '(LocalFileSummaryCacheBackend'
id 4725  'ResolvedBuiltinTokenEstimator'
id 4371  ' ContextDatasetBuilderConfig'
id 4731  ' CompressionStrategyDefaults'
id 4373  'ocalFileSummaryCacheBackend'
id 4686  ' BudgetPolicyViolationError'
id 4733  ' HistoricalScoreAdjustment'
```

These are Redcon's own class names. The trainer carved a single token
out of each because the corpus included `grep -rn 'def '` over the
Redcon repo, which dumped these names dozens of times. A user running
Redcon on their own codebase would receive zero benefit from these
merges and the storage overhead in the vocab would crowd out merges
useful to them.

## Conclusion

V99 is a useful theoretical bound but not a deployable change. The
empirical ceiling for tokenizer-only compression of Redcon outputs is
about -5% vs cl100k, and that ceiling is only reachable with a
backend the project does not control. The -5% is also the same margin
that V31 (multi-token-string substitution) targets via cl100k-aware
string rewrites, no backend change required. V31 and V32 are the
operational version of this idea; V99 quantifies how much they can
ever deliver and is the kill-switch threshold: if a future tokenizer-
rewrite vector is delivering >5% on the same fixtures, double-check
the measurement; if it's delivering <1% the well is dry and effort
should move to V41-V50 (cross-call dedup) or V47 (snapshot delta)
where wins compound across calls instead of competing with cl100k's
already-good merges within a single call.

Throwaway scripts: `/tmp/v99_collect_corpus.py`,
`/tmp/v99_collect_redcon_self.py`, `/tmp/v99_grow_corpus.py`,
`/tmp/v99_train_bpe.py`, `/tmp/v99_train_bpe_v2.py`,
`/tmp/v99_final_eval.py`. Artifacts: `/tmp/v99_corpus/`. All numbers
in this note are reproducible by re-running those scripts in order
against this repo.
