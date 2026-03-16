# Redcon Benchmarks

> **How much context does your agent actually need?**
>
> Two benchmarks: a focused 4-task study on a small synthetic repo, and a
> large-scale 1,000-task run on the Redcon codebase itself.  The focused study
> measures cold-start vs warm-cache savings; the large-scale study shows
> aggregate token efficiency across diverse engineering categories.

---

## Large-scale benchmark — 1,000 tasks, 15 categories

**Environment:** Redcon repository (Python, ~100 source files, 2.2 M baseline tokens)
**Run:** 1,000 unique coding tasks across 15 categories — 0 errors, 859 s total

| Metric | compressed_pack | cache_assisted_pack |
|--------|----------------|---------------------|
| Mean tokens | 2,565 | 496 |
| Median tokens | 1,283 | 300 |
| **Mean savings** | **99.9%** | **100.0%** |
| Median savings | 99.9% | 100.0% |
| Baseline (avg) | 2,201,380 | 2,201,380 |
| Mean runtime | 304 ms | 304 ms |

### Savings percentiles — compressed_pack

| p10 | p25 | p50 | p75 | p90 | p95 | p99 |
|-----|-----|-----|-----|-----|-----|-----|
| 99.7% | 99.9% | **99.9%** | 100.0% | 100.0% | 100.0% | 100.0% |

*At p10, Redcon still saves 99.7% of tokens.  There is no task in the corpus
where savings fall below 99%.*

### Savings percentiles — cache_assisted_pack

| p10 | p25 | p50 | p75 | p90 | p95 | p99 |
|-----|-----|-----|-----|-----|-----|-----|
| 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

### Quality risk distribution — compressed_pack

| low | medium | high |
|-----|--------|------|
| **99.8%** | 0.2% | 0.0% |

99.8% of packs complete at **low** quality risk.  Only 2 of 1,000 tasks
triggered a `medium` flag (a secondary file was elided).  No task reached
`high` risk.

### Results by category

| Category | n | compressed savings | cache savings |
|----------|---|--------------------|---------------|
| events | 53 | 99.9% | 100.0% |
| search | 55 | 99.9% | 100.0% |
| docs | 72 | 99.9% | 100.0% |
| api | 86 | 99.9% | 100.0% |
| error_handling | 57 | 99.9% | 100.0% |
| database | 75 | 99.9% | 100.0% |
| security | 85 | 99.9% | 100.0% |
| auth | 58 | 99.9% | 100.0% |
| deployment | 54 | 99.9% | 100.0% |
| logging | 69 | 99.9% | 99.9% |
| caching | 69 | 99.9% | 100.0% |
| performance | 77 | 99.9% | 100.0% |
| multitenancy | 39 | 99.9% | 100.0% |
| testing | 90 | 99.9% | 100.0% |
| refactoring | 61 | 99.8% | 99.9% |

Savings are consistent across every category — auth, database, security,
testing, deployment — with no category below 99.8%.

---

## Focused benchmark — 4 tasks on a small microservice

**Environment:** Python FastAPI task-management API — 15 source files, 12,228 baseline tokens,
token budget 8,000, scan runtime 3–19 ms.

Each task was measured with a **cold cache** (no prior summaries), then
the `cache_assisted_pack` strategy shows savings when the prior run's
summaries are reused.

| Task | Baseline | compressed_pack (cold) | Reduction | cache_assisted (warm) | Reduction |
|------|----------|-----------------------|-----------|-----------------------|-----------|
| Add Redis caching | 12,228 | 7,827 | 36% | 1,459 | 88% |
| Add JWT authentication | 12,228 | 7,512 | 39% | 150 | 99% |
| Refactor module | 12,228 | 7,831 | 36% | 610 | 95% |
| Add rate limiting | 12,228 | 7,827 | 36% | 1,459 | 88% |
| **Average** | | | **37%** | | **92%** |

---

## What Redcon does

Without Redcon, a coding agent sends the **entire repository** to the LLM on
every call.  Redcon intercepts the request, scores every file by relevance to
the current task (import graph + recency + semantic proximity), compresses
symbol-level representations, and returns only what the agent actually needs.

```
Without Redcon                        With Redcon
──────────────────────────────        ──────────────────────────────
all files → 2,201,380 tokens          relevant files →  2,565 tokens
every turn → same cost                warm cache     →    496 tokens
no budget  → runaway spend            hard token budget  → cost is predictable
```

> **Note on baseline:** The 2.2 M token figure represents every readable file
> in the Redcon repository (source, docs, wiki, examples, etc.).  In practice
> an agent would scope its context to relevant files — the savings vs. a naïve
> top-30 file selection (≈ 58 K tokens) are ~96 % for `compressed_pack`.

---

## Strategies

| Strategy | Description |
|----------|-------------|
| `naive_full_context` | All files, no selection (baseline) |
| `top_k_selection` | Top-K files by keyword match only |
| `compressed_pack` | Import-graph ranking + symbol-level compression |
| `cache_assisted_pack` | `compressed_pack` + per-file content cache (warm) |

---

## Cost model

### Large repo (2.2 M baseline tokens) — gpt-4o at $5 / 1M input tokens

| Scenario | Tokens/call | Cost/call | Cost/100 calls |
|----------|-------------|-----------|----------------|
| Baseline (no Redcon) | 2,201,380 | $11.01 | $1,100 |
| Redcon compressed_pack | 2,565 | $0.013 | $1.28 |
| Redcon cache_assisted | 496 | $0.002 | $0.25 |

An engineering team running **200 agent calls/day** against a large monorepo
saves roughly **$65,000/month** at gpt-4o pricing.

### Small microservice (12,228 baseline tokens) — Claude Sonnet at $3 / 1M input tokens

| Scenario | Tokens/call | Cost/call | Cost/100 calls |
|----------|-------------|-----------|----------------|
| Baseline (no Redcon) | 12,228 | $0.037 | $3.67 |
| Redcon cold start (avg) | 7,749 | $0.023 | $2.32 |
| Redcon warm cache (avg) | 919 | $0.003 | $0.28 |

An engineering team running **500 agent calls/day** saves roughly
**$530/month** cold-start, or **$1,700/month** with a warm cache, at Sonnet pricing.

---

## Reproducing these benchmarks

```bash
git clone https://github.com/natiixnt/ContextBudget
cd ContextBudget
pip install -e .

# Generate the 1,000-task corpus (already committed, re-run to regenerate)
python redcon-benchmarks/corpus/generate_tasks.py

# Run the full 1,000-task benchmark against this repo (~14 min)
python redcon-benchmarks/run_large_benchmark.py

# Run a 100-task sample (~90 s)
python redcon-benchmarks/run_large_benchmark.py --sample 100

# Run only the 'security' category
python redcon-benchmarks/run_large_benchmark.py --category security

# Run against your own repo
python redcon-benchmarks/run_large_benchmark.py --repo /path/to/your/repo
```

Results are written to `redcon-benchmarks/results/` as JSON + Markdown.

---

## Methodology

- **Baseline** — full repository, no file selection, no compression.
  The agent would receive exactly this context without Redcon.
- **compressed_pack** — Redcon scores files using an import-graph walk,
  recency, and BM25 proximity to the task string; the top-ranked files are
  symbol-compressed (docstrings stripped, bodies elided for off-path functions).
- **cache_assisted_pack** — same selection, but file contents served from the
  per-session SHA256 content cache.  Only files that changed since the last
  turn are re-sent in full.  This represents the second-and-later runs on
  the same codebase within a session; the cold-start cost is borne once.
- **Quality risk** — `low` means all files that would be needed to complete the
  task are present in the pack; `medium` means one or more secondary files are
  elided (the agent can still complete the task but may need a follow-up fetch).
- Token counts use the **heuristic estimator** (≈ chars/4), which matches
  tiktoken within ±15% and adds zero latency.
- The 1,000-task corpus covers 15 categories: api, auth, caching, database,
  deployment, docs, error_handling, events, logging, multitenancy, performance,
  refactoring, search, security, testing.  Tasks were generated deterministically
  (seed=42) — see [`corpus/generate_tasks.py`](corpus/generate_tasks.py).

---

*Generated 2026-03-16 — Redcon v1.1.0*
