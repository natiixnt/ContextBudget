"""
Benchmark harness for command-output compressors.

Times each compressor on a fixture at every level and reports:
  - cold-start parse + format duration
  - warm-call duration (second invocation, regex cache + bytecode warmed)
  - raw vs compressed tokens, reduction percentage

Used by tests/test_cmd_benchmark.py and as a CLI:
  python -m redcon.cmd.benchmark --json
  python -m redcon.cmd.benchmark --md  # default

The harness loads the same fixture corpus as the quality gate (M8) so
adding a fixture in tests/test_cmd_quality.py automatically benchmarks
it. This keeps quality + perf in lock-step rather than drifting.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import Compressor, CompressorContext
from redcon.cmd.types import CompressionLevel


@dataclass(frozen=True, slots=True)
class LevelBenchmark:
    level: str
    compressed_tokens: int
    reduction_pct: float
    cold_seconds: float
    warm_seconds: float
    must_preserve_ok: bool


@dataclass(frozen=True, slots=True)
class Benchmark:
    schema: str
    fixture: str
    raw_bytes: int
    raw_tokens: int
    levels: tuple[LevelBenchmark, ...]


def run_benchmark(
    fixture_name: str,
    compressor: Compressor,
    raw_stdout: bytes,
    raw_stderr: bytes,
    argv: tuple[str, ...],
    *,
    warm_iterations: int = 5,
) -> Benchmark:
    """Run one fixture through one compressor at all three levels."""
    raw_tokens = 0
    levels: list[LevelBenchmark] = []
    for level in (
        CompressionLevel.VERBOSE,
        CompressionLevel.COMPACT,
        CompressionLevel.ULTRA,
    ):
        ctx = CompressorContext(
            argv=argv,
            cwd=".",
            returncode=0,
            hint=_force_level(level),
        )
        # Cold: single timed call
        cold_start = time.perf_counter()
        cold_out = compressor.compress(raw_stdout, raw_stderr, ctx)
        cold_dur = time.perf_counter() - cold_start
        # Warm: average of `warm_iterations` calls after the first
        warm_total = 0.0
        for _ in range(warm_iterations):
            t0 = time.perf_counter()
            compressor.compress(raw_stdout, raw_stderr, ctx)
            warm_total += time.perf_counter() - t0
        warm_avg = warm_total / max(1, warm_iterations)
        if raw_tokens == 0:
            raw_tokens = cold_out.original_tokens
        levels.append(
            LevelBenchmark(
                level=level.value,
                compressed_tokens=cold_out.compressed_tokens,
                reduction_pct=round(cold_out.reduction_pct, 2),
                cold_seconds=round(cold_dur, 6),
                warm_seconds=round(warm_avg, 6),
                must_preserve_ok=cold_out.must_preserve_ok,
            )
        )
    return Benchmark(
        schema=compressor.schema,
        fixture=fixture_name,
        raw_bytes=len(raw_stdout) + len(raw_stderr),
        raw_tokens=raw_tokens,
        levels=tuple(levels),
    )


def run_benchmarks(cases: list[tuple]) -> list[Benchmark]:
    """Run a list of (name, compressor, raw_stdout, raw_stderr, argv) cases."""
    out: list[Benchmark] = []
    for name, compressor, stdout, stderr, argv in cases:
        out.append(run_benchmark(name, compressor, stdout, stderr, argv))
    return out


def render_json(results: list[Benchmark]) -> str:
    """Stable JSON view of the benchmark run, suitable for CI baselines."""
    payload = [_to_dict(b) for b in results]
    return json.dumps(payload, indent=2, sort_keys=False)


def render_markdown(results: list[Benchmark]) -> str:
    """Human-readable markdown table grouped by fixture."""
    lines: list[str] = ["# Compressor benchmark", ""]
    lines.append(
        "| fixture | schema | level | raw_tok | comp_tok | reduction | cold (ms) | warm (ms) |"
    )
    lines.append(
        "|---------|--------|-------|---------|----------|-----------|-----------|-----------|"
    )
    for b in results:
        for lvl in b.levels:
            lines.append(
                f"| {b.fixture} | {b.schema} | {lvl.level} | "
                f"{b.raw_tokens} | {lvl.compressed_tokens} | "
                f"{lvl.reduction_pct:+.1f}% | "
                f"{lvl.cold_seconds * 1000:.2f} | "
                f"{lvl.warm_seconds * 1000:.2f} |"
            )
    lines.append("")
    lines.append(_summary_table(results))
    return "\n".join(lines)


def _summary_table(results: list[Benchmark]) -> str:
    """Per-schema averages: useful for spotting which compressor regressed."""
    by_schema: dict[str, list[LevelBenchmark]] = {}
    for b in results:
        by_schema.setdefault(b.schema, []).extend(b.levels)
    lines: list[str] = ["## Per-schema averages", ""]
    lines.append(
        "| schema | level | avg reduction | avg cold (ms) | avg warm (ms) |"
    )
    lines.append(
        "|--------|-------|---------------|---------------|---------------|"
    )
    for schema, entries in by_schema.items():
        for level_value in ("verbose", "compact", "ultra"):
            level_entries = [e for e in entries if e.level == level_value]
            if not level_entries:
                continue
            avg_red = sum(e.reduction_pct for e in level_entries) / len(level_entries)
            avg_cold = sum(e.cold_seconds for e in level_entries) / len(level_entries)
            avg_warm = sum(e.warm_seconds for e in level_entries) / len(level_entries)
            lines.append(
                f"| {schema} | {level_value} | {avg_red:+.1f}% | "
                f"{avg_cold * 1000:.2f} | {avg_warm * 1000:.2f} |"
            )
    return "\n".join(lines)


def _to_dict(b: Benchmark) -> dict:
    return {
        "schema": b.schema,
        "fixture": b.fixture,
        "raw_bytes": b.raw_bytes,
        "raw_tokens": b.raw_tokens,
        "levels": [asdict(level) for level in b.levels],
    }


def _force_level(level: CompressionLevel) -> BudgetHint:
    if level == CompressionLevel.VERBOSE:
        return BudgetHint(remaining_tokens=10**6, max_output_tokens=10**6)
    if level == CompressionLevel.COMPACT:
        return BudgetHint(
            remaining_tokens=200,
            max_output_tokens=4_000,
            quality_floor=CompressionLevel.COMPACT,
        )
    return BudgetHint(remaining_tokens=10, max_output_tokens=2)


def _default_cases() -> list[tuple]:
    """Reuse the M8 fixture corpus so benchmarks track quality coverage."""
    from tests.test_cmd_quality import CASES  # type: ignore

    return list(CASES)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run command-output compressor benchmarks.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    args = parser.parse_args(argv)

    results = run_benchmarks(_default_cases())
    text = render_json(results) if args.json else render_markdown(results)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
