# Compressor: git_diff

_Generated 2026-04-27 08:11 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `git_diff_small` | 61 | +31.1% (cold 0.17 ms, warm 0.02 ms) | +60.7% (cold 0.03 ms, warm 0.02 ms) | +83.6% (cold 0.02 ms, warm 0.02 ms) |
| `git_diff_huge` | 8,078 | +31.6% (cold 1.06 ms, warm 0.99 ms) | +97.3% (cold 0.83 ms, warm 0.82 ms) | +99.5% (cold 0.81 ms, warm 0.81 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`git_diff.json`](./git_diff.json) for the full structured payload.