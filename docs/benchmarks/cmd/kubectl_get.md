# Compressor: kubectl_get

_Generated 2026-04-27 08:11 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `kubectl_pods_typical` | 176 | +47.7% (cold 0.35 ms, warm 0.06 ms) | +47.7% (cold 0.06 ms, warm 0.06 ms) | +92.0% (cold 0.05 ms, warm 0.04 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`kubectl_get.json`](./kubectl_get.json) for the full structured payload.