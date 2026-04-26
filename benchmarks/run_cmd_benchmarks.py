#!/usr/bin/env python3
"""Run cmd-compressor benchmarks and emit JSON + Markdown artifacts.

Companion to ``run_benchmarks.py`` (which benchmarks the file-context engine).
This script runs the M9 harness over every registered cmd compressor on the
shared M8/M9 fixture corpus and writes:

- ``docs/benchmarks/cmd/README.md``        - per-schema summary table
- ``docs/benchmarks/cmd/<schema>.md``      - one report per registered schema
- ``docs/benchmarks/cmd/<schema>.json``    - structured data for the same

Usage
-----
    python benchmarks/run_cmd_benchmarks.py

Options (env vars)
------------------
    CMD_BENCHMARK_OUT_DIR    Output directory (default: docs/benchmarks/cmd)
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Allow importing redcon and tests from the repo root without installation.
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

from redcon.cmd.benchmark import Benchmark, run_benchmarks  # noqa: E402

OUT_DIR: Path = Path(
    os.getenv("CMD_BENCHMARK_OUT_DIR", str(_repo_root / "docs" / "benchmarks" / "cmd"))
)


def _load_cases() -> list[tuple]:
    """Reuse the M8 fixture corpus so quality and benchmark stay in lock-step."""
    from tests.test_cmd_quality import CASES  # type: ignore

    return list(CASES)


def _group_by_schema(results: list[Benchmark]) -> dict[str, list[Benchmark]]:
    groups: dict[str, list[Benchmark]] = defaultdict(list)
    for benchmark in results:
        groups[benchmark.schema].append(benchmark)
    return dict(groups)


def _render_summary(results: list[Benchmark], generated_at: str) -> str:
    by_schema = _group_by_schema(results)
    lines = [
        "# Command output compressor benchmarks",
        "",
        "Reduction and parse-time numbers for every registered cmd compressor,",
        "measured on the shared M8/M9 fixture corpus. Reduction percentages are",
        "from `compact` level (the typical agent default).",
        "",
        f"_Generated {generated_at}_",
        "",
        "| Schema | Fixtures | Avg raw tokens | Avg reduction (compact) | "
        "Avg cold parse | Avg warm parse |",
        "|--------|----------|----------------|-------------------------|"
        "----------------|----------------|",
    ]
    for schema in sorted(by_schema):
        bench_list = by_schema[schema]
        avg_raw = int(statistics.mean(b.raw_tokens for b in bench_list))
        compact_results = [
            level for b in bench_list for level in b.levels if level.level == "compact"
        ]
        if not compact_results:
            continue
        avg_red = statistics.mean(level.reduction_pct for level in compact_results)
        avg_cold = statistics.mean(level.cold_seconds for level in compact_results) * 1000
        avg_warm = statistics.mean(level.warm_seconds for level in compact_results) * 1000
        lines.append(
            f"| [{schema}](./{schema}.md) "
            f"| {len(bench_list)} "
            f"| {avg_raw:,} "
            f"| {avg_red:+.1f}% "
            f"| {avg_cold:.2f} ms "
            f"| {avg_warm:.2f} ms |"
        )
    lines += [
        "",
        "## How to reproduce",
        "",
        "```bash",
        "python benchmarks/run_cmd_benchmarks.py",
        "```",
        "",
        "Or run the harness directly:",
        "",
        "```bash",
        "redcon cmd-bench           # markdown table to stdout",
        "redcon cmd-bench --json    # JSON suitable for CI baselines",
        "```",
        "",
        "## Methodology",
        "",
        "The benchmark times each compressor on every fixture at all three",
        "compression levels (verbose / compact / ultra). Cold timings reflect",
        "the first call after the parser is loaded; warm timings are the mean",
        "of five subsequent calls on the same input.",
        "",
        "Reductions and durations are deterministic. The same fixture corpus",
        "powers the M8 quality gate, which independently asserts that every",
        "compressor preserves required information at compact and verbose",
        "levels and that reduction stays above per-level floors.",
    ]
    return "\n".join(lines)


def _render_schema_report(schema: str, bench_list: list[Benchmark], generated_at: str) -> str:
    lines = [
        f"# Compressor: {schema}",
        "",
        f"_Generated {generated_at}_",
        "",
        "| Fixture | Raw tokens | Verbose | Compact | Ultra |",
        "|---------|-----------:|---------|---------|-------|",
    ]
    for benchmark in bench_list:
        cells: list[str] = []
        for level_name in ("verbose", "compact", "ultra"):
            level = next(
                (level for level in benchmark.levels if level.level == level_name), None
            )
            if level is None:
                cells.append("-")
                continue
            cells.append(
                f"{level.reduction_pct:+.1f}% "
                f"(cold {level.cold_seconds * 1000:.2f} ms, "
                f"warm {level.warm_seconds * 1000:.2f} ms)"
            )
        lines.append(
            f"| `{benchmark.fixture}` "
            f"| {benchmark.raw_tokens:,} "
            f"| {cells[0]} "
            f"| {cells[1]} "
            f"| {cells[2]} |"
        )

    lines += [
        "",
        "## Notes",
        "",
        "- Negative reductions on small fixtures (under ~80 raw tokens) are",
        "  expected: the format header dominates and the M8 quality gate",
        "  exempts these from the reduction floor check.",
        "- Ultra is by design lossy; it summarises rather than preserving",
        "  every entry. The M8 quality gate enforces information preservation",
        "  only at compact and verbose levels.",
        "",
        "## Raw data",
        "",
        f"See [`{schema}.json`](./{schema}.json) for the full structured payload.",
    ]
    return "\n".join(lines)


def _benchmark_to_payload(benchmark: Benchmark) -> dict:
    return {
        "schema": benchmark.schema,
        "fixture": benchmark.fixture,
        "raw_bytes": benchmark.raw_bytes,
        "raw_tokens": benchmark.raw_tokens,
        "levels": [
            {
                "level": level.level,
                "compressed_tokens": level.compressed_tokens,
                "reduction_pct": level.reduction_pct,
                "cold_seconds": level.cold_seconds,
                "warm_seconds": level.warm_seconds,
                "must_preserve_ok": level.must_preserve_ok,
            }
            for level in benchmark.levels
        ],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = _load_cases()
    print(f"Redcon cmd-compressor benchmark runner")
    print(f"  cases  : {len(cases)}")
    print(f"  output : {OUT_DIR}")
    print()

    results = run_benchmarks(cases)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_schema = _group_by_schema(results)

    for schema, bench_list in sorted(by_schema.items()):
        json_path = OUT_DIR / f"{schema}.json"
        md_path = OUT_DIR / f"{schema}.md"
        json_path.write_text(
            json.dumps(
                {
                    "schema": schema,
                    "generated_at": generated_at,
                    "benchmarks": [_benchmark_to_payload(b) for b in bench_list],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        md_path.write_text(
            _render_schema_report(schema, bench_list, generated_at), encoding="utf-8"
        )
        compact_results = [
            level for b in bench_list for level in b.levels if level.level == "compact"
        ]
        avg_red = statistics.mean(level.reduction_pct for level in compact_results)
        avg_warm = statistics.mean(level.warm_seconds for level in compact_results) * 1000
        print(
            f"[{schema}] {len(bench_list)} fixtures, "
            f"compact avg reduction {avg_red:+.1f}%, warm parse {avg_warm:.2f} ms"
        )
        print(f"  -> {md_path.relative_to(_repo_root)}")

    summary_path = OUT_DIR / "README.md"
    summary_path.write_text(_render_summary(results, generated_at), encoding="utf-8")
    print()
    print(f"Summary written to {summary_path.relative_to(_repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
