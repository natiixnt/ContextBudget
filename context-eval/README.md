# context-eval

**An open, reproducible benchmark for context-selection tools.**

Every AI coding agent has to answer the same question before it calls the
model: *which part of this repository does the current task actually need?*
Tools that answer it (redcon, aider's repo map, embedding retrievers,
full-repo dumpers) all claim to be good at it. None of them could be
compared, because there was no common measurement. context-eval is that
measurement.

## How it works

1. **Tasks come from real git history.** A commit is a task: the commit
   subject is the task description an agent would receive, and the source
   files the commit modified are the ground truth a selection tool should
   surface. Only `feat`/`fix`/`perf`/`refactor` commits qualify - their
   subjects describe code, not process.
2. **No leakage.** Every tool sees the repository checked out at the
   commit's *parent*, so nothing about the change itself is visible.
   Newly added files are excluded from ground truth (no tool could have
   selected a file that did not exist yet).
3. **Same budget for everyone.** Each tool returns a ranking; the harness
   greedily packs full file contents in that order into a fixed token
   budget (default 24,000 tokens, chars/4 estimator for every tool).
4. **Two metrics.**
   - **Coverage** - share of ground-truth files inside the packed budget.
   - **Tokens per coverage point** - `tokens_used / coverage`; what a
     point of useful context costs. Lower is cheaper evidence.

## Current results

Repository: this repo (335 source files) | Budget: 24,000 tokens |
Tasks: 33 real commits | Full table: [`results/results.md`](results/results.md)

| Tool | Mean coverage | Median | Tokens / coverage point |
|------|--------------:|-------:|------------------------:|
| `redcon` | **43.8%** | 50.0% | **306.8** |
| `keyword-topk` | 29.8% | 0.0% | 538.9 |
| `aider-repomap` | 15.3% | 0.0% | 533.3 |
| `pagerank` | 11.4% | 0.0% | 720.0 |
| `random` | 5.8% | 0.0% | 690.0 |
| `full-dump` | 0.0% | 0.0% | n/a |

Reading of the table: task-keyword matching alone reaches ~30%, structural
centrality alone (aider's repo map, plain PageRank) reaches 11-15%, and
redcon's hybrid of both reaches ~44% at the lowest cost per point. Full-repo
dumping scores zero here because under a fixed budget alphabetical packing
exhausts tokens before reaching the relevant modules - which is exactly the
failure mode budgets exist to expose.

## Compared approaches

| Adapter | What it is |
|---------|------------|
| `redcon` | Task-aware hybrid: keyword relevance + import-graph propagation, via the public `RedconEngine.plan()` API |
| `aider-repomap` | The real `aider` package's `RepoMap` ranking (PageRank over tree-sitter tags), with task identifiers passed the same way aider receives them from a chat |
| `keyword-topk` | Task-keyword matching only (path hits weighted over content hits) |
| `pagerank` | Task-agnostic PageRank over a regex-built import graph |
| `full-dump` | Everything in path order until the budget runs out (repomix-style) |
| `random` | Seeded random order - the floor any real tool must clear |

## Run it yourself

```bash
pip install git+https://github.com/natiixnt/ContextBudget
pip install aider-chat   # optional, enables the aider-repomap adapter

python context-eval/run.py --repo /path/to/any/git/repo --budget 24000
python context-eval/run.py --tools redcon,keyword-topk,random   # subset
```

Any git repository with a descriptive commit history works. Results land
in `results/results.json` and `results/results.md`.

## Adding a tool

An adapter is one function in
[`contexteval/adapters.py`](contexteval/adapters.py):

```python
def my_tool_rank(task: str, root: Path, files: list[str]) -> list[str]:
    """Return candidate files, best first. The harness handles the budget."""
```

Register it in `ADAPTERS` and it appears in every table. PRs adding tools
or task sets are welcome - the point of this benchmark is that nobody,
including redcon, gets to grade their own homework in private.

## Honest limitations

- Ground truth is "files the real commit touched". A tool that selects a
  *better* set than the human author gets penalised; over many tasks this
  noise averages out but individual tasks are only proxies.
- Packing uses full file contents for every tool. redcon's symbol-level
  compression would fit more files per budget in production, so this
  measures pure *selection* quality, deliberately ignoring compression.
- One repository so far. The harness runs on any git repo; results on
  more repositories (and more tools) are the roadmap.
- Single-commit tasks have binary coverage (0% or 100%), which is why
  medians are coarse; means over 33 tasks are the headline metric.
