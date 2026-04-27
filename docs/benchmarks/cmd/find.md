# Compressor: find

_Generated 2026-04-27 08:46 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `find` | 12 | -41.7% (cold 0.05 ms, warm 0.01 ms) | -41.7% (cold 0.01 ms, warm 0.01 ms) | -125.0% (cold 0.02 ms, warm 0.01 ms) |
| `find_massive` | 3,398 | +43.7% (cold 1.43 ms, warm 0.73 ms) | +81.3% (cold 0.82 ms, warm 0.82 ms) | +99.8% (cold 0.51 ms, warm 0.51 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`find.json`](./find.json) for the full structured payload.