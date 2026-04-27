# Compressor: sql_explain

_Generated 2026-04-27 08:11 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `sql_explain_postgres` | 421 | +31.1% (cold 0.28 ms, warm 0.15 ms) | +70.3% (cold 0.14 ms, warm 0.13 ms) | +93.6% (cold 0.13 ms, warm 0.12 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`sql_explain.json`](./sql_explain.json) for the full structured payload.