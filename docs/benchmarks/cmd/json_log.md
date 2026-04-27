# Compressor: json_log

_Generated 2026-04-27 08:46 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `json_log_typical` | 6,014 | +45.2% (cold 2.09 ms, warm 1.76 ms) | +91.2% (cold 1.69 ms, warm 1.61 ms) | +99.6% (cold 0.84 ms, warm 0.85 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`json_log.json`](./json_log.json) for the full structured payload.