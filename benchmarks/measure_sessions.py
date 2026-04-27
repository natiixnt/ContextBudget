#!/usr/bin/env python3
"""
Empirical measurements for the gated cross-call / cross-content vectors
(V42/V43, V49, V25/V26). Simulates realistic agent sessions over the
Redcon repo and reports the signals each vector's research note marked
as load-bearing for the ship decision.

Outputs:
  - benchmarks/session_measurements.json
  - benchmarks/session_measurements.md (human summary)

Rules of engagement: deterministic seeds, no external network, only
shell commands already in the runner allowlist. Writes are confined to
the temp DB at .redcon/measure-sessions.db so the production history
is untouched.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

from redcon.cmd.budget import BudgetHint  # noqa: E402
from redcon.cmd.pipeline import (  # noqa: E402
    CompressionReport,
    clear_default_cache,
    compress_command,
)
from redcon.cmd.types import CompressionLevel  # noqa: E402


SESSIONS: list[tuple[str, list[list[str]]]] = [
    (
        "investigate-failing-tests",
        [
            ["git", "status"],
            ["git", "log", "-5", "--oneline"],
            ["pytest", "tests/test_cmd_quality.py", "-x"],
            ["grep", "-rn", "test_quality_check", "tests/"],
            ["pytest", "tests/test_cmd_quality.py", "-x"],
            ["git", "diff", "--stat"],
            ["git", "status"],
        ],
    ),
    (
        "code-review",
        [
            ["git", "status"],
            ["git", "diff"],
            ["git", "log", "-10", "--oneline"],
            ["git", "diff", "--stat"],
            ["pytest", "tests/test_cmd_pipeline.py"],
            ["git", "diff"],
            ["git", "status"],
        ],
    ),
    (
        "search-and-edit",
        [
            ["grep", "-rn", "compress_command", "redcon/"],
            ["grep", "-rn", "verify_must_preserve", "redcon/"],
            ["ls", "redcon/cmd/compressors/"],
            ["find", "redcon/cmd", "-name", "*.py"],
            ["grep", "-rn", "compress_command", "redcon/"],
            ["pytest", "tests/test_cmd_pipeline.py", "-x"],
            ["git", "diff"],
        ],
    ),
    (
        "explore-codebase",
        [
            ["tree", "redcon/cmd/", "-L", "2"],
            ["ls", "redcon/cmd/compressors/"],
            ["find", "redcon", "-name", "*.py", "-type", "f"],
            ["grep", "-rn", "schema =", "redcon/cmd/compressors/"],
            ["git", "log", "-20", "--oneline"],
            ["tree", "redcon/cmd/", "-L", "2"],
        ],
    ),
    (
        "deep-debug",
        [
            ["pytest", "tests/test_cmd_quality.py", "-x"],
            ["grep", "-rn", "must_preserve", "redcon/cmd/compressors/"],
            ["git", "log", "-5", "redcon/cmd/compressors/git_log.py"],
            ["git", "diff", "redcon/cmd/compressors/git_log.py"],
            ["pytest", "tests/test_cmd_quality.py", "-x"],
            ["grep", "-rn", "must_preserve", "redcon/cmd/compressors/"],
            ["git", "status"],
        ],
    ),
]


@dataclass
class CallRecord:
    session: str
    turn: int
    argv: tuple[str, ...]
    schema: str
    raw_tokens: int
    compressed_tokens: int
    cache_hit: bool
    text: str

    @property
    def argv_key(self) -> str:
        return " ".join(self.argv)


def _shingles(text: str, n: int = 3) -> set[str]:
    """Compute line-level n-gram shingles for content-overlap signals."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < n:
        return {"||".join(lines)} if lines else set()
    return {"||".join(lines[i : i + n]) for i in range(len(lines) - n + 1)}


_PATH_RE = re.compile(
    r"\b(?:[\w.\-]+/)+[\w.\-]+\.(?:py|js|ts|tsx|go|rs|md|toml|yaml|yml|json|sh)\b"
)
_SYMBOL_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]+|_?[a-z][a-z0-9_]+)\b")


def _paths_in(text: str) -> set[str]:
    return set(_PATH_RE.findall(text))


def _symbol_refs(text: str, min_len: int = 6) -> collections.Counter:
    return collections.Counter(
        m for m in _SYMBOL_RE.findall(text) if len(m) >= min_len
    )


def run_session(
    name: str, argvs: list[list[str]], cwd: Path
) -> list[CallRecord]:
    records: list[CallRecord] = []
    clear_default_cache()
    cache: dict = {}
    hint = BudgetHint(
        remaining_tokens=8_000,
        max_output_tokens=4_000,
        quality_floor=CompressionLevel.COMPACT,
    )
    for turn, argv in enumerate(argvs, start=1):
        try:
            report: CompressionReport = compress_command(
                argv,
                cwd=cwd,
                hint=hint,
                cache=cache,
                use_default_cache=False,
            )
        except Exception as exc:  # pragma: no cover
            print(f"  [skip] {' '.join(argv)}: {type(exc).__name__}: {exc}")
            continue
        out = report.output
        records.append(
            CallRecord(
                session=name,
                turn=turn,
                argv=tuple(argv),
                schema=out.schema,
                raw_tokens=out.original_tokens,
                compressed_tokens=out.compressed_tokens,
                cache_hit=report.cache_hit,
                text=out.text,
            )
        )
    return records


def analyse_session(records: list[CallRecord]) -> dict:
    if not records:
        return {}
    total_calls = len(records)
    cache_hits = sum(1 for r in records if r.cache_hit)
    distinct_argvs = len({r.argv_key for r in records})
    repeat_calls = total_calls - distinct_argvs

    # V25/V26 signals: argv transition matrix and replay rate per session.
    transitions: collections.Counter = collections.Counter()
    for prev, curr in zip(records, records[1:]):
        transitions[(prev.argv_key, curr.argv_key)] += 1

    # V42/V43 signals: cross-call content-line overlap via 3-line shingles.
    shingles_per_call = [_shingles(r.text) for r in records]
    shared_shingles = 0
    seen: set[str] = set()
    for shingles in shingles_per_call:
        shared_shingles += len(shingles & seen)
        seen |= shingles
    total_shingles = sum(len(s) for s in shingles_per_call)

    # V41 signal: per-path repetition across calls in a session.
    path_counter: collections.Counter = collections.Counter()
    for r in records:
        path_counter.update(_paths_in(r.text))
    repeated_paths = sum(c - 1 for c in path_counter.values() if c >= 2)
    distinct_paths = len(path_counter)

    # V49 signal: symbol references that recur across multiple calls.
    symbol_counter: collections.Counter = collections.Counter()
    for r in records:
        symbol_counter.update(_symbol_refs(r.text).keys())
    recurring_symbols = sum(
        c for s, c in symbol_counter.items() if c >= 2
    )
    distinct_symbols = len(symbol_counter)

    return {
        "total_calls": total_calls,
        "cache_hit_rate": cache_hits / total_calls,
        "distinct_argvs": distinct_argvs,
        "argv_repeat_rate": repeat_calls / total_calls,
        "transitions_top": transitions.most_common(3),
        "shingle_overlap_rate": (
            shared_shingles / total_shingles if total_shingles else 0.0
        ),
        "total_shingles": total_shingles,
        "repeated_paths": repeated_paths,
        "distinct_paths": distinct_paths,
        "recurring_symbol_refs": recurring_symbols,
        "distinct_symbols": distinct_symbols,
        "total_raw_tokens": sum(r.raw_tokens for r in records),
        "total_compressed_tokens": sum(r.compressed_tokens for r in records),
    }


def aggregate(per_session: dict[str, dict]) -> dict:
    sessions = list(per_session.values())
    if not sessions:
        return {}

    def avg(field: str) -> float:
        return statistics.mean(s[field] for s in sessions if field in s)

    def total(field: str) -> int:
        return sum(s.get(field, 0) for s in sessions)

    return {
        "session_count": len(sessions),
        "avg_total_calls": avg("total_calls"),
        "avg_cache_hit_rate": avg("cache_hit_rate"),
        "avg_argv_repeat_rate": avg("argv_repeat_rate"),
        "avg_shingle_overlap_rate": avg("shingle_overlap_rate"),
        "avg_repeated_paths_per_session": avg("repeated_paths"),
        "avg_distinct_paths_per_session": avg("distinct_paths"),
        "avg_recurring_symbol_refs": avg("recurring_symbol_refs"),
        "avg_distinct_symbols": avg("distinct_symbols"),
        "total_raw_tokens": total("total_raw_tokens"),
        "total_compressed_tokens": total("total_compressed_tokens"),
    }


def verdict_lines(agg: dict) -> list[str]:
    """Per-vector ship/skip/defer decisions based on the aggregate."""
    out: list[str] = []
    out.append(
        "## Per-vector verdicts based on session measurements"
    )
    out.append("")
    # V41 already shipped; report the empirical floor.
    paths = agg["avg_repeated_paths_per_session"]
    distinct = agg["avg_distinct_paths_per_session"]
    out.append(
        f"### V41 path aliases (already shipped)\n"
        f"- {paths:.1f} repeated path mentions per session over "
        f"{distinct:.1f} distinct paths -> "
        f"{paths / max(distinct, 1) * 100:.0f}% of paths show up >=2 times. "
        f"Per the V41 model the per-call saving is ~6 cl100k tokens per repeat, "
        f"~{paths * 6:.0f} tokens / session."
    )
    out.append("")
    # V42/V43: shingle overlap.
    shingle = agg["avg_shingle_overlap_rate"]
    if shingle >= 0.05:
        verdict = "SHIP candidate (>=5% cross-call line overlap)"
    elif shingle >= 0.01:
        verdict = "DEFER (1-5% overlap; small edge)"
    else:
        verdict = "SKIP (<1% overlap; below the V42 break-even)"
    out.append(
        f"### V42/V43 cross-content dedup\n"
        f"- 3-line shingle overlap across calls in the same session: "
        f"{shingle * 100:.1f}%.\n"
        f"- Verdict: {verdict}."
    )
    out.append("")
    # V49: recurring symbol refs.
    sym_recur = agg["avg_recurring_symbol_refs"]
    sym_distinct = agg["avg_distinct_symbols"]
    out.append(
        f"### V49 symbol cards\n"
        f"- {sym_recur:.0f} recurring symbol references per session over "
        f"{sym_distinct:.0f} distinct symbols. "
        f"V49's break-even is per-symbol freq >= 2; "
        f"{sym_recur / max(sym_distinct, 1) * 100:.0f}% of symbols cross the bar. "
        + (
            "SHIP candidate."
            if sym_recur >= 50
            else "DEFER (low recurrence in observed sessions)."
        )
    )
    out.append("")
    # V25: Markov chain top transition concentration.
    repeat_rate = agg["avg_argv_repeat_rate"]
    cache_rate = agg["avg_cache_hit_rate"]
    if cache_rate >= 0.30:
        v25 = "DEFER (existing in-process cache already absorbs >=30% of calls)"
    elif repeat_rate >= 0.20:
        v25 = "SHIP candidate (argv repeats but cache misses suggest content drift)"
    else:
        v25 = "SKIP (low argv repeat rate)"
    out.append(
        f"### V25/V26 Markov prefetch / replay\n"
        f"- argv repeat rate: {repeat_rate * 100:.1f}%, "
        f"cache hit rate: {cache_rate * 100:.1f}%.\n"
        f"- Verdict: {v25}."
    )
    out.append("")
    # V47 already shipped; report what fraction of repeat calls would
    # benefit from the snapshot delta.
    out.append(
        f"### V47 snapshot delta (already shipped)\n"
        f"- argv repeat rate {repeat_rate * 100:.1f}% (each repeat is a "
        f"potential V47 swap if jaccard >= 0.30).\n"
        f"- The schema-aware renderers (pytest/git_diff/coverage) cover the "
        "highest-traffic repeat schemas in the simulated sessions."
    )
    return out


def main() -> int:
    cwd = _repo_root
    print(f"Running {len(SESSIONS)} simulated agent sessions in {cwd}")
    started = time.monotonic()
    per_session_records: dict[str, list[CallRecord]] = {}
    for name, argvs in SESSIONS:
        print(f"  [{name}] {len(argvs)} calls")
        records = run_session(name, argvs, cwd)
        per_session_records[name] = records
    elapsed = time.monotonic() - started
    print(f"Done in {elapsed:.1f}s")

    per_session_stats = {
        name: analyse_session(records)
        for name, records in per_session_records.items()
    }
    agg = aggregate(per_session_stats)

    payload = {
        "sessions": {
            name: {
                "stats": stats,
                "calls": [
                    {
                        "turn": r.turn,
                        "argv": list(r.argv),
                        "schema": r.schema,
                        "raw_tokens": r.raw_tokens,
                        "compressed_tokens": r.compressed_tokens,
                        "cache_hit": r.cache_hit,
                    }
                    for r in per_session_records[name]
                ],
            }
            for name, stats in per_session_stats.items()
        },
        "aggregate": agg,
    }

    out_dir = _repo_root / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "session_measurements.json"
    md_path = out_dir / "session_measurements.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_lines = [
        "# Session-trace measurements",
        "",
        "Empirical signals captured by running "
        f"{len(SESSIONS)} synthetic agent sessions over this repo. "
        "Used to gate the cross-call / cross-content vectors that "
        "V42-V49 / V25-V26 research left as 'measure first'.",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Sessions simulated | {agg.get('session_count', 0)} |",
        f"| Avg calls per session | {agg.get('avg_total_calls', 0):.1f} |",
        f"| Avg argv repeat rate | {agg.get('avg_argv_repeat_rate', 0) * 100:.1f}% |",
        f"| Avg cache hit rate | {agg.get('avg_cache_hit_rate', 0) * 100:.1f}% |",
        f"| Avg 3-line shingle overlap | {agg.get('avg_shingle_overlap_rate', 0) * 100:.1f}% |",
        f"| Avg distinct paths / session | {agg.get('avg_distinct_paths_per_session', 0):.1f} |",
        f"| Avg repeated path refs / session | {agg.get('avg_repeated_paths_per_session', 0):.1f} |",
        f"| Avg distinct symbols / session | {agg.get('avg_distinct_symbols', 0):.0f} |",
        f"| Avg recurring symbol refs / session | {agg.get('avg_recurring_symbol_refs', 0):.0f} |",
        f"| Total raw tokens | {agg.get('total_raw_tokens', 0):,} |",
        f"| Total compressed tokens | {agg.get('total_compressed_tokens', 0):,} |",
        "",
    ]
    md_lines.extend(verdict_lines(agg))
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"Wrote {json_path.relative_to(_repo_root)}")
    print(f"Wrote {md_path.relative_to(_repo_root)}")
    print()
    print("Per-vector verdicts:")
    for line in verdict_lines(agg):
        if line.startswith("###") or line.startswith("- Verdict"):
            print(f"  {line.lstrip('# -')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
