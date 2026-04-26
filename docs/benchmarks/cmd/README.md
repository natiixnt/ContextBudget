# Command output compressor benchmarks

Reduction and parse-time numbers for every registered cmd compressor,
measured on the shared M8/M9 fixture corpus. Reduction percentages are
from `compact` level (the typical agent default).

_Generated 2026-04-26 19:55 UTC_

| Schema | Fixtures | Avg raw tokens | Avg reduction (compact) | Avg cold parse | Avg warm parse |
|--------|----------|----------------|-------------------------|----------------|----------------|
| [cargo_test](./cargo_test.md) | 1 | 84 | +66.7% | 0.02 ms | 0.02 ms |
| [find](./find.md) | 2 | 1,705 | +19.8% | 0.32 ms | 0.32 ms |
| [git_diff](./git_diff.md) | 2 | 4,069 | +78.0% | 0.48 ms | 0.43 ms |
| [git_log](./git_log.md) | 1 | 64 | +78.1% | 0.02 ms | 0.01 ms |
| [git_status](./git_status.md) | 1 | 16 | -25.0% | 0.01 ms | 0.02 ms |
| [go_test](./go_test.md) | 1 | 42 | +69.0% | 0.01 ms | 0.01 ms |
| [grep](./grep.md) | 2 | 3,517 | +17.4% | 0.69 ms | 0.66 ms |
| [ls](./ls.md) | 2 | 792 | +47.2% | 0.43 ms | 0.42 ms |
| [npm_test](./npm_test.md) | 1 | 68 | +47.1% | 0.02 ms | 0.02 ms |
| [pytest](./pytest.md) | 2 | 1,381 | +74.2% | 0.24 ms | 0.24 ms |
| [tree](./tree.md) | 1 | 10 | -30.0% | 0.01 ms | 0.01 ms |

## How to reproduce

```bash
python benchmarks/run_cmd_benchmarks.py
```

Or run the harness directly:

```bash
redcon cmd-bench           # markdown table to stdout
redcon cmd-bench --json    # JSON suitable for CI baselines
```

## Methodology

The benchmark times each compressor on every fixture at all three
compression levels (verbose / compact / ultra). Cold timings reflect
the first call after the parser is loaded; warm timings are the mean
of five subsequent calls on the same input.

Reductions and durations are deterministic. The same fixture corpus
powers the M8 quality gate, which independently asserts that every
compressor preserves required information at compact and verbose
levels and that reduction stays above per-level floors.