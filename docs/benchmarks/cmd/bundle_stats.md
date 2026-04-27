# Compressor: bundle_stats

_Generated 2026-04-27 08:46 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `bundle_stats_webpack` | 794 | +56.4% (cold 0.15 ms, warm 0.07 ms) | +83.5% (cold 0.05 ms, warm 0.05 ms) | +97.7% (cold 0.05 ms, warm 0.04 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`bundle_stats.json`](./bundle_stats.json) for the full structured payload.