# Compressor: npm_test

_Generated 2026-04-26 19:55 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `npm_test_jest` | 68 | +47.1% (cold 0.10 ms, warm 0.02 ms) | +47.1% (cold 0.02 ms, warm 0.02 ms) | +69.1% (cold 0.02 ms, warm 0.02 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`npm_test.json`](./npm_test.json) for the full structured payload.