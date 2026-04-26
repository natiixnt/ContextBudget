# Compressor: go_test

_Generated 2026-04-26 19:55 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `go_test` | 42 | +69.0% (cold 0.04 ms, warm 0.01 ms) | +69.0% (cold 0.01 ms, warm 0.01 ms) | +69.0% (cold 0.01 ms, warm 0.01 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`go_test.json`](./go_test.json) for the full structured payload.