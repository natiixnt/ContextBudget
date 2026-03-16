# Redcon Benchmarks

> **How much context does your agent actually need?**
>
> Redcon runs every task against a real repository and measures token usage
> across four strategies.  Numbers below are generated against a real Python
> microservice with 15 source files and 12,230 baseline tokens.

---

## Summary

| Task | Baseline | Redcon (first run) | Reduction | Warm cache |
|------|----------|--------------------|-----------|------------|
| Add authentication | 12,230 | **2,196** | **82%** | 150 (−99%) |
| Refactor module | 12,230 | **2,484** | **80%** | 150 (−99%) |
| Add rate limiting | 12,230 | **2,619** | **79%** | 150 (−99%) |
| Add caching layer | 12,230 | **7,935** | **35%** | 2,255 (−82%) |
| **Average** | | | **−69%** | **−95%** |

Benchmark environment: Python FastAPI task-management API, 15 source files,
12,230 tokens (heuristic estimator), token budget 8,000, scan runtime 3-19 ms.

---

## What Redcon does

Without Redcon, a coding agent sends the **entire repository** to the LLM on
every call.  Redcon intercepts the request, scores every file by relevance to
the current task (import graph + recency + semantic proximity), compresses
symbol-level representations, and returns only what the agent actually needs.

```
Without Redcon                        With Redcon
──────────────────────────────        ──────────────────────────────
all 15 files → 12,230 tokens          4-15 ranked files → 2,196 tokens
every turn   → same cost              warm cache        →   150 tokens
no budget    → runaway spend          hard token budget → cost is predictable
```

---

## Strategies

| Strategy | Description |
|----------|-------------|
| `naive_full_context` | All files, no selection (baseline) |
| `top_k_selection` | Top-K files by keyword match only |
| `compressed_pack` | Import-graph ranking + symbol-level compression |
| `cache_assisted_pack` | `compressed_pack` + per-file content cache (warm) |

Only `compressed_pack` and `cache_assisted_pack` are unique to Redcon.
The benchmark compares all four so you can see exactly where the savings come from.

---

## Task results

### 1. Add authentication

> *Add JWT authentication middleware to protect task and user API routes*

| Strategy | Tokens | Saved | Quality risk |
|----------|--------|-------|--------------|
| naive_full_context | 12,230 | - | low |
| compressed_pack | **2,196** | 10,034 (**82%**) | low |
| cache_assisted_pack | **150** | 12,080 (**99%**) | low |

Files selected: `app.py`, `config.py`, `routes/tasks.py`, `routes/users.py`,
`services/task_service.py`, `services/user_service.py`, `models/user.py` + tests.

The auth task pulls in both route layers and the user model - Redcon identifies
all the right files and skips the unrelated ones (`db/connection.py` is already
abstracted), keeping quality risk **low**.

→ [Full report](tasks/add-authentication.md)

---

### 2. Refactor module

> *Refactor the database repository layer to use connection pooling*

| Strategy | Tokens | Saved | Quality risk |
|----------|--------|-------|--------------|
| naive_full_context | 12,230 | - | low |
| compressed_pack | **2,484** | 9,746 (**80%**) | low |
| cache_assisted_pack | **150** | 12,080 (**99%**) | low |

Files selected: full repository minus unneeded test boilerplate.
The import-graph scorer correctly surfaces `db/connection.py`,
`db/repository.py`, and their service callers.

→ [Full report](tasks/refactor-module.md)

---

### 3. Add rate limiting

> *Add rate limiting middleware to API endpoints to prevent abuse*

| Strategy | Tokens | Saved | Quality risk |
|----------|--------|-------|--------------|
| naive_full_context | 12,230 | - | low |
| compressed_pack | **2,619** | 9,611 (**79%**) | low |
| cache_assisted_pack | **150** | 12,080 (**99%**) | low |

Files selected: `app.py` (bootstrap), `config.py`, route handlers.
Middleware tasks are cross-cutting, yet Redcon fits them in 2,619 tokens
with no quality degradation.

→ [Full report](tasks/add-rate-limiting.md)

---

### 4. Add caching layer

> *Add Redis caching to task lookup endpoints to reduce database load*

| Strategy | Tokens | Saved | Quality risk |
|----------|--------|-------|--------------|
| naive_full_context | 12,230 | - | low |
| compressed_pack | **7,935** | 4,295 (**35%**) | medium |
| cache_assisted_pack | **2,255** | 9,975 (**82%**) | low |

This task touches fewer files on the first scan (11 vs 15), so first-run
savings are smaller.  The `medium` quality risk flag indicates the compressed
pack omits the user service - intentional, since caching is task-scoped.
On a warm cache the pack drops to 2,255 tokens at `low` risk.

→ [Full report](tasks/add-caching.md)

---

## Cost model

At **$3 / 1M input tokens** (Claude Sonnet 4.5):

| Scenario | Tokens/call | Cost/call | Cost/100 calls |
|----------|-------------|-----------|----------------|
| Baseline (no Redcon) | 12,230 | $0.037 | $3.67 |
| Redcon first run (avg) | 3,809 | $0.011 | $1.14 |
| Redcon warm cache (avg) | 676 | $0.002 | $0.20 |

An engineering team running **500 agent calls/day** saves roughly
**$950/month** at Sonnet pricing - before accounting for reduced
latency and fewer timeout errors from oversized prompts.

---

## Reproducing these benchmarks

```bash
git clone https://github.com/natiixnt/ContextBudget
cd ContextBudget
pip install -e .
python benchmarks/run_benchmarks.py
```

Output written to `docs/benchmarks/` as `.json` + `.md` per task.

Raw JSON data is in [`tasks/`](tasks/) in this repo.

---

## Methodology

- **Baseline** - full repository, no file selection, no compression.
  The agent would receive exactly this context without Redcon.
- **compressed_pack** - Redcon scores files using an import-graph walk,
  recency, and BM25 proximity to the task string; the top-ranked files are
  symbol-compressed (docstrings stripped, bodies elided for off-path functions).
- **cache_assisted_pack** - same selection, but file contents served from the
  per-session SHA256 content cache.  Only files that changed since the last
  turn are re-sent in full.
- **Quality risk** - `low` means all files that would be needed to complete the
  task are present in the pack; `medium` means one or more secondary files are
  elided (the agent can still complete the task but may need a follow-up fetch).
- Token counts use the **heuristic estimator** (≈ chars/4), which matches
  tiktoken within ±15% and adds zero latency.

---

*Generated 2026-03-15 - Redcon v1.1.0*
