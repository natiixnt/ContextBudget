# V10: Information-bottleneck objective for compressor parameter tuning per command

## Hypothesis

Today every Redcon compressor carries a small handful of magic numbers - `items[:3]` matches per file in grep, `failures[:6]` body lines in pytest verbose, `clip(msg, 200)` for failure summaries, `file_limit = 30` for lint, `containers[:30]` for docker, top-8 paths in git diff ULTRA, snippet `[:8]` in pytest, head/tail counts in log-pointer mode. They were picked by hand on a few fixtures. Treat each compressor as a parameterised lossy channel q_theta(Z|X) and pick theta by minimising the Information Bottleneck Lagrangian L(theta) = I(X;Z) - beta * I(Z;T), where T is a deterministic relevance proxy derived from the must-preserve regex set (already shipped) plus the list of paths/IDs the compressor's parser already extracts. The claim: a single offline run over the existing benchmark fixtures emits a static `tuning.toml` of (head_count, tail_count, clip_limit, group_top_k) constants per compressor that cuts ~3-6 absolute pp on COMPACT-tier reduction at fixed must-preserve pass rate, with zero hot-path cost (no neural at runtime).

## Theoretical basis

Tishby, Pereira, Bialek (2000) "The Information Bottleneck Method" arXiv:physics/0004057.
Given joint p(X, T), find a stochastic map p(Z|X) minimising

    L_IB(p(Z|X)) = I(X; Z) - beta * I(Z; T)

The minimiser interpolates between the trivial solution Z = const (0 I(X;Z), 0 I(Z;T)) and the identity Z = X (max I(X;Z), max I(Z;T)). The slope of the deterministic-IB curve at the operating point is exactly 1/beta.

We adapt this to discrete deterministic compressors with a finite parameter vector theta = (theta_1, ..., theta_k) (head counts, clip widths, group top-k). The compressor is a function f_theta : X -> Z, so I(X;Z) collapses to H(Z) (deterministic channel); we approximate H(Z) by E[token_count(Z)] up to a constant since Z is text and the token count upper-bounds H(Z) on a fixed tokenizer. T is a derived random variable on the same input X via a deterministic extractor g : X -> T - the bag of (must-preserve hits, parsed paths/test-names/error-codes) produced by the compressor's existing parser. Because both f_theta and g are deterministic, I(Z; T) reduces to the indicator that g(X) is recoverable from Z, and we replace it with a deterministic surrogate

    R_recall(theta) = (1/N) sum_i  |{t in g(x_i) : t in z_i(theta)}| / |g(x_i)|

Back-of-envelope. For pytest at COMPACT, current params are head=top-3 message lines (effectively first_meaningful_line), clip=200 chars, snippet=8. On the M8 benchmark fixtures (12 files, 30 failures synthetic), denote token_count(z) ~ a + b * head_lines + c * clip * log(N_failures). Empirically clip=200 already loses no test names (g(x) preserved) but consumes ~80 tokens/failure. Target Lagrangian:

    L(clip, head) = (a + b*head + c*log(N)*clip) - beta * R_recall(clip, head)

With beta tuned to match the COMPACT 0.30 reduction floor, dL/d(clip) = 0 yields

    clip* = (beta / c log N) * dR_recall/d(clip)

Since R_recall is monotone non-decreasing and saturates (every failure name is already in `f.name`, not `f.message`), the marginal gain of clip beyond the first meaningful line is near-zero. Gradient at clip=200 is empirically ~0; the saddle sits at clip ~ 60-80. Same logic for grep `items[:3]` and lint `file_limit = 30`. This is the lever IB formalises.

## Concrete proposal for Redcon

New offline tool:
- `redcon/cmd/tuning.py` (~300 LOC) - builds the IB objective and runs grid+local search over a discrete parameter grid per compressor.
- `redcon/cmd/tuning_config.toml` - emitted artefact, hand-reviewed before merge.
- Each compressor exposes a `Params` dataclass (e.g. `PytestParams(clip=200, head_lines=1, snippet=8)`); existing constants become attribute reads. A module-level `PARAMS = PytestParams.load_from(tuning_config_or_default)` is loaded once at import time. No per-call config plumbing.

Files touched:
- `redcon/cmd/compressors/{git_diff,grep_compressor,pytest_compressor,test_format,lint_compressor,docker_compressor,listing_compressor}.py`: replace literals with `PARAMS.foo`. Default values preserved if config file missing - byte-identical behaviour for users who don't run tuning.
- `tests/test_cmd_quality.py::CASES`: re-used as the IB training corpus.
- `redcon/cmd/quality.py`: unchanged (must-preserve regex set IS the surrogate for T).

Search procedure (offline only):

```python
def tune(compressor, fixtures, beta_grid=(0.5, 1, 2, 4, 8)):
    grid = compressor.params_grid()  # discrete: e.g. clip in {40,60,80,120,200}
    best = None
    for theta in grid:
        comp = compressor.with_params(theta)
        tot_tokens = 0
        recall = 0.0
        determinism_ok = True
        must_preserve_ok = True
        for fixture in fixtures:
            z1 = comp.compress(fixture.raw, fixture.argv).text
            z2 = comp.compress(fixture.raw, fixture.argv).text
            determinism_ok &= (z1 == z2)
            tot_tokens += estimate_tokens(z1)
            facts = compressor.extract_facts(fixture.raw)
            recall += sum(1 for t in facts if t in z1) / max(1, len(facts))
            must_preserve_ok &= verify_must_preserve(z1, compressor.must_preserve_patterns, fixture.raw_text)
        if not (determinism_ok and must_preserve_ok):
            continue
        for beta in beta_grid:
            L = tot_tokens - beta * recall
            best = min((L, theta, beta), best, key=lambda x: x[0]) if best else (L, theta, beta)
    return best
```

Output artefact format (toml, hand-mergeable):

```toml
[pytest]
clip_chars = 80          # was 200
head_message_lines = 1   # was 1
verbose_snippet_lines = 6 # was 8

[grep]
matches_per_file = 2     # was 3
text_clip = 160          # was 200

[git_diff.ultra]
top_paths = 6            # was 8
```

Runtime path: parameters are read from a frozen module-level dataclass (no I/O on hot path). Cache key includes the tuning_config digest so a config bump invalidates stale entries deterministically.

## Estimated impact

- Token reduction (COMPACT tier, on M8 fixture corpus):
  - pytest: +3-5 pp (clip 200 -> ~80 saves ~12 tokens per failure x 30 failures / total compact tokens ~ 600 -> ~5pp).
  - grep: +2-4 pp (drop one match per file from 3 -> 2 when match texts are long; gated by recall on path set, which is invariant).
  - git_diff: +1-2 pp at ULTRA (8 -> 6 paths); negligible at COMPACT.
  - lint: +1-2 pp (file_limit 30 -> tuned per fixture distribution).
  - docker, listings: marginal (header-dominated outputs).
  - Aggregate cross-compressor: +2-3 pp average; not by itself a >=5pp breakthrough on a single compressor, but compounds on top of every existing compressor.
- Latency: zero cold-start delta (constants are constants). Tuning run itself is ~10 seconds on the existing benchmark corpus, run once in CI.
- Affects: every compressor module (constants -> dataclass fields), `quality.py` (no logic change, inputs to thresholds may shift), cache key (config digest added).

## Implementation cost

- ~300 LOC for `tuning.py` + ~50 LOC of dataclass scaffolding spread across compressors + ~30 LOC test for the optimiser itself. ~400 LOC total.
- New runtime deps: none. tomllib (stdlib 3.11+) for config load. No neural, no network.
- Risks to determinism: low. Tuning is offline; runtime reads frozen config; same config produces same output. Cache key must include config-file SHA256 to avoid cross-version cache poisoning (one-line addition in `pipeline.py`).
- Risks to must-preserve: search rejects any theta that fails must-preserve on any fixture, so the floor is enforced by construction.
- Risk: corpus-overfit. Fixtures are synthetic plus a few real ones; tuned constants might overfit to those distributions. Mitigated by enforcing must-preserve invariants (the contract), not just average recall.

## Disqualifiers / why this might be wrong

1. Already partially implemented in disguise. `must_preserve_patterns` plus the COMPACT 30% reduction floor already form a constrained-optimisation contract; the harness rejects regressions. What's missing is the *search* that would find slack. So the novelty is "automate the constant-tuning step" not the framework.
2. The deterministic surrogate for I(Z;T) collapses to plain recall (because both f_theta and the fact extractor g are deterministic). Once you've made that collapse, this is just "grid-search compressor params under a recall-and-tokens objective." Calling it Information Bottleneck is dressing - the IB literature is about stochastic Z, neural mutual-info estimators, or analytic Gaussian/discrete chains. Honest framing: this is constrained discrete optimisation, with the IB Lagrangian as the loss shape, no more.
3. Gain is small per compressor. The constants were not picked by ML - several are already at recall-saturation knees (pytest clip 200 vs first_meaningful_line). Real compact-tier gains are bounded by what the parser preserves, not what the formatter clips.
4. Per-fixture overfit. The IB tuning may pick `clip=60` because the M8 pytest fixture has short messages; a real-world long-message run regresses must-preserve via the verify step but if the regex pattern is just the test name, the harness misses semantic loss inside the message body.
5. Surrogate gap. R_recall over parsed facts captures path/test-name preservation but not "did the agent succeed at the task", which is the only T that actually matters for the IB formulation. Without an agent-success signal we are tuning a proxy at best (this is V97's territory: active-learning loop on agent feedback).

## Honesty check on the no-neural rule

The classical IB algorithm (Tishby's original Blahut-Arimoto-style update) is parameter-free over discrete alphabets and runs CPU-only with no model. Neural-IB (Tishby/Schwartz-Ziv 2017, MINE estimators, VIB) is what introduces models. This proposal stays on the classical side: deterministic compressors, deterministic fact extractor, grid-search Lagrangian. The output is a static TOML config consumed at module import time. Runtime hot path is byte-identical to today's modulo the read of frozen constants. Acceptable per BASELINE constraint #3.

## Verdict

- Novelty: low (mostly: this is a principled name for a search procedure that the project should arguably already run; the IB formalism gives a defensible objective shape but the math collapses to constrained recall-vs-tokens search).
- Feasibility: high (stays inside existing harness, no new deps, clean offline-only workflow).
- Estimated speed of prototype: 1-2 days for tuning.py + dataclass scaffolding on 2-3 high-leverage compressors (pytest, grep, lint) and a short PR showing measured pp deltas.
- Recommend prototype: conditional-on-X, where X = "we already plan to add a benchmarks-as-config-driver step". As a standalone vector it is a minor tightening; folded into a broader configurable-params refactor (also enables V01 rate-distortion operating points and V87 Pareto curves) it pays for itself.
