# Compressor: cargo_test

_Generated 2026-04-27 08:11 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `cargo_test` | 84 | +61.9% (cold 0.07 ms, warm 0.02 ms) | +67.9% (cold 0.02 ms, warm 0.02 ms) | +78.6% (cold 0.02 ms, warm 0.02 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`cargo_test.json`](./cargo_test.json) for the full structured payload.