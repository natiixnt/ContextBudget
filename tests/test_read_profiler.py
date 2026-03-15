from __future__ import annotations

import json
from pathlib import Path

import pytest

from redcon import RedconEngine
from redcon.core.read_profiler import (
    HIGH_COST_READ_THRESHOLD,
    UNNECESSARY_READ_MIN_TOKENS,
    UNNECESSARY_READ_SCORE_THRESHOLD,
    build_read_profile,
    read_profile_as_dict,
)
from redcon.core.render import render_read_profile_markdown


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _entry(
    path: str,
    *,
    original_tokens: int,
    compressed_tokens: int,
    strategy: str = "full",
    chunk_strategy: str = "full-file",
) -> dict:
    return {
        "path": path,
        "strategy": strategy,
        "chunk_strategy": chunk_strategy,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "cache_status": "",
        "selected_ranges": [],
    }


def _ranked(path: str, score: float) -> dict:
    return {
        "path": path,
        "score": score,
        "heuristic_score": score,
        "historical_score": 0.0,
        "reasons": [],
    }


def _run(entries: list[dict], ranked: list[dict] | None = None, duplicate_prevented: int = 0) -> dict:
    return {
        "command": "pack",
        "compressed_context": entries,
        "ranked_files": ranked or [],
        "budget": {
            "estimated_input_tokens": sum(e["compressed_tokens"] for e in entries),
            "estimated_saved_tokens": 0,
            "duplicate_reads_prevented": duplicate_prevented,
        },
        "cache": {},
    }


# ---------------------------------------------------------------------------
# Unit: basic detection
# ---------------------------------------------------------------------------


def test_read_profiler_counts_files_correctly() -> None:
    data = _run([
        _entry("a.py", original_tokens=100, compressed_tokens=100),
        _entry("b.py", original_tokens=200, compressed_tokens=150),
    ])
    report = build_read_profile(data)
    assert report.total_files_read == 2
    assert report.unique_files_read == 2
    assert report.duplicate_reads == 0


def test_read_profiler_detects_duplicate_reads() -> None:
    data = _run([
        _entry("a.py", original_tokens=300, compressed_tokens=300),
        _entry("a.py", original_tokens=300, compressed_tokens=300),  # duplicate
    ])
    report = build_read_profile(data)
    assert report.total_files_read == 2
    assert report.unique_files_read == 1
    assert report.duplicate_reads == 1
    assert report.tokens_wasted_duplicates == 300
    assert len(report.duplicate_files) == 1
    assert report.duplicate_files[0].path == "a.py"
    assert report.duplicate_files[0].read_count == 2


def test_read_profiler_detects_triple_duplicate() -> None:
    data = _run([
        _entry("x.py", original_tokens=100, compressed_tokens=100),
        _entry("x.py", original_tokens=100, compressed_tokens=100),
        _entry("x.py", original_tokens=100, compressed_tokens=100),
    ])
    report = build_read_profile(data)
    assert report.duplicate_reads == 2
    assert report.tokens_wasted_duplicates == 200


def test_read_profiler_detects_high_cost_reads() -> None:
    data = _run([
        _entry("big.py", original_tokens=HIGH_COST_READ_THRESHOLD + 1, compressed_tokens=200),
        _entry("small.py", original_tokens=50, compressed_tokens=50),
    ])
    report = build_read_profile(data)
    assert report.high_cost_reads == 1
    assert len(report.high_cost_files) == 1
    assert report.high_cost_files[0].path == "big.py"


def test_read_profiler_exact_threshold_not_high_cost() -> None:
    data = _run([
        _entry("borderline.py", original_tokens=HIGH_COST_READ_THRESHOLD - 1, compressed_tokens=100),
    ])
    report = build_read_profile(data)
    assert report.high_cost_reads == 0


def test_read_profiler_detects_unnecessary_reads() -> None:
    data = _run(
        entries=[
            _entry("low_rel.py", original_tokens=UNNECESSARY_READ_MIN_TOKENS + 50, compressed_tokens=100),
        ],
        ranked=[
            _ranked("low_rel.py", UNNECESSARY_READ_SCORE_THRESHOLD - 0.1),
        ],
    )
    report = build_read_profile(data)
    assert report.unnecessary_reads == 1
    assert report.unnecessary_files[0].path == "low_rel.py"
    assert report.tokens_wasted_unnecessary > 0


def test_read_profiler_high_score_is_not_unnecessary() -> None:
    data = _run(
        entries=[
            _entry("relevant.py", original_tokens=800, compressed_tokens=400),
        ],
        ranked=[
            _ranked("relevant.py", UNNECESSARY_READ_SCORE_THRESHOLD + 1.0),
        ],
    )
    report = build_read_profile(data)
    assert report.unnecessary_reads == 0


def test_read_profiler_tiny_file_not_flagged_unnecessary() -> None:
    # File below UNNECESSARY_READ_MIN_TOKENS should not be flagged even with low score
    data = _run(
        entries=[
            _entry("tiny.py", original_tokens=UNNECESSARY_READ_MIN_TOKENS - 1, compressed_tokens=10),
        ],
        ranked=[
            _ranked("tiny.py", 0.1),
        ],
    )
    report = build_read_profile(data)
    assert report.unnecessary_reads == 0


def test_read_profiler_no_score_not_flagged_unnecessary() -> None:
    # If a file has no score in ranked_files it should not be flagged unnecessary
    data = _run(
        entries=[
            _entry("unranked.py", original_tokens=800, compressed_tokens=400),
        ],
        ranked=[],  # no ranking info
    )
    report = build_read_profile(data)
    assert report.unnecessary_reads == 0


# ---------------------------------------------------------------------------
# Unit: waste token accounting
# ---------------------------------------------------------------------------


def test_read_profiler_total_waste_is_sum_of_parts() -> None:
    data = _run(
        entries=[
            # Duplicate (appears twice)
            _entry("dup.py", original_tokens=200, compressed_tokens=200),
            _entry("dup.py", original_tokens=200, compressed_tokens=200),
            # Unnecessary (low score, large file)
            _entry("unneeded.py", original_tokens=UNNECESSARY_READ_MIN_TOKENS + 100, compressed_tokens=50),
        ],
        ranked=[
            _ranked("dup.py", 2.0),
            _ranked("unneeded.py", 0.1),
        ],
    )
    report = build_read_profile(data)
    assert report.tokens_wasted_total == report.tokens_wasted_duplicates + report.tokens_wasted_unnecessary


def test_read_profiler_duplicate_reads_prevented_comes_from_budget() -> None:
    data = _run([], duplicate_prevented=7)
    report = build_read_profile(data)
    assert report.duplicate_reads_prevented == 7


# ---------------------------------------------------------------------------
# Unit: sorting
# ---------------------------------------------------------------------------


def test_read_profiler_files_sorted_by_original_tokens_descending() -> None:
    data = _run([
        _entry("small.py", original_tokens=50, compressed_tokens=50),
        _entry("big.py", original_tokens=900, compressed_tokens=400),
        _entry("medium.py", original_tokens=300, compressed_tokens=200),
    ])
    report = build_read_profile(data)
    sizes = [r.original_tokens for r in report.files]
    assert sizes == sorted(sizes, reverse=True)


# ---------------------------------------------------------------------------
# Unit: empty run
# ---------------------------------------------------------------------------


def test_read_profiler_empty_run() -> None:
    data = {"command": "pack", "compressed_context": [], "budget": {}}
    report = build_read_profile(data)
    assert report.total_files_read == 0
    assert report.unique_files_read == 0
    assert report.duplicate_reads == 0
    assert report.tokens_wasted_total == 0
    assert report.files == []


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_read_profile_as_dict_is_json_serializable() -> None:
    data = _run([
        _entry("a.py", original_tokens=300, compressed_tokens=300),
        _entry("a.py", original_tokens=300, compressed_tokens=300),
    ])
    report = build_read_profile(data)
    d = read_profile_as_dict(report)
    # Must not raise
    json.dumps(d)
    assert "total_files_read" in d
    assert "files" in d
    assert "duplicate_files" in d


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def test_render_read_profile_markdown_contains_required_sections() -> None:
    data = _run(
        entries=[
            _entry("dup.py", original_tokens=300, compressed_tokens=300),
            _entry("dup.py", original_tokens=300, compressed_tokens=300),
            _entry("big.py", original_tokens=HIGH_COST_READ_THRESHOLD + 50, compressed_tokens=200),
        ],
        ranked=[
            _ranked("dup.py", 2.5),
            _ranked("big.py", 3.0),
        ],
    )
    report = build_read_profile(data, run_json="run.json")
    d = read_profile_as_dict(report)
    md = render_read_profile_markdown(d)

    assert "# Redcon Agent Read Profile" in md
    assert "## Summary" in md
    assert "## Duplicate Reads" in md
    assert "## High Token-Cost Reads" in md
    assert "## All Files Read" in md
    assert "dup.py" in md
    assert "big.py" in md
    assert "run.json" in md


def test_render_read_profile_markdown_no_sections_when_clean() -> None:
    # A run with no duplicates, no high-cost, no unnecessary should still render summary
    data = _run([_entry("a.py", original_tokens=50, compressed_tokens=50)])
    report = build_read_profile(data)
    d = read_profile_as_dict(report)
    md = render_read_profile_markdown(d)

    assert "## Summary" in md
    # No problem sections should appear
    assert "## Duplicate Reads" not in md
    assert "## Unnecessary Reads" not in md
    assert "## High Token-Cost Reads" not in md


# ---------------------------------------------------------------------------
# Integration: real pack run → engine.read_profile()
# ---------------------------------------------------------------------------


def test_read_profile_from_real_pack_run(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "router.py", "\n".join(
        [f"def route_{i}(req): return '{i}'" for i in range(80)]
    ))
    _write(tmp_path / "src" / "tiny.py", "def ping(): return 'pong'\n")

    engine = RedconEngine()
    run = engine.pack(task="add auth to router", repo=tmp_path, max_tokens=800)
    report = engine.read_profile(run)

    assert report["total_files_read"] >= 1
    assert report["unique_files_read"] >= 1
    assert isinstance(report["files"], list)
    assert all("path" in r for r in report["files"])
    assert all("original_tokens" in r for r in report["files"])
    assert report["tokens_wasted_total"] >= 0


def test_read_profile_from_json_file(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "svc.py", "\n".join(
        [f"def op_{i}(): pass" for i in range(60)]
    ))
    engine = RedconEngine()
    run = engine.pack(task="optimize service", repo=tmp_path, max_tokens=400)

    run_json_path = tmp_path / "run.json"
    run_json_path.write_text(json.dumps(run, default=str), encoding="utf-8")

    report = engine.read_profile(run_json_path)
    assert report["run_json"] == str(run_json_path)
    assert report["total_files_read"] >= 1


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_read_profiler_writes_json_and_markdown(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "router.py", "\n".join(
        [f"def route_{i}(req): return '{i}'" for i in range(70)]
    ))
    engine = RedconEngine()
    run = engine.pack(task="add auth", repo=tmp_path, max_tokens=600)

    run_json_path = tmp_path / "run.json"
    run_json_path.write_text(json.dumps(run, default=str), encoding="utf-8")

    import os
    from redcon.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "read-profiler", str(run_json_path),
        "--out-prefix", str(tmp_path / "rp"),
    ])
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rc = args.func(args)
    finally:
        os.chdir(old_cwd)

    assert rc == 0
    assert (tmp_path / "rp.json").exists()
    assert (tmp_path / "rp.md").exists()

    out = json.loads((tmp_path / "rp.json").read_text(encoding="utf-8"))
    assert "total_files_read" in out
    assert "duplicate_reads" in out
    assert "tokens_wasted_total" in out
    assert "files" in out

    md = (tmp_path / "rp.md").read_text(encoding="utf-8")
    assert "# Redcon Agent Read Profile" in md
    assert "## Summary" in md
