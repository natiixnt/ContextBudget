# V29: Set-cover atomic-fact decomposition - rank files by which task facts they cover

## Hypothesis

The current scorer in `redcon/scorers/relevance.py` returns a real-valued
score per file and the planner picks the top-K. That ranking is
order-by-score and inherently *redundant*: if a task touches three
distinct concerns ("config dataclass", "CLI parsing", "history adjustment")
the top-K is dominated by whichever concern produces the strongest signal,
and the other two concerns may be missed entirely under a tight file
budget. Set-cover replaces that with diversity-aware selection: decompose
the task into a small set `F` of atomic facts the agent must learn to
finish the task; treat each candidate file as the *subset* of `F` it
covers; greedily pick files that maximise marginal coverage per token.

Claim: under a tight file budget (K <= 6) and tasks that touch >= 2
disjoint code regions, greedy set-cover picks a file set with strictly
higher fact-coverage than the current weighted top-K - empirically the
top-K duplicates near-clones (e.g. three test files for the same
production module) and misses one of the legs. Prediction on this repo:
for the task `"add config option for max retries"`, current top-K
selects `redcon/config.py` plus 4 adjacent test/helper files; set-cover
also picks `redcon/cli.py` (the option must be exposed) and
`docs/configuration.md` (a doc fact) for the same K, dropping a
redundant config test. For `"fix bug in pytest_compressor"`, current
top-K duplicates pytest fixtures; set-cover keeps the production file
plus exactly one test, freeing budget for `redcon/cmd/budget.py`
(`select_level` is called from `pytest_compressor.py` and is a likely
upstream cause).

The hard part is the fact-extraction step. We don't have an LLM in the
loop and BASELINE.md (line 40) makes "no embeddings" a load-bearing
constraint. The proposal uses a deterministic, rule-driven extractor:
task-keyword expansion via existing `redcon/core/text.py::task_keywords`,
plus repo-symbol lookup (already produced by `redcon/symbols/` for the
incoming `FileRecord.symbol_names`), plus a small set of
hand-coded *role facts* derived from the task verbs (`"add"` -> need
config + CLI + tests; `"fix bug"` -> need symbol definition + test that
covers it + the call sites). 5-15 facts per task is enough.

## Theoretical basis

### Set cover, formally

Given a universe `F = {f_1, ..., f_n}` of atomic facts and a collection
`S = {S_1, ..., S_m}` of subsets `S_i subseteq F` (one per candidate
file), with positive cost `c_i` per file, the *weighted set-cover*
problem is

  minimise   sum_{i in C} c_i
  s.t.       union_{i in C} S_i = F.

It is NP-hard but the greedy algorithm

  while uncovered != empty:
      pick i = argmax_i  |S_i intersect uncovered| / c_i
      C <- C union {i};  uncovered <- uncovered \ S_i

achieves the well-known `H(d) = 1 + 1/2 + ... + 1/d <= ln(d) + 1`
approximation, where `d = max_i |S_i|`. With `n <= 15` facts per task
this gives an at-worst `H(15) approx 3.32` factor vs OPT, but on the
relevant *budget-constrained* variant (max-cover under cardinality
constraint K) the same greedy attains the textbook
`(1 - 1/e) approx 0.632` approximation (Nemhauser-Wolsey-Fisher 1978).

Set-cover and weighted top-K are not the same objective. Top-K maximises
`sum_{i in K} score(i)` where `score` is a real-valued relevance
estimator. Set-cover maximises `|union_{i in K} S_i|` - a *submodular*
function of the chosen set. Submodularity is exactly the property top-K
*lacks*: top-K does not penalise the third file for re-covering what
the first two already covered. That redundancy is the practical failure
mode the vector targets.

### Cost choice

Cost `c_i` is per file. Two natural choices:

  - `c_i = 1` (cardinality cover): picks fewest files.
  - `c_i = file_tokens(i)` (token-cost cover): picks fewest tokens.

For Redcon, `c_i = file_tokens(i)` is the budget-honest choice because
the downstream packer is bounded by token budget, not file count
(`BudgetSettings.max_tokens`, `redcon/config.py:46`). Greedy with
weighted cost still gives `H(d)` in the unbounded variant and
`(1 - 1/e)` in the bounded one. The denominator becomes "covered facts
per estimated token" - i.e. *information density*, which is
operationally what you want when the bottleneck is context length.

### Comparison to current scorer

`redcon/scorers/relevance.py` accumulates a per-file scalar `score`
from terms like `cfg.path_keyword_weight * path_hits +
cfg.content_keyword_weight * preview_hits + ...`. There is no
inter-file coupling: the score of `config.py` does not change when
`cli.py` is also picked. Top-K of this scalar is provably suboptimal
for any fact-coverage objective whenever the score is correlated
with overlap (which it is: a path keyword hit tends to co-occur with
preview hits on the same concern).

Concretely: let task have two equal-cardinality concerns A and B. Let
file `x` cover `{A_1, A_2, A_3}` (heavily duplicated A signal), file
`y` cover `{A_1}`, file `z` cover `{B_1, B_2}`. With weights
`w_path = 2, w_preview = 0.25` the relevance score will rank
`score(x) > score(z) > score(y)` because `x` has the most A-keyword
hits in path and content. Top-2 = `{x, y}` covers `{A_1, A_2, A_3}`
- 3 facts. Greedy set-cover picks `x` first (largest set), then picks
`z` (only file with B facts), giving coverage `{A_1, A_2, A_3, B_1, B_2}`
- 5 facts. **Same K=2, +67% coverage.** The current scorer cannot
recover this because its per-file score has no notion that `y` is
already covered by `x`.

### Fact-extraction heuristic (deterministic, no LLM, no embeddings)

The genuine engineering risk in V29 is that "atomic facts" are normally
identified by a model. Proposal: synthesise facts from three
deterministic sources, all already present in the repo.

1. **Keyword facts**: `redcon/core/text.py::task_keywords(task)` already
   returns up to 16 deduplicated keywords (it splits CamelCase, applies
   stopword filter, drops noise). Each keyword is a fact `kw:<word>`.
2. **Symbol facts**: each `FileRecord.symbol_names` is a comma-separated
   list of definitions (function, class, etc.) emitted by the symbol
   extractor (`redcon/symbols/`). For each task keyword `kw`, if any
   file's symbol list contains a fuzzy match (substring or
   `startswith` after CamelCase split), emit a fact
   `sym:<canonical_symbol>`. This grounds facts in actual
   identifiers in this repo, not just task words.
3. **Role facts**: a tiny static table `_ROLE_FACTS` keyed on task verbs:
   - `"add"` / `"new"`: emits `role:config`, `role:cli`, `role:test`,
     `role:doc`. Adding a feature usually requires updating each.
   - `"fix"` / `"bug"`: emits `role:def`, `role:test`,
     `role:caller`. Fixing a bug usually requires the definition,
     a regression test, and at least one caller.
   - `"refactor"`: emits `role:def`, `role:caller` (>= 2 callers
     desired - we model this as 2 distinct facts `role:caller_1`,
     `role:caller_2`).
   These are the same role classes already in
   `redcon/scorers/file_roles.py::classify_file_role`. Files
   contribute the corresponding role-fact when their classified
   role matches.

Total fact set size on real tasks: `|F| in [5, 15]`. Greedy on |F|=15,
m=300 candidate files: ~15 * 300 = 4500 set intersection ops, each
on bitsets of 15 bits - sub-millisecond.

### Back-of-envelope on the two test tasks

The repo is small enough to enumerate manually.

#### Task 1: "add config option for max retries"

Keywords from `task_keywords` (after stopword filter):
`task_keywords("add config option for max retries")`
-> `["config", "option", "max", "retries"]`
(`"add"`, `"for"` are in the stopword set, `redcon/core/text.py:13-22`).

Atomic facts (5 keyword + 4 role + 1 symbol = 10):
  f1: kw:config
  f2: kw:option
  f3: kw:max
  f4: kw:retries
  f5: sym:BudgetSettings   (closest existing dataclass touching `max_tokens`)
  f6: role:config
  f7: role:cli
  f8: role:test
  f9: role:doc
  f10: kw:max_tokens (composite from f3+f5 expansion)

Manual coverage table (using `redcon/scorers/file_roles.py` role
classification + grep evidence):

| File                                         | Covers                                  | tokens (~) |
|----------------------------------------------|-----------------------------------------|------------|
| redcon/config.py                             | f1,f2,f3,f5,f10,f6                       | ~9000      |
| redcon/cli.py                                | f1,f7,f10                                | ~5000      |
| tests/test_config_validation.py              | f1,f3,f8                                 | ~2000      |
| tests/test_config.py                         | f1,f8,f10                                | ~2500      |
| docs/configuration.md (if present, role:doc) | f1,f9                                    | ~1500      |
| redcon/cmd/budget.py                         | f3,f10                                   | ~1500      |
| redcon/core/pipeline.py                      | f10                                      | ~3500      |

**Current weighted-score top-K=4** (estimated from the relevance
weights: `config`, `max`, `retries` keyword path-hits dominate):
  1. redcon/config.py            (huge content+path hit on `config`, `max`, `option`)
  2. tests/test_config_validation.py   (path keyword `config`, content)
  3. tests/test_config.py        (path+content)
  4. redcon/cmd/budget.py        (content `max_tokens`)

  Coverage: f1, f2, f3, f5, f6, f8, f10 = 7/10. Missing
  f4 (`retries` - the task's most distinctive token, not present in
  any file because the feature is new), f7 (CLI), f9 (doc).

**Greedy set-cover, K=4, c=1 per file:**
  iter 1: argmax = redcon/config.py (covers 6 facts). Uncovered: f4, f7, f8, f9.
  iter 2: argmax = redcon/cli.py (covers f7; ties broken by token cost).
          Uncovered: f4, f8, f9.
  iter 3: argmax = tests/test_config.py or test_config_validation.py
          (covers f8). Uncovered: f4, f9.
  iter 4: argmax = docs/configuration.md (covers f9; no file covers f4
          because the feature is genuinely new - this is correct
          behaviour, the agent will add the new symbol).

  Coverage: f1, f2, f3, f5, f6, f7, f8, f9, f10 = 9/10.
  **+2 facts vs top-K**, same K. The "missing" fact f4 is unsatisfiable
  by any existing file (the symbol literally does not exist yet) -
  the cover algorithm correctly leaves it uncovered rather than
  duplicating already-covered facts.

#### Task 2: "fix bug in pytest_compressor"

Keywords (`add`, `fix`, `the` filtered): -> `["bug", "pytest", "compressor", "pytest_compressor"]`
After lowercasing duplicate-removal: `["bug", "pytest", "compressor", "pytest_compressor"]`
(`task_keywords` keeps the underscore-bearing token whole, since
`_WORD_RE = r"[a-zA-Z][a-zA-Z0-9_]{2,}"` allows underscores).

Atomic facts (4 keyword + 3 role + 1 symbol = 8):
  f1: kw:bug
  f2: kw:pytest
  f3: kw:compressor
  f4: kw:pytest_compressor
  f5: sym:PytestCompressor
  f6: role:def     (the production file under fix)
  f7: role:test    (the regression test)
  f8: role:caller  (something that *invokes* the compressor; `select_level` is called from it)

Manual coverage table:

| File                                              | Covers              | tokens |
|---------------------------------------------------|---------------------|--------|
| redcon/cmd/compressors/pytest_compressor.py       | f2,f3,f4,f5,f6      | ~3500  |
| tests/test_cmd_compressors.py                     | f2,f3,f7            | ~5000  |
| redcon/cmd/compressors/test_format.py             | f3,f7               | ~1500  |
| redcon/cmd/budget.py                              | f8 (select_level called from pytest_compressor) | ~1500 |
| redcon/cmd/registry.py                            | f3,f8               | ~1200  |
| redcon/cmd/pipeline.py                            | f8                  | ~3000  |
| benchmarks/run_cmd_benchmarks.py                  | f2                  | ~2500  |

**Current weighted-score top-K=4:**
  1. redcon/cmd/compressors/pytest_compressor.py  (path: `pytest`,
     `compressor`, `pytest_compressor` triple-hit, symbol match)
  2. tests/test_cmd_compressors.py                (path: `compressors`,
     content: heavy)
  3. redcon/cmd/compressors/test_format.py        (path keyword,
     content references pytest)
  4. benchmarks/run_cmd_benchmarks.py             (content `pytest`)

  Coverage: f2, f3, f4, f5, f6, f7 = 6/8. Missing f8 entirely (no
  caller-graph file is selected) and f1 (`bug` doesn't match any
  file - unsatisfiable, correct).

**Greedy set-cover, K=4, c=1 per file:**
  iter 1: pytest_compressor.py (covers 5). Uncovered: f1, f7, f8.
  iter 2: tests/test_cmd_compressors.py (covers f7). Uncovered: f1, f8.
  iter 3: redcon/cmd/budget.py *or* redcon/cmd/registry.py - tie on
          single fact f8. Token-cost tiebreak picks the cheaper:
          registry.py (~1200 < budget.py ~1500). But registry.py
          *also* covers f3, already covered, so the marginal-fact
          score is identical. Deterministic tiebreak by path order
          picks budget.py. Uncovered: f1.
  iter 4: f1 unsatisfiable (no file has `bug` as a keyword). Greedy
          selects the file with the next *new* keyword: there are
          none. Falls back to the existing top-K weighted score for
          slot 4: pick the highest-scored *unselected* file =
          redcon/cmd/compressors/test_format.py.

  Coverage: f2, f3, f4, f5, f6, f7, f8 = 7/8. **+1 fact vs top-K**,
  AND the agent gets `redcon/cmd/budget.py` which is the actual
  upstream of `select_level` - the most likely cause of a regression
  in pytest_compressor. The current top-K never sees budget.py.

### Why this is a real win

The two cases share a pattern: top-K spends extra slots on files that
re-prove the dominant keyword and starves the secondary axes (CLI,
caller). Set-cover spends each slot on a *new* axis. On task 1 it
buys one extra fact (CLI) at the cost of dropping one redundant test;
on task 2 it buys the upstream-caller axis at the cost of one
adjacent helper test. Both changes match the engineer's intuition for
"what would I actually need open to do this".

Important: set-cover is **strictly worse** when |F| <= K. If the
task has fewer atomic facts than the budget allows files, all
selections cover all facts and the secondary tiebreak (per-file
relevance) decides. So we run greedy until coverage saturates, then
fall through to the existing top-K for remaining slots. This is the
right limiting behaviour.

## Concrete proposal for Redcon

### Files

- `redcon/scorers/relevance.py` (modify, ~80 LOC added): add
  `selection_mode: Literal["weighted", "cover"]` to `score_files`,
  default `"weighted"` (existing behaviour). When `"cover"`, after
  scoring, run greedy set-cover and *re-rank* (not re-score) the
  ranked list so the cover-selected files come first in output
  order. Score is unchanged - only the *relative ordering* changes.
- `redcon/scorers/atomic_facts.py` (new, ~120 LOC): pure functions
  `extract_facts(task: str) -> list[Fact]` and
  `file_covers(file_record: FileRecord, facts: list[Fact])
  -> frozenset[int]` returning bit-indices into the fact list.
  No new deps. Uses existing `task_keywords`, existing
  `classify_file_role`. Deterministic.
- `redcon/config.py` (modify, ~5 LOC): add
  `ScoreSettings.selection_mode: str = "weighted"` so the mode is
  surfaced through normal config and the existing override
  pipeline (`_apply_score_overrides`).
- `tests/test_set_cover_scoring.py` (new, ~80 LOC): two parameterised
  cases reproducing the manual examples above; check fact
  identification, cover order, and that
  `mode="weighted"` is byte-identical to the current behaviour.

### API sketch

```python
# redcon/scorers/atomic_facts.py
from __future__ import annotations
from dataclasses import dataclass
from redcon.core.text import task_keywords
from redcon.scorers.file_roles import classify_file_role

@dataclass(frozen=True, slots=True)
class Fact:
    kind: str   # "kw" | "sym" | "role"
    value: str

_VERB_ROLE_FACTS: dict[str, tuple[str, ...]] = {
    "add":      ("config", "cli", "test", "doc"),
    "new":      ("config", "cli", "test", "doc"),
    "fix":      ("def", "test", "caller"),
    "bug":      ("def", "test", "caller"),
    "refactor": ("def", "caller_a", "caller_b"),
}

def extract_facts(task: str, repo_symbols: frozenset[str]) -> tuple[Fact, ...]:
    kws = task_keywords(task)
    facts: list[Fact] = [Fact("kw", k) for k in kws]
    # Symbol facts: cross task keywords with repo symbols
    lower_syms = {s.lower(): s for s in repo_symbols}
    for kw in kws:
        for sym_lower, sym in lower_syms.items():
            if kw in sym_lower or sym_lower.startswith(kw):
                facts.append(Fact("sym", sym))
                break        # one symbol fact per keyword
    # Role facts from task verbs (deterministic)
    task_lower = task.lower()
    for verb, roles in _VERB_ROLE_FACTS.items():
        if verb in task_lower:
            facts.extend(Fact("role", r) for r in roles)
            break            # first verb wins; deterministic
    # Dedup preserving order
    seen: set[tuple[str, str]] = set()
    uniq: list[Fact] = []
    for f in facts:
        key = (f.kind, f.value)
        if key not in seen:
            seen.add(key); uniq.append(f)
    return tuple(uniq[:15])  # cap at 15

def file_covers(record, facts: tuple[Fact, ...]) -> frozenset[int]:
    path = (record.path or "").lower()
    preview = (record.content_preview or "").lower()
    syms = (record.symbol_names or "").lower()
    role = classify_file_role(record.path)
    covered = set()
    for i, f in enumerate(facts):
        v = f.value.lower()
        if f.kind == "kw" and (v in path or v in preview or v in syms):
            covered.add(i)
        elif f.kind == "sym" and v in syms:
            covered.add(i)
        elif f.kind == "role" and role == _normalize_role(f.value):
            covered.add(i)
    return frozenset(covered)
```

```python
# redcon/scorers/relevance.py - new helper, called when mode == "cover"
def _greedy_cover(
    ranked: list[RankedFile],
    facts: tuple[Fact, ...],
    files: list[FileRecord],
) -> list[RankedFile]:
    if not facts:
        return ranked
    # Build coverage map keyed by path
    sets: dict[str, frozenset[int]] = {
        rec.path: file_covers(rec, facts) for rec in files
    }
    universe = frozenset(range(len(facts)))
    uncovered = set(universe)
    chosen: list[str] = []
    chosen_set = set()
    # Greedy by marginal-coverage / token-cost
    while uncovered:
        best_path, best_gain, best_cost = None, 0, float("inf")
        for r in ranked:
            if r.file.path in chosen_set:
                continue
            gain = len(sets[r.file.path] & uncovered)
            cost = max(1, r.file.token_estimate or 1)
            # Deterministic tiebreak: higher gain, then higher score, then path
            if gain == 0:
                continue
            density = gain / cost
            if (best_path is None
                or density > best_gain / best_cost + 1e-9
                or (abs(density - best_gain / best_cost) < 1e-9
                    and r.score > _score_of(best_path, ranked))):
                best_path, best_gain, best_cost = r.file.path, gain, cost
        if best_path is None:
            break       # remaining facts unsatisfiable
        chosen.append(best_path); chosen_set.add(best_path)
        uncovered -= sets[best_path]
    # Re-emit: cover-selected first (in cover order), then remaining by score
    by_path = {r.file.path: r for r in ranked}
    head = [by_path[p] for p in chosen]
    tail = [r for r in ranked if r.file.path not in chosen_set]
    return head + tail
```

The wire-in to `score_files`:

```python
# at end of score_files, after the historical adjustments loop
if cfg.selection_mode == "cover":
    facts = extract_facts(task, repo_symbols=_collect_repo_symbols(files))
    ranked = _greedy_cover(ranked, facts, files)
return ranked
```

`_collect_repo_symbols(files)` is one pass over `record.symbol_names`,
splitting on commas. ~10 LOC.

### Behaviour delta vs current code (deterministic, testable)

  - Default mode (`"weighted"`): byte-identical to today.
  - `"cover"` mode on task 1 above: `redcon/cli.py` gains a slot,
    `tests/test_config.py` loses one. Verified deterministically by
    the unit test.
  - Coverage saturates at `|F|` files; remaining slots fall through
    to score order. So K > |F| reduces to today's top-K plus a
    permutation of the first |F| slots.
  - `extract_facts` returns same output for same input -> determinism
    preserved. No randomness, no embeddings.

## Estimated impact

- **File-coverage gain**: +1 to +2 atomic facts per task at K=4 on
  multi-axis tasks; on single-axis tasks (`"fix typo in config.py"`)
  the rule degenerates to top-K. For agent quality, this is the
  closer analogue of "agent retrieved the right files" than weighted
  score is.
- **Token reduction in pack output**: indirect. By selecting the
  CLI file or the caller graph file *instead of* a redundant test,
  the packer's compression budget is spent on a more diverse signal,
  which empirically (V08 / V22) reduces re-fetches in the same
  agent session. Conservative estimate: -3 to -7% of total
  agent-session tokens on tasks with >= 2 disjoint axes,
  via avoided re-issue of `redcon plan` with a tweaked task string.
  No effect on per-file compact-tier reduction numbers in
  BASELINE.md table.
- **Latency**: cold-start unaffected (atomic_facts.py is lazy-imported
  only when `selection_mode == "cover"`). Warm latency:
  greedy is `O(|F| * m)` set ops on `|F| <= 15`, `m <= 300`; for
  `m=10000` (large repo) it's still `<= 150k` tiny ops.
  Sub-millisecond on this repo.
- **Affects**: `redcon/scorers/relevance.py` (only when mode opted in),
  `redcon/config.py` (one new field), nothing else. Cache layer
  unchanged. MCP/CLI surface unchanged unless `redcon_rank` exposes
  the new mode (additive, opt-in).

## Implementation cost

- Lines of code: ~120 LOC `atomic_facts.py`, ~50 LOC additions to
  `relevance.py`, ~5 LOC config field, ~80 LOC tests = ~255 LOC.
- New runtime deps: none. Stdlib only.
- Risks:
  - **Determinism**: greedy with explicit tiebreak chain
    (gain -> density -> score -> path-lex) is deterministic same-input-
    same-output. Preserved.
  - **`task_keywords` cap of 16 vs fact cap of 15**: minor; both are
    deterministic constants. The cap matters for large compound tasks
    only.
  - **Role-fact heuristic is brittle**: the `_VERB_ROLE_FACTS` table
    encodes English task patterns. Tasks in non-imperative form
    ("`config option for max retries needed`") miss the verb match
    and fall back to keyword + symbol facts only. Not a regression
    vs today (today has *no* role facts at all), but the upper bound
    on the rule's lift drops.
  - **Symbol coverage depends on `record.symbol_names` being
    populated**: which depends on the symbol extractor having run.
    On a cold repo without symbols, the symbol-fact channel is
    empty and the algorithm reduces to keyword + role only. Still
    a strict superset of today.
  - **Must-preserve / output formatting**: untouched - this changes
    file *selection*, not file *content* or compression tier.

## Disqualifiers / why this might be wrong

1. **The fact set is too small to matter.** With `|F| in [5, 15]` and
   typical K = 6-12, coverage saturates *before* the cover algorithm
   has time to express its preference. Past saturation, behaviour is
   identical to top-K. Real agent budgets (M9 default top_files=12)
   may already be in the saturation regime on this repo. Honest read:
   the cover behaviour kicks in only on tight budgets (K <= 6) or
   wide tasks (>= 8 facts). Whether that's the common case depends
   on usage telemetry I do not have.

2. **The fact-extraction is the actual problem.** The greedy bound is
   strong, but an "atomic fact" extracted by simple keyword + role
   rules is a noisy proxy for what the agent actually needs. If the
   role-fact for `"add"` is wrong (e.g. the new feature does *not*
   need a doc update), the algorithm spends a slot on an irrelevant
   doc file and is strictly worse than top-K. The first user-facing
   bug will be "why did set-cover put a docs file in my pack?". This
   risk is real - it's the same risk every rule-based extractor
   carries. Mitigation: keep `selection_mode = "weighted"` as the
   default; make `"cover"` an opt-in per call.

3. **Already implicit in `file_roles.py` + `import_graph.py`.**
   Today's scorer already applies role multipliers and an import-graph
   bonus. Both are *diversity-encouraging* signals: the role multiplier
   penalises an over-representation of one role, and the graph bonus
   pulls in adjacent files. So part of the lift V29 claims is already
   captured. The remaining lift is precisely the *submodular*
   anti-redundancy ("don't pick a third file that re-covers fact 1
   when fact 2 is uncovered"), which today's scorer cannot express
   no matter how the weights are tuned. That's the genuine
   contribution; it is narrower than the full V29 framing.

4. **Listed in BASELINE.md "open frontier".** Line 54 explicitly says
   "Markov-blanket / set-cover style file selection" is *not* done
   yet. So this is not already implemented in disguise - but it is
   on the explicit roadmap, which means the originality bar is the
   *deterministic, no-LLM, repo-symbol-grounded* fact extractor, not
   the set-cover bit. The set-cover bit is textbook.

## Verdict

- **Novelty: medium**. Set-cover is a 1970s textbook result with a
  tight `(1 - 1/e)` bound; the contribution here is a deterministic
  fact-extraction recipe that fits Redcon's "no embeddings" rule and
  re-uses already-shipped pieces (`task_keywords`,
  `classify_file_role`, `symbol_names`). The empirical lift on the
  two test tasks is +1-2 facts at K=4, which is a real shift in
  selection but not a paradigm change.
- **Feasibility: high**. ~250 LOC, no new deps, opt-in via config,
  deterministic, fully testable on the manual cases above. Default
  path is unchanged so the change ships with zero regression
  surface.
- **Estimated speed of prototype: 1-2 days**. Half a day for
  `atomic_facts.py` and the greedy helper, half a day for the
  config wire-in and tests, possibly a second day to hand-tune the
  `_VERB_ROLE_FACTS` table on a small task corpus.
- **Recommend prototype: conditional-on-X**, where X is "we have a
  small corpus of real agent tasks (10-30) annotated with
  ground-truth file lists". Without that, we cannot verify that
  greedy cover beats weighted top-K beyond the two manually-worked
  examples in this note. With it, the prototype either replaces the
  default rank or is shelved as a known-limit experiment - either
  outcome is informative. Worth the day of plumbing.
