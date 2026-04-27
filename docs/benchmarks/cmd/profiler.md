# Compressor: profiler

_Generated 2026-04-27 08:46 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `profiler_typical` | 2,366 | +74.4% (cold 0.97 ms, warm 0.48 ms) | +89.2% (cold 0.44 ms, warm 0.49 ms) | +99.0% (cold 0.40 ms, warm 0.40 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`profiler.json`](./profiler.json) for the full structured payload.