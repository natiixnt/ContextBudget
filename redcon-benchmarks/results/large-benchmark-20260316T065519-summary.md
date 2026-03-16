# Redcon Large-Scale Benchmark

**1000 tasks** across **15 categories** — repo: `/Users/naithai/Desktop/amogus/praca/ContextBudget` — duration: 859s

---

## Overall results

| Metric | compressed_pack | cache_assisted_pack |
|--------|----------------|---------------------|
| Mean tokens | 2,565 | 496 |
| Median tokens | 1,283 | 300 |
| Mean savings | 99.9% | 100.0% |
| Median savings | 99.9% | 100.0% |
| Baseline (avg) | 2,201,380 | 2,201,380 |
| Mean runtime | 304ms | 304ms |

## Savings percentiles (compressed_pack)

| p10 | p25 | p50 | p75 | p90 | p95 | p99 |
|-----|-----|-----|-----|-----|-----|-----|
| 99.7% | 99.9% | 99.9% | 100.0% | 100.0% | 100.0% | 100.0% |

## Savings percentiles (cache_assisted_pack)

| p10 | p25 | p50 | p75 | p90 | p95 | p99 |
|-----|-----|-----|-----|-----|-----|-----|
| 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## Quality risk distribution (compressed_pack)

| low | medium | high |
|-----|--------|------|
| 99.8% | 0.2% | 0.0% |

## Results by category

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

---

*Generated 2026-03-16 — Redcon v1.1.0 — 1000 tasks, 1000 succeeded, 0 errors*