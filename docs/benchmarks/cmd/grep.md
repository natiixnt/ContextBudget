# Compressor: grep

_Generated 2026-04-26 19:55 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `grep_small` | 19 | -42.1% (cold 0.07 ms, warm 0.01 ms) | -42.1% (cold 0.01 ms, warm 0.01 ms) | -47.4% (cold 0.02 ms, warm 0.01 ms) |
| `grep_massive` | 7,015 | +33.9% (cold 2.52 ms, warm 1.55 ms) | +76.9% (cold 1.37 ms, warm 1.32 ms) | +99.9% (cold 0.79 ms, warm 0.79 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`grep.json`](./grep.json) for the full structured payload.