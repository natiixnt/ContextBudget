# Compressor: ls

_Generated 2026-04-26 19:55 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `ls` | 41 | +61.0% (cold 0.12 ms, warm 0.02 ms) | +61.0% (cold 0.02 ms, warm 0.02 ms) | +82.9% (cold 0.03 ms, warm 0.02 ms) |
| `ls_huge` | 1,543 | -0.3% (cold 0.74 ms, warm 0.72 ms) | +33.5% (cold 0.84 ms, warm 0.82 ms) | +99.0% (cold 0.71 ms, warm 0.69 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`ls.json`](./ls.json) for the full structured payload.