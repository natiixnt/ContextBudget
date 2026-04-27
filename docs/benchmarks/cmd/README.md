# Command output compressor benchmarks

Reduction and parse-time numbers for every registered cmd compressor,
measured on the shared M8/M9 fixture corpus. Reduction percentages are
from `compact` level (the typical agent default).

_Generated 2026-04-27 08:11 UTC_

| Schema | Fixtures | Avg raw tokens | Avg reduction (compact) | Avg cold parse | Avg warm parse |
|--------|----------|----------------|-------------------------|----------------|----------------|
| [cargo_test](./cargo_test.md) | 1 | 84 | +67.9% | 0.02 ms | 0.02 ms |
| [coverage](./coverage.md) | 1 | 738 | +73.2% | 0.22 ms | 0.21 ms |
| [docker](./docker.md) | 1 | 195 | +60.0% | 0.05 ms | 0.04 ms |
| [find](./find.md) | 2 | 1,705 | +19.8% | 0.40 ms | 0.40 ms |
| [git_diff](./git_diff.md) | 2 | 4,069 | +79.0% | 0.43 ms | 0.42 ms |
| [git_log](./git_log.md) | 1 | 64 | +78.1% | 0.02 ms | 0.01 ms |
| [git_status](./git_status.md) | 1 | 16 | +0.0% | 0.01 ms | 0.01 ms |
| [go_test](./go_test.md) | 1 | 42 | +69.0% | 0.01 ms | 0.01 ms |
| [grep](./grep.md) | 2 | 3,517 | +26.7% | 0.67 ms | 0.65 ms |
| [json_log](./json_log.md) | 1 | 6,014 | +91.2% | 1.62 ms | 1.60 ms |
| [kubectl_get](./kubectl_get.md) | 1 | 176 | +47.7% | 0.06 ms | 0.06 ms |
| [lint](./lint.md) | 2 | 962 | +76.7% | 0.31 ms | 0.30 ms |
| [ls](./ls.md) | 2 | 792 | +47.2% | 0.64 ms | 0.49 ms |
| [npm_test](./npm_test.md) | 1 | 68 | +48.5% | 0.02 ms | 0.02 ms |
| [pkg_install](./pkg_install.md) | 1 | 171 | +84.2% | 0.03 ms | 0.03 ms |
| [profiler](./profiler.md) | 1 | 2,366 | +89.2% | 0.42 ms | 0.42 ms |
| [pytest](./pytest.md) | 2 | 1,381 | +79.4% | 0.29 ms | 0.28 ms |
| [sql_explain](./sql_explain.md) | 1 | 421 | +70.3% | 0.14 ms | 0.13 ms |
| [tree](./tree.md) | 1 | 10 | -30.0% | 0.02 ms | 0.01 ms |

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