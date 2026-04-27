# Compressor: git_status

_Generated 2026-04-27 08:46 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `git_status` | 16 | +0.0% (cold 0.13 ms, warm 0.01 ms) | +0.0% (cold 0.01 ms, warm 0.01 ms) | +0.0% (cold 0.02 ms, warm 0.01 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`git_status.json`](./git_status.json) for the full structured payload.