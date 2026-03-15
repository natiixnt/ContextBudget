# Redcon Agent Read Profile

Run: `run.json`
Generated at: 2026-03-15T12:00:00Z

## Summary

| Metric | Value |
|--------|------:|
| Files read (total) | 9 |
| Unique files read | 8 |
| Duplicate reads detected | 1 |
| Unnecessary reads | 2 |
| High token-cost reads | 3 |
| Tokens wasted (duplicates) | 340 |
| Tokens wasted (unnecessary) | 680 |
| Total tokens wasted | **1,020** |

## Duplicate Reads

| File | Read Count | Tokens/Read | Tokens Wasted |
|------|----------:|------------:|--------------:|
| `src/router.py` | 2 | 340 | 340 |

## Unnecessary Reads

Files with relevance score ≤ 1.0 and cost ≥ 50 tokens:

| File | Tokens | Relevance Score |
|------|-------:|----------------:|
| `src/utils/deprecated.py` | 420 | 0.8 |
| `tests/fixtures/stub.py` | 260 | 0.5 |

## High Token-Cost Reads

Files with original token count ≥ 500:

| File | Tokens |
|------|-------:|
| `src/auth/middleware.py` | 1,240 |
| `src/api/routes.py` | 980 |
| `src/config.py` | 820 |
