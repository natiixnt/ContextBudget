# Compressor: docker

_Generated 2026-04-27 08:46 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `docker_build_typical` | 195 | +60.0% (cold 0.11 ms, warm 0.05 ms) | +60.0% (cold 0.05 ms, warm 0.04 ms) | +89.2% (cold 0.04 ms, warm 0.04 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`docker.json`](./docker.json) for the full structured payload.