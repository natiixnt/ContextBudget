# Compressor: grep

_Generated 2026-04-27 08:46 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `grep_small` | 19 | -26.3% (cold 0.08 ms, warm 0.01 ms) | -26.3% (cold 0.01 ms, warm 0.01 ms) | -31.6% (cold 0.02 ms, warm 0.01 ms) |
| `grep_massive` | 7,015 | +40.3% (cold 2.50 ms, warm 1.50 ms) | +79.7% (cold 1.31 ms, warm 1.31 ms) | +99.9% (cold 0.82 ms, warm 0.84 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`grep.json`](./grep.json) for the full structured payload.