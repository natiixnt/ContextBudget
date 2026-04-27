# Compressor: coverage

_Generated 2026-04-27 08:11 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `coverage_typical` | 738 | +39.3% (cold 1.23 ms, warm 0.30 ms) | +73.2% (cold 0.22 ms, warm 0.21 ms) | +95.8% (cold 0.17 ms, warm 0.16 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`coverage.json`](./coverage.json) for the full structured payload.