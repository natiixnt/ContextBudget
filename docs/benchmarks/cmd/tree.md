# Compressor: tree

_Generated 2026-04-27 08:46 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `tree` | 10 | -30.0% (cold 0.07 ms, warm 0.02 ms) | -30.0% (cold 0.02 ms, warm 0.01 ms) | -160.0% (cold 0.03 ms, warm 0.02 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`tree.json`](./tree.json) for the full structured payload.