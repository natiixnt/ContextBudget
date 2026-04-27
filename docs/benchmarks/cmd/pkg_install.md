# Compressor: pkg_install

_Generated 2026-04-27 08:11 UTC_

| Fixture | Raw tokens | Verbose | Compact | Ultra |
|---------|-----------:|---------|---------|-------|
| `pip_install_typical` | 171 | +84.2% (cold 0.12 ms, warm 0.03 ms) | +84.2% (cold 0.03 ms, warm 0.03 ms) | +97.1% (cold 0.02 ms, warm 0.02 ms) |

## Notes

- Negative reductions on small fixtures (under ~80 raw tokens) are
  expected: the format header dominates and the M8 quality gate
  exempts these from the reduction floor check.
- Ultra is by design lossy; it summarises rather than preserving
  every entry. The M8 quality gate enforces information preservation
  only at compact and verbose levels.

## Raw data

See [`pkg_install.json`](./pkg_install.json) for the full structured payload.