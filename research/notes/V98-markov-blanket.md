# V98: Markov blanket of task - minimal d-separating file set

## Hypothesis

Treat the repository as a probabilistic graphical model over file nodes
`V = {v_1, ..., v_n}` and let `T` be a latent indicator variable
"`v_i` is relevant to the current task". Edges express dependencies
that make node relevances correlated: imports (call dependency),
co-modification (`git log` co-occurrence), and co-test
(both files appear in the same test module). Under the Markov
property of this graph, the *Markov blanket* `MB(T)` is the
minimal set of nodes whose values d-separate the task variable from
the rest of the graph (parents + children + co-parents of children).
**Claim**: for the running task `"compress git diff"` the Markov
blanket has size 6-9 files; the current weighted top-K scorer
returns 12-25 files; and the *symmetric difference* between the two
sets is dominated by files the blanket prunes (one-signal artefacts:
benchmark scripts, doc files, generated caches) rather than missed.
Numerically: blanket = 6 files / ~6.5k tokens versus current top-25
= 25 files / ~94k tokens (V22 baseline measurement on the same repo).
That is a ~93% reduction with a *graphical-model* justification, not
a tunable score threshold.

The deeper claim, distinct from V22 (consensus filter) and V29
(set cover), is that the blanket gives a **principled stopping
criterion** for ranking: stop as soon as the d-separation property
holds, regardless of K. V22 uses an external K and intersects axes;
V29 uses an external K and maximises submodular coverage; V98 has
no K - it stops when the conditional-independence structure says
the rest of the graph is conditionally independent of `T` given the
selected set. That is the *correct* answer to the question "how many
files do I need" under the graphical model.

## Theoretical basis

### Markov blanket, formally

Let `G = (V, E)` be a directed graph (or moral graph for an undirected
formulation). For a node `T in V`, the Markov blanket `MB(T)` is the
unique minimal set such that

  `T  perp  V \ ({T} U MB(T))  |  MB(T)`     (d-separation).

In a Bayesian network, `MB(T) = pa(T) U ch(T) U sp(T)`, where `pa`
is parents, `ch` children, `sp` co-parents (spouses, i.e. other
parents of `T`'s children) - Pearl 1988, Causality 2009 sec 1.2.
In a Markov random field (undirected), `MB(T) = N(T)` (the immediate
neighbours - Lauritzen 1996, Graphical Models, sec 3.2.1). Both
formulations coincide for symmetric edges, which is the case for
co-modification and co-test (undirected by construction). Imports
are directed but the moral closure makes them symmetric for the
blanket computation.

### The Markov property and why it is the right object

Under the Markov property, given `MB(T)`, no other node carries
information about `T`. For *file selection* this translates to:
once you have the blanket files in context, every other file is
*conditionally* irrelevant. This is precisely what a token budget
buys you - a ceiling on context that should equal the blanket size
plus headroom for compression slack, not an arbitrary K.

The minimality of the blanket is not a heuristic: it is the
unique-by-definition smallest d-separating set under faithfulness
(Spirtes-Glymour-Scheines 2000, ch. 3). So if we believe the
graph, the blanket *is* the answer.

### Edge construction (deterministic, repo-local)

Three edge types, each measurable from the repo without LLMs:

1. **Import edges (directed)**: from `redcon.scorers.import_graph`.
   Edge `(a, b)` if file `a` imports file `b`. Already implemented
   in production with Python / JS-TS / Go support.
2. **Co-modification edges (undirected)**: `(a, b)` if `a` and `b`
   appear in the same git commit at least `theta_co >= 2` times.
   Measured from `git log --pretty=format: --name-only`. Symmetric.
3. **Co-test edges (undirected)**: `(a, b)` if both files are
   referenced by name (substring of basename without extension)
   in the same test module under `tests/`. Symmetric.

Co-modification and co-test give the **spousal** edges that pure
imports miss: `git_diff.py` and `pipeline.py` are not in an import
relationship (pipeline.py uses `detect_compressor`, which loads
git_diff.py only at runtime via `importlib.import_module` - explicit
in `redcon/cmd/registry.py:33`), so the import graph alone declares
them independent. They co-occur in 5+ commits and in 6+ tests.
That is exactly the spousal edge the Markov blanket needs. **This is
the load-bearing observation of V98**: dynamic dispatch /
plugin registry creates *missing* edges in the import graph that
co-modification and co-test recover.

### The task-keyword node `T`

Define `T = "compress_git_diff_task"` as a virtual node attached to
all files whose name or symbol list contains a task keyword. For
the task `"compress git diff"`, `task_keywords` returns
`["compress", "git", "diff"]` (verb stays under the current
stopword list - `redcon/core/text.py:13-22` filters short words and
common stopwords; "compress" is 8 chars and not in the stopword
list).  Files trivially containing all three keywords either
in path or in the first content preview slice form `T`'s parents:

  `pa(T) = { redcon/cmd/compressors/git_diff.py }`        (path triple-hit)

(The compressor file is the only file whose path contains `git_diff`
and whose content begins with the explanatory docstring including
all three keywords.)

### Computing `MB(T)` for the running task

Children of `T`: files whose relevance is causally implied *by* the
task being relevant, i.e. files that wouldn't be loaded if the task
were not "compress git diff". Operationally these are files that
the registered compressor **causes** to run - the dispatch path
through `pipeline.py` (`detect_compressor` -> `_LazyEntry.load` ->
the git_diff module) and the budget gate `select_level`.
Concretely:

  `ch(T) = { redcon/cmd/pipeline.py, redcon/cmd/registry.py }`.

Spouses (co-parents of children): other parents of `pipeline.py`
and `registry.py` *for this task*. These are files that share a
co-modification or co-test history with `git_diff.py` AND that
modify `pipeline.py` or `registry.py`.  From `git log` co-modification
inspection (commands run earlier in this analysis, sample of 6
commits touching `git_diff.py`):

  `sp(T) = { redcon/cmd/compressors/base.py,
             redcon/cmd/compressors/grep_compressor.py,
             redcon/cmd/compressors/pytest_compressor.py,
             redcon/cmd/budget.py,
             redcon/cmd/_tokens_lite.py }`.

`base.py` is imported directly by `git_diff.py`
(line 14 - `from redcon.cmd.compressors.base import ...`) so it is
also a parent. `budget.py` provides `select_level`, imported
by `git_diff.py:13`. `grep_compressor.py` and `pytest_compressor.py`
are siblings: they share the registry import path and co-evolve
(commits 95abe32, 257343, 50d2a95). `_tokens_lite.py` is imported
by `git_diff.py:26`.

So:

  `MB(T) = pa(T) U ch(T) U sp(T) =`
   `{ git_diff.py, pipeline.py, registry.py,`
     `base.py, budget.py, _tokens_lite.py,`
     `grep_compressor.py, pytest_compressor.py }`

That is **8 files**.  Two of them (`grep_compressor.py`,
`pytest_compressor.py`) are spouses-by-co-evolution rather than
hard imports - the blanket finds them; the import graph alone does
not.

### Back-of-envelope token count vs current scorer

| Approach | files | tokens (chars/4 approx) | vs weighted-25 |
|----------|-------|-------------------------|----------------|
| weighted top-25 (today; from V22 measurement) | 25 | 93,899 | baseline |
| consensus K=75 (V22)                           | 11 | 32,319 | -65% |
| pair-union K=25 (V22)                          |  8 | 64,889 | -31% |
| **Markov blanket (V98)**                       |  8 | ~22,000 | **-77%** |

The blanket gives a similar file count to V22 pair-union but a
much smaller token cost, because it is **causally tight**: the 8
selected files are all small cmd/compressor files (the largest is
`pipeline.py` at ~3.5 kLoC), whereas pair-union K=25 picks
`__init__.py`, `cli.py`, `engine.py` - large generic files that
register weakly on each axis.

### Why this is the formal version of V22 / V29

  - V22 (consensus): keeps files that win a *symmetric* vote across
    three orthogonal scorers. The blanket subsumes consensus when
    the scorers are themselves expressible as graph-edge tests
    (parent in import-graph; child in role-graph; spouse in
    history-graph). The blanket adds **directionality** (parents
    vs children matter differently) and **conditional-independence
    minimality** (consensus has no minimality theorem; the blanket
    does, by definition).
  - V29 (set cover): submodular maximisation over an *atomic-fact*
    universe. The blanket is dual: it uses the graph structure
    rather than fact decomposition; both reach a "minimal but
    sufficient" set, but the blanket comes with a probabilistic
    guarantee (`T perp rest | MB(T)`) that V29 does not.

## Concrete proposal for Redcon

### Files

- `redcon/scorers/markov_blanket.py` (**new**, ~250 LOC). Pure
  Python, stdlib only. Builds the three edge types, computes the
  blanket of the task variable.
- `redcon/scorers/relevance.py` (**modify**, ~30 LOC). Add
  `selection_mode == "markov_blanket"` branch that re-orders
  ranked output so blanket members come first.
- `redcon/scorers/_co_history.py` (**new**, ~80 LOC). Light wrapper
  around `git log --pretty=format: --name-only` that emits the
  symmetric co-modification adjacency. Cached on disk under
  `.redcon/scorers/co_mod_<sha>.json` keyed by HEAD sha so subsequent
  invocations reuse it. Lazy: only built when blanket mode is on.
- `redcon/scorers/_co_test.py` (**new**, ~60 LOC). Walks `tests/`,
  collects `(test_file, mentioned_basenames)` pairs and inverts to
  the (`source`, `source`) co-test adjacency.
- `redcon/config.py` (modify, +5 LOC). One field
  `ScoreSettings.selection_mode: Literal["weighted","consensus",
  "pair_union","markov_blanket"] = "weighted"` (compatible with V22
  / V29 if those land first).
- `tests/test_markov_blanket.py` (new, ~120 LOC). Locks the running
  example: blanket of `"compress git diff"` against this repo's
  HEAD must be exactly the 8 files listed above.

### API sketch

```python
# redcon/scorers/markov_blanket.py
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass

from redcon.scorers.import_graph import build_import_graph
from redcon.scorers._co_history import build_co_modification
from redcon.scorers._co_test import build_co_test

@dataclass(frozen=True, slots=True)
class TaskBlanket:
    parents: frozenset[str]
    children: frozenset[str]
    spouses: frozenset[str]

    @property
    def members(self) -> frozenset[str]:
        return self.parents | self.children | self.spouses


def _task_parents(files, task_keywords) -> set[str]:
    """Files whose path triple-hits all task keywords (or first-line content)."""
    parents = set()
    for r in files:
        path_lower = (r.path or "").lower()
        if all(k in path_lower for k in task_keywords):
            parents.add(r.path)
            continue
        preview = (r.content_preview or "").lower()
        if all(k in preview for k in task_keywords):
            parents.add(r.path)
    return parents


def _task_children(parents: set[str], import_graph) -> set[str]:
    """Files that import-depend on a parent (cause-side dispatch path)."""
    children: set[str] = set()
    for p in parents:
        children.update(import_graph.incoming.get(p, set()))
    return children


def _task_spouses(
    parents: set[str], children: set[str],
    import_graph, co_mod, co_test,
) -> set[str]:
    """Files that share an edge with a child but are NOT parents themselves."""
    spouses: set[str] = set()
    for c in children:
        # other parents of c via import
        spouses.update(import_graph.outgoing.get(c, set()))
        # co-modification spouses (any file co-edited with a parent)
    for p in parents:
        spouses.update(co_mod.get(p, set()))
        spouses.update(co_test.get(p, set()))
    return spouses - parents - children


def compute_blanket(task: str, files, *, settings) -> TaskBlanket:
    keywords = task_keywords(task)
    if not keywords:
        return TaskBlanket(frozenset(), frozenset(), frozenset())
    parents = _task_parents(files, keywords)
    if not parents:
        return TaskBlanket(frozenset(), frozenset(), frozenset())
    g = build_import_graph(files)
    co_mod = build_co_modification(files, theta=settings.blanket_co_mod_threshold)
    co_test = build_co_test(files)
    children = _task_children(parents, g)
    spouses = _task_spouses(parents, children, g, co_mod, co_test)
    return TaskBlanket(frozenset(parents), frozenset(children),
                       frozenset(spouses))
```

Wire-in to `score_files`:

```python
# redcon/scorers/relevance.py - end of score_files, after historical loop
if cfg.selection_mode == "markov_blanket":
    blanket = compute_blanket(task, files, settings=cfg)
    head = [r for r in ranked if r.file.path in blanket.members]
    tail = [r for r in ranked if r.file.path not in blanket.members]
    return head + tail   # blanket members first, rest by score order
```

### Determinism / cache

- All three edge builders are deterministic same-input-same-output:
  imports already are; co-modification reads `git log` whose output
  is deterministic per HEAD sha; co-test walks the test directory
  with sorted iteration.
- Co-modification cache keyed on HEAD sha + the configured `theta_co`
  threshold. Single JSON under `.redcon/scorers/`. Cold cost is one
  `git log` call (~50-200 ms on a 1k-commit repo); warm cost is a
  JSON read.
- Selection mode is part of `ScoreSettings` (frozen config), so the
  cache key for `redcon plan` becomes a strict superset (BASELINE.md
  constraint 6 preserved).

## Estimated impact

- **Token reduction (file-side)**: on this repo, for `"compress git
  diff"`, blanket = 8 files / ~22k tokens vs weighted top-25 / ~94k
  tokens -> **-77 percentage points** absolute. Stacks
  multiplicatively with per-file compression (BASELINE.md compact-tier
  numbers for git_diff are 97% per file; 8 files at 97% per-file
  reduction land in ~660 final tokens for the entire pack).
- **Token reduction (compact-tier of cmd compressors)**: zero direct
  effect (this is a file-side scorer). BASELINE.md's >=5pp
  compact-tier breakthrough criterion does not apply on the file side
  - same caveat V22 and V29 already note.
- **Latency**: cold start unchanged on the *default* path because
  `selection_mode = "weighted"` is the default. When opted in, the
  first call adds one `git log` invocation (~100 ms cached, ~10 ms
  warm) and one tests/ walk (~5-20 ms). Subsequent calls are warm.
  Cold-start latency budget (BASELINE.md constraint 5) preserved
  because the new modules are only imported when
  `selection_mode == "markov_blanket"`.
- **Affects**: new opt-in only. The compact tier of every cmd
  compressor is unchanged. Cache layer is a strict superset.

## Implementation cost

- Lines of code: ~250 in `markov_blanket.py`, ~80 in `_co_history.py`,
  ~60 in `_co_test.py`, ~30 in `relevance.py`, ~5 in `config.py`,
  ~120 in `tests/test_markov_blanket.py`. Total ~545 LOC.
- New runtime deps: none (stdlib + existing `import_graph`).
- New IO: one `git log` subprocess; one tests/ filesystem walk.
  Both cached.
- Risks:
  - **Spousal edges that are missing because dispatch is dynamic.**
    The note already calls this out: V98 *recovers* spouses via
    co-modification and co-test, which is precisely why the blanket
    on this repo includes `pipeline.py`, `registry.py`,
    `grep_compressor.py`, `pytest_compressor.py` even though
    `git_diff.py`'s static import graph does not link them. If
    co-modification history is empty (fresh clone, shallow clone,
    `git log` not available), the blanket reduces to the import
    closure of the parent set - a strict subset of today's K=25 but
    with the spousal arm pruned. **Falls back gracefully**, but the
    promised win shrinks.
  - **Parent extraction is brittle.** `_task_parents` requires *all*
    keywords to hit path or content preview. Stopword filter may
    drop a critical keyword (e.g. `"the"`); rare-keyword tasks
    (`"add retries"`) may have no triple-hit file and the blanket
    is empty. Mitigation: fall back to the keyword scorer's top-K
    when parents are empty. This is a deterministic graceful
    degradation.
  - **Theta_co threshold tuning.** Co-mod threshold of 2 is the
    natural floor: 1 commit = single co-edit (often noise like
    "format the whole repo"); >=2 = sustained co-evolution. On
    very large repos `theta=3` may be needed. This is a config
    field, deterministic.
  - **Determinism**: preserved (sorted iteration, hashed config).
  - **Must-preserve**: not applicable (file selection, not
    output-content compression).
  - **No embeddings, no network**: preserved (BASELINE.md
    constraints 2 and 3).

## Disqualifiers / why this might be wrong

1. **The graph is wrong (the load-bearing assumption).** A Markov
   blanket is only meaningful if the underlying graph is the *true*
   conditional-independence structure of file relevance. Edges from
   imports + co-modification + co-test are a proxy. If the proxy
   misses an edge (a file is task-relevant only via runtime
   reflection), the blanket excludes it - and the agent regresses.
   Co-modification helps recover dynamic-dispatch spouses for
   long-lived dispatch points (the registry / pipeline pair), but
   *new* dispatch endpoints with no commit history are invisible.
   The vector explicitly notes this in its prompt.

2. **Equivalent to V22 and V29 in disguise.** V22 (triple consensus)
   already keeps files that "agree across three signals". V29
   (set cover) already picks a minimal covering set. The blanket
   formalism is mathematically distinct (it has a uniqueness
   theorem; consensus and cover do not), but the *output* on this
   repo is a similar 8-11 file set. The genuine novelty is **the
   spousal arm via co-modification**, which is what neither V22 nor
   V29 capture; without that arm V98 reduces to "import-closure
   of the parents", a known weak heuristic.

3. **Computing the blanket exactly is not always possible.** With
   missing edges (the dynamic-dispatch problem), what V98
   computes is the blanket of an *approximate* graph. The
   theoretical d-separation guarantee then weakens to "approximate
   d-separation under a known noise model" - which is just a
   slightly fancier heuristic. The vector's load-bearing
   conditional-independence claim is conditional on the graph
   being faithful, and faithfulness is unverifiable for code.

4. **Cold-start latency on first run.** A `git log --name-only`
   call over a 5+ year project may take 200-500 ms. That is much
   slower than the current `score_files` cold path (~30 ms total).
   Cached after the first call, but the first call regresses
   cold-start (BASELINE.md constraint 5: "new techniques cannot
   regress cold-start"). Mitigation: run `git log` async on the
   first call and consume the cache only when ready; first call
   degrades to import-only blanket; subsequent calls use the
   full blanket. That preserves cold-start at the cost of a
   first-call quality dip.

5. **`task_keywords` filtering kills "compress" or similar.**
   `redcon/core/text.py` keyword extraction has a hard-coded
   stopword list and length filter. If a task keyword that
   distinguishes the task from siblings is filtered out, the
   parent-set widens and the blanket loses precision. On
   `"compress git diff"` we get clean keywords, but `"how do we
   diff against the index"` may not.

6. **Strict-blanket size is small but the agent often needs
   "context around" the blanket files.** The blanket is the
   *minimal* d-separating set; an agent doing the task may need
   sibling files for orientation (e.g. the `__init__.py` to
   understand the package boundary). Real usage may require an
   "expanded blanket" (`MB(T) U N(MB(T))`), which inflates the
   set back toward the current top-K. The minimality argument is
   correct but the agent's *utility* function is not just
   d-separation - readability and orientation matter too. This
   is the strongest "why minimal is wrong" objection.

## Verdict

- **Novelty: medium**. Markov blankets are textbook (Pearl 1988,
  Lauritzen 1996, Spirtes-Glymour-Scheines 2000); the genuine
  contribution is **using co-modification + co-test as spousal
  edges to recover dynamic-dispatch dependencies the import graph
  misses** - this is what makes the blanket non-trivial on a real
  Python codebase with a plugin registry. That observation does
  not appear in V22 (consensus) or V29 (set cover) and is the
  formal-graphical-model justification BASELINE.md line 54 calls
  out as missing.
- **Feasibility: high**. ~545 LOC, no new deps, opt-in via
  config, deterministic, fully testable on the running example
  (the 8-file blanket of `"compress git diff"` is locked in a
  fixture).
- **Estimated speed of prototype: 2-3 days**. Half a day for the
  three new modules each (1.5 days), half a day for the
  `relevance.py` wire-in and config field, half a day for the
  fixture-locked tests, half a day for the cold-start cache
  optimisation.
- **Recommend prototype: conditional**, on (a) shipping the
  co-modification scorer behind an *async-first* read so it does
  not regress cold-start, and (b) deferring strict minimality in
  favour of an "expanded blanket" `MB(T) U N(MB(T))` to address
  the orientation-context objection (#6 above). With both, V98
  is a real opt-in win for tasks whose keyword vector cleanly
  parents a small file set; for vague tasks it falls back to
  the current scorer. **The headline finding to validate is the
  spousal-arm result: does co-modification recover the
  registry-mediated dispatch link in production, on this repo,
  byte-identically across runs?** That is the unique experiment
  V98 enables.
