# Compressor: pytest

_Generated 2026-04-26 19:55 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `pytest_small` | 208 | +64.9% (cold 0.15 ms, warm 0.05 ms) | +74.5% (cold 0.05 ms, warm 0.04 ms) | +90.4% (cold 0.04 ms, warm 0.03 ms) |
| `pytest_massive` | 2,555 | +55.3% (cold 1.04 ms, warm 0.46 ms) | +73.8% (cold 0.44 ms, warm 0.44 ms) | +99.2% (cold 0.31 ms, warm 0.33 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`pytest.json`](./pytest.json) for the full structured payload.