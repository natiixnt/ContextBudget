# V88: Self-supervised re-feed eval - "anything missing?" probe

## Hypothesis

Compressor authors today rely on `redcon/cmd/quality.py`'s regex `must_preserve_patterns` plus determinism / robustness checks to gate releases. Those tests answer the question "did the bytes I told you to keep survive?" but not the more interesting question "would an agent who only sees this compressed string be able to act on it?". The proposal: build an **offline evaluator** that takes (raw_output, task_description, compressed_output) triples from a small fixture corpus, hands them to a model, and asks one prompt - "list every fact the agent would need that is not in the compressed view, and rank by severity". The complaints (deduped, frequency-weighted) become the seed for new must-preserve-pattern proposals and ULTRA-tier guardrails.

The claim is not that this changes runtime behaviour. BASELINE constraint #3 ("no embedding models in the scoring/compression hot path") and the broader "no model calls" framing forbid putting an LLM in the request path. The claim is that the *authoring loop* for compressors is currently informal ("did I think of every regex?") and an LLM-as-judge eval, run as a CI gate or release-time check, surfaces gaps the human author missed before they ship. Concretely: I expect a ~2-day prototype on 5 fixtures (the existing quality fixtures used by `tests/test_cmd_quality.py`) to surface 3-7 distinct missing-fact classes that are not currently covered by `must_preserve_patterns` across the 11 shipped compressors, of which 1-3 are real (i.e. would pass the "if I were the agent and lacked this, I'd be stuck" sniff test) and worth promoting to either a new pattern or a tier-floor adjustment.

## Theoretical basis

### 1. LLM-as-judge as a sufficient-information probe

Frame the compressor as a channel `C: X -> Y` where X is the raw subprocess output and Y is the compressed string. The agent's downstream task `T: Y -> A` produces an action A. The compressor is *task-sufficient* iff for every relevant task T:

    P(A_correct | Y, task) >= P(A_correct | X, task) - epsilon

This is the standard sufficient-statistic / minimal-sufficient definition (Lehmann-Scheffe in statistics; in information theory: `I(X; A_correct | task) - I(Y; A_correct | task) <= delta`). A *judge model* J approximates the sufficiency check by reading (Y, task) and producing a complaint set:

    J(Y, task) = { fact_i in X : fact_i needed_for(T) and fact_i not in Y }

If J's recall over the true missing-fact set is bounded below by some r, then `union over task in tasks` of J's complaints covers an r-fraction of the channel's task-sufficiency violations. r need not be 1 for the probe to be useful as a regression-test seeder: even r ~ 0.3 finds 30% of latent gaps that the human author missed, which is a strict improvement over the current loop (regex-author-imagines-what-matters).

### 2. Why this is *not* embeddings or a hot-path model

The constraint distinction is about runtime: the compressor at request time must remain pure-deterministic and model-free. An *offline batch evaluator* run by the compressor author on a fixture corpus, producing a list of suggested regex additions, is the same kind of CI tooling category as `mypy`, `pytest`, or a linter that calls a remote service. It does not violate determinism of the shipped compressor; the shipped compressor still has fixed regexes. The judge's output is an *artifact for the human*, not a runtime decision.

### 3. Convergence as a regression-test generator

Run J on N fixtures with K distinct task framings each (e.g. "fix the failing test", "summarise the diff", "find the line that broke the build"). The complaints stream is a multiset of (compressor_name, task_kind, missing_fact). Bin by compressor and take the top-frequency complaints; promote each to a candidate `must_preserve_pattern`. The author manually accepts/rejects (since J has false positives), and the accepted ones are merged into the compressor.

The number of distinct genuine complaints is bounded above by the *true* count of task-sufficiency violations in the current compressor set; in steady state, repeated runs find no new complaints, which is a useful "saturation" signal that the must-preserve set is approximately complete. With N=5 fixtures, K=3 task framings each, 11 compressors, that's 165 judge calls per run - cheap enough to gate on a release branch (~$1-3 per gate at current Claude / GPT-4-class rates) but not in CI for every PR.

### 4. Expected hit rate (back of envelope)

Today's `must_preserve_patterns` is a tuple of regex literals declared at module top in each compressor. For pytest_compressor, it's roughly { failure name, failure file:line, AssertionError text }. For grep_compressor: { matching path, line number }. For git_diff: { changed file path, +/- counts }. These are all "obvious to the author" facts.

Less obvious facts that might surface:
- pytest: the *python version*, the *test parameters* on parametrized tests (e.g. `test_foo[case-3]` vs just `test_foo`), the *xfail/xpass* state.
- grep: the *line count per file* (current dedup loses it), surrounding context lines on the first match, the *file extension distribution* (when the agent is asking "is this a Python or Rust problem?").
- git_diff: the *binary-file* indicator (detected but maybe not surfaced at ULTRA), the *file-mode change* (chmod), the *symlink target* change.
- find: the *mtime distribution* (when the agent asks "what's recently modified?"), the *file size sum*.
- docker: the *image digest* (vs just tag), the *layer cache hit ratio*.

If the judge surfaces even half of these per compressor and 30% of those survive author review, that's ~1-2 new must-preserve patterns per compressor over a single eval pass. Across 11 compressors: ~15 net adds, which is meaningful but is *quality* improvement, not *reduction* improvement. (The technique cannot improve token reduction at all - if anything it *increases* output by forcing more facts to survive. The win is in correctness-per-token, not tokens-per-output.)

### 5. Coding cost vs alternatives

Compare with V81 (Hypothesis property-based fuzzing) and V85 (adversarial input generator). Property-based fuzzing checks that *invariants the author already wrote* hold across input perturbations - it cannot find missing invariants. V85's adversarial generator can find inputs that break existing invariants but still does not surface what *new* invariants ought to exist. V88 specifically targets the "what should we be preserving that we aren't?" question, which neither V81 nor V85 answers. V83 (KL divergence between line distributions before/after) is a quantitative drift signal but is not aware of *task* - it would flag a legitimate ULTRA-tier collapse as drift.

So V88's niche is narrow but distinct. It is the only proposal in the index that asks the question "is the must-preserve set complete?" directly.

## Concrete proposal for Redcon

### Files added (offline-only, no production source touched)

- **`redcon/eval/refeed_judge.py`** (~120 LOC, new): the judge runner. Reads fixtures, calls J, parses complaints into a typed list, emits a Markdown report.
- **`redcon/eval/fixtures/`** (new dir): 5 starter fixtures, each a directory with `raw.txt` (subprocess output), `task.txt` (one-paragraph task description), `compressed_compact.txt` and `compressed_ultra.txt` (output of running the existing pipeline at the two tiers, generated and committed - so the judge sees what an agent today sees). The five fixtures span: pytest with 3 failures, git diff of 5 files, grep across 80 paths, find on a 200-file tree, docker build log with one error layer.
- **`redcon/eval/__init__.py`** (empty marker).
- **`redcon/eval/judge_prompt.txt`** (the actual prompt template - kept as text so it diffs cleanly).
- **`redcon/eval/snapshots/`** (judge output snapshots, committed - so re-runs with the same judge model are diffable; flagged as "may change when we bump the judge model").
- **CI integration** as a *manual* GitHub Actions workflow (`.github/workflows/refeed_eval.yml`), opt-in via `workflow_dispatch`. Not on every PR. Author kicks it off pre-release.

### Sketch (refeed_judge.py)

```python
JUDGE_MODEL = os.environ.get("REDCON_JUDGE_MODEL", "claude-haiku-4-5")
JUDGE_PROMPT = (Path(__file__).parent / "judge_prompt.txt").read_text()

def run_one(fixture_dir: Path, tier: str) -> list[Complaint]:
    raw = (fixture_dir / "raw.txt").read_text()
    task = (fixture_dir / "task.txt").read_text()
    compressed = (fixture_dir / f"compressed_{tier}.txt").read_text()
    schema = (fixture_dir / "schema.txt").read_text().strip()  # e.g. "pytest"
    prompt = JUDGE_PROMPT.format(raw=raw, task=task, compressed=compressed, schema=schema)
    resp = call_judge(prompt, model=JUDGE_MODEL, temperature=0.0, seed=0)
    return parse_complaints(resp)

def main():
    out: dict[tuple[str, str], list[Complaint]] = {}
    for fx in sorted((Path(__file__).parent / "fixtures").iterdir()):
        if not fx.is_dir(): continue
        for tier in ("compact", "ultra"):
            out[(fx.name, tier)] = run_one(fx, tier)
    write_report(out, Path("redcon/eval/snapshots/latest.md"))
    write_proposal(out, Path("redcon/eval/snapshots/proposed_patterns.md"))
```

### Judge prompt (text)

```
You are auditing a deterministic compressor used by an AI coding agent.

Schema: {schema}
Compression tier: {tier}
Agent's task (verbatim, what the agent was trying to accomplish):
---
{task}
---

Raw subprocess output (what the compressor saw):
---
{raw}
---

Compressed output (what the agent will see):
---
{compressed}
---

List every fact present in the raw output that the agent would plausibly need
to act on this task and that is missing or unrecoverable from the compressed
output. For each:
  - quote the missing fact (verbatim from raw)
  - rank severity: BLOCKING / IMPORTANT / NICE_TO_HAVE
  - one-sentence justification

Output JSON: {"complaints": [{"fact": "...", "severity": "...", "why": "..."}]}.
If nothing is missing, return {"complaints": []}.
Do not invent facts. Do not complain about cosmetic issues.
```

Determinism: judge call uses `temperature=0.0` and a fixed seed where the SDK supports it. We should not pretend the judge output is byte-identical across model versions, so the snapshot file is committed but flagged as "regenerate on JUDGE_MODEL bump". A non-zero diff in `snapshots/latest.md` is informative, not a hard CI failure.

### Promotion path

Each accepted complaint becomes a `must_preserve_patterns` candidate. The author reviews `snapshots/proposed_patterns.md`, picks the real ones, and adds them to the relevant compressor's tuple. The eval is then re-run; the previous complaints should disappear, exposing the next layer. The loop terminates when one full pass yields zero BLOCKING complaints across all fixtures.

### Why this is *not* in production source

Per the task brief and per BASELINE constraint #2 ("no required network"), this entire module sits under `redcon/eval/` and is never imported from `redcon/cmd/` or `redcon/scorers/`. It is a developer tool, like `redcon/cmd/benchmark.py` is today (offline benchmark, not a runtime feature).

## Estimated impact

- **Token reduction**: zero, possibly slightly negative (new patterns -> more facts retained -> higher floor on compact tier output). The honest framing: this trades 1-3% reduction for measurable correctness improvement on edge tasks.
- **Compressor correctness on out-of-author-imagination tasks**: the metric is hard to quantify without the eval itself, but anecdotally, every shipped compressor has had at least one post-hoc bug where the author missed a fact (e.g. the 2026-Q1 grep_compressor change to surface `match_count_per_file` was driven by a real agent stumbling on it). V88 systematizes that discovery.
- **Latency**: zero on hot path. Eval run takes ~2-5 minutes wallclock on 5 fixtures x 2 tiers x 11 compressors-when-fully-fixtured = ~110 judge calls. Cost ~$0.50-3 per pass at Haiku-class rates.
- **Affects**: only `redcon/eval/` (new). No changes to `pipeline.py`, no changes to `quality.py`, no cache impact, no determinism risk on the production binary.

## Implementation cost

- **Lines of code**: ~150 in `refeed_judge.py` + parser + report writer. ~50 in fixture scaffolding (one fixture is essentially `raw.txt` + `task.txt` + a one-line schema marker + the two compressed views, the latter generated by `redcon run --pin-floor=compact|ultra`). ~30 in the GitHub Actions workflow.
- **New runtime deps**: `anthropic` Python SDK (or equivalent OpenAI SDK depending on judge choice) - **but only as a dev/eval dep**, not a runtime dep of the compressors. Goes in `pyproject.toml` `[project.optional-dependencies] eval`. No impact on cold-start.
- **Risks to determinism**: only on the judge artifact, not on shipped behaviour. Mitigation: snapshot file is treated as advisory, not as a hard CI gate.
- **Risks to must-preserve guarantees**: zero on the existing set; only adds candidates that the author manually accepts.
- **Network**: yes, the eval makes an outbound HTTPS call. This is a constraint break **for the eval tool**, but the BASELINE constraint #2 is "no required network" for the *product*. A dev tool that calls a judge model is in the same tolerance bucket as `pre-commit autoupdate` calling GitHub.

## Disqualifiers / why this might be wrong

1. **It's just LLM-as-judge, which is well-known to be noisy.** Judge model false positives are a real failure mode: it might say "the compressed output is missing the timezone of the timestamps" when the agent doesn't care. The proposal mitigates with the human-review step (author accepts/rejects each candidate) but at scale this becomes a bottleneck. If the false-positive rate on first runs is >50%, the tool is more annoying than useful and gets ignored, which is the same fate as any unreliable linter.

2. **Recall is unmeasured.** I claimed the judge has *some* recall over true task-sufficiency violations, but I have no way to measure this without ground truth. Building ground truth is the same problem as solving the eval itself - circular. The honest answer is "we know LLM judges find *some* gaps, we don't know how many they miss". This caps the technique's claim to "useful seeder of ideas", not "complete sufficient-statistic checker".

3. **Already implemented in disguise.** The existing `redcon/cmd/quality.py` plus `tests/test_cmd_quality.py` plus the `must_preserve_patterns` mechanism is a regex-based version of this idea. V88 is "swap the regex author for a model author". Whether that is a meaningful change depends on whether the model finds gaps the regex authors missed. If the compressor regex sets are already approximately complete (which 11 mature compressors might be), V88 finds nothing and is a wash.

4. **Task-conditioning is fragile.** The complaints depend heavily on the framing of `task.txt`. A vague task ("understand this output") elicits vague complaints; a sharp task ("which specific test method did the regression introduce") elicits sharp complaints. Composing a representative task corpus is its own research effort, and getting it wrong silently biases the entire eval. This is the same problem as building a benchmark suite for any LLM eval, and it's not a solved problem.

5. **Saturation is illusory.** The "loop terminates when zero BLOCKING complaints" criterion is judge-model-dependent. A bigger judge tomorrow finds new BLOCKING gaps. So saturation is a function of (current model, current fixtures), not of (true compressor sufficiency). Pretending otherwise misleads the compressor author.

6. **Ranks "missing fact" the wrong way for token-budget thinking.** A judge model trained on web corpora has no concept of "this fact costs 30 tokens to retain and the agent only needs it 2% of the time". It will demand exhaustive retention. The compressor author has to push back against the judge, which is the opposite of trust - and if the author always pushes back, why run the eval. Mitigation: bake "weighted by task frequency" into the prompt, but that requires task-frequency data the project does not have.

7. **Not novel as a CS technique.** "LLM as judge" / "model-graded eval" is the literal default for OpenAI Evals, Anthropic's evaluation tooling, lm-eval-harness with a judge model, and every model-card report from 2024 onward. Applying it to compressor authoring is straightforward; the only question is whether anyone ships the harness for Redcon specifically. Verdict on novelty must be honest: low.

## Verdict

- **Novelty: low**. LLM-as-judge eval is a standard technique. The Redcon-specific framing is "judge the must-preserve completeness rather than judge a generation", which is mildly novel as a framing but not as a method. BASELINE does not list "model-graded eval of compressor sufficiency" as a frontier item, but that's because it is conceptually a developer tool, not a product feature - the BASELINE frontier list is about runtime improvements.
- **Feasibility: high**, with the explicit caveat that it is a *developer tool*, not a product feature. Implementing the harness is ~2-3 days. Running it on 5 fixtures is one wallclock-afternoon. Producing actionable patches from the output is the unbounded-time part (depends on how many real gaps exist).
- **Estimated speed of prototype: 2-3 days** for the harness + 5 fixtures. Triage of judge output and conversion to merged compressor patches: ~1 day per compressor that has gaps, so 1-2 weeks of follow-up if the eval is productive.
- **Recommend prototype: conditional**. Recommend yes if (a) someone is already paying for a judge model in this org and the marginal cost of $1-3/pass is not a concern, and (b) at least one compressor has a known correctness complaint from a real agent run that the existing regex tests did not catch (which provides at least one ground-truth example to validate the judge's recall against). Recommend no if (a) the team's bandwidth would be better spent on the runtime-impacting frontier items in BASELINE (V47 snapshot deltas, V41 session aliases, V36 cross-tokenizer rosetta), since this is *quality at the cost of slight reduction*, not *reduction or speed*.

This is a useful tool, not a breakthrough. It belongs in `redcon/eval/`, run pre-release, with output reviewed by a human and converted to ordinary `must_preserve_patterns` entries. It does not move the BASELINE breakthrough bar (>=5pp compact reduction or >=20% cold-start cut). It does plausibly catch some latent quality bugs before they ship, which has value but a different unit.
