# Compressor: lint

_Generated 2026-04-27 08:11 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `mypy_large` | 732 | +13.4% (cold 0.84 ms, warm 0.28 ms) | +70.6% (cold 0.28 ms, warm 0.27 ms) | +98.4% (cold 0.19 ms, warm 0.19 ms) |
| `ruff_typical` | 1,192 | -1.3% (cold 0.93 ms, warm 0.38 ms) | +82.7% (cold 0.34 ms, warm 0.34 ms) | +99.0% (cold 0.27 ms, warm 0.27 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`lint.json`](./lint.json) for the full structured payload.