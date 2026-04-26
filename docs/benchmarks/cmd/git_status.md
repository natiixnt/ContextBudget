# Compressor: git_status

_Generated 2026-04-26 19:55 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `git_status` | 16 | -25.0% (cold 0.11 ms, warm 0.01 ms) | -25.0% (cold 0.01 ms, warm 0.02 ms) | -75.0% (cold 0.03 ms, warm 0.01 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`git_status.json`](./git_status.json) for the full structured payload.