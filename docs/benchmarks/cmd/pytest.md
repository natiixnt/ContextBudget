# Compressor: pytest

_Generated 2026-04-27 08:11 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `pytest_small` | 208 | +66.3% (cold 0.13 ms, warm 0.04 ms) | +75.0% (cold 0.04 ms, warm 0.04 ms) | +90.4% (cold 0.04 ms, warm 0.03 ms) |
| `pytest_massive` | 2,555 | +57.4% (cold 1.04 ms, warm 0.46 ms) | +83.8% (cold 0.53 ms, warm 0.51 ms) | +99.2% (cold 0.32 ms, warm 0.31 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`pytest.json`](./pytest.json) for the full structured payload.