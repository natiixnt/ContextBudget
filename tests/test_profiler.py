from __future__ import annotations

import json
from pathlib import Path

from redcon import RedconEngine
from redcon.core.profiler import (
    STAGE_CACHE_REUSE,
    STAGE_COMPRESSION,
    STAGE_FULL,
    STAGE_SLICING,
    STAGE_SYMBOL_EXTRACTION,
    build_savings_profile,
)
from redcon.core.render import render_profile_markdown


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_compressed_entry(
    path: str,
    *,
    strategy: str,
    chunk_strategy: str,
    original_tokens: int,
    compressed_tokens: int,
    cache_status: str = "stored",
) -> dict:
    return {
        "path": path,
        "strategy": strategy,
        "chunk_strategy": chunk_strategy,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "cache_status": cache_status,
        "selected_ranges": [],
    }


def _make_run_data(entries: list[dict]) -> dict:
    return {
        "command": "pack",
        "compressed_context": entries,
        "budget": {
            "estimated_input_tokens": sum(e["compressed_tokens"] for e in entries),
            "estimated_saved_tokens": sum(
                max(0, e["original_tokens"] - e["compressed_tokens"]) for e in entries
            ),
        },
        "cache": {},
    }


# ---------------------------------------------------------------------------
# Unit tests: stage classification
# ---------------------------------------------------------------------------


def test_profiler_attributes_full_strategy_to_full_stage() -> None:
    data = _make_run_data(
        [_make_compressed_entry("a.py", strategy="full", chunk_strategy="full-file", original_tokens=100, compressed_tokens=105)]
    )
    profile = build_savings_profile(data)
    assert profile.per_file[0].stage == STAGE_FULL
    assert profile.per_file[0].tokens_saved == 0


def test_profiler_attributes_symbol_strategy_to_symbol_stage() -> None:
    data = _make_run_data(
        [_make_compressed_entry("b.py", strategy="symbol", chunk_strategy="symbol-extract-python", original_tokens=400, compressed_tokens=120)]
    )
    profile = build_savings_profile(data)
    assert profile.per_file[0].stage == STAGE_SYMBOL_EXTRACTION
    assert profile.per_file[0].tokens_saved == 280


def test_profiler_attributes_slice_strategy_to_slicing_stage() -> None:
    data = _make_run_data(
        [_make_compressed_entry("c.py", strategy="slice", chunk_strategy="lang-python", original_tokens=300, compressed_tokens=80)]
    )
    profile = build_savings_profile(data)
    assert profile.per_file[0].stage == STAGE_SLICING
    assert profile.per_file[0].tokens_saved == 220


def test_profiler_attributes_summary_strategy_to_compression_stage() -> None:
    data = _make_run_data(
        [_make_compressed_entry("d.py", strategy="summary", chunk_strategy="summary-preview", original_tokens=700, compressed_tokens=60)]
    )
    profile = build_savings_profile(data)
    assert profile.per_file[0].stage == STAGE_COMPRESSION
    assert profile.per_file[0].tokens_saved == 640


def test_profiler_attributes_cache_reuse_regardless_of_strategy() -> None:
    data = _make_run_data(
        [_make_compressed_entry("e.py", strategy="slice", chunk_strategy="lang-go", original_tokens=200, compressed_tokens=40, cache_status="reused")]
    )
    profile = build_savings_profile(data)
    assert profile.per_file[0].stage == STAGE_CACHE_REUSE
    assert profile.per_file[0].tokens_saved == 160


def test_profiler_attributes_snippet_strategy_to_snippet_stage() -> None:
    from redcon.core.profiler import STAGE_SNIPPET
    data = _make_run_data(
        [_make_compressed_entry("f.py", strategy="snippet", chunk_strategy="snippet-keyword", original_tokens=250, compressed_tokens=90)]
    )
    profile = build_savings_profile(data)
    assert profile.per_file[0].stage == STAGE_SNIPPET
    assert profile.per_file[0].tokens_saved == 160


# ---------------------------------------------------------------------------
# Aggregation tests
# ---------------------------------------------------------------------------


def test_profiler_totals_match_sum_of_per_file_values() -> None:
    entries = [
        _make_compressed_entry("a.py", strategy="full", chunk_strategy="full-file", original_tokens=100, compressed_tokens=105),
        _make_compressed_entry("b.py", strategy="symbol", chunk_strategy="symbol-extract-python", original_tokens=400, compressed_tokens=120),
        _make_compressed_entry("c.py", strategy="summary", chunk_strategy="summary-preview", original_tokens=700, compressed_tokens=60),
    ]
    data = _make_run_data(entries)
    profile = build_savings_profile(data)

    assert profile.tokens_before == 1200
    assert profile.tokens_after == 285
    assert profile.tokens_saved == 915
    assert profile.savings_pct == round((915 / 1200) * 100, 1)


def test_profiler_by_stage_sums_correctly() -> None:
    entries = [
        _make_compressed_entry("a.py", strategy="symbol", chunk_strategy="symbol-extract-python", original_tokens=300, compressed_tokens=100),
        _make_compressed_entry("b.ts", strategy="symbol", chunk_strategy="symbol-extract-typescript", original_tokens=200, compressed_tokens=80),
        _make_compressed_entry("c.py", strategy="summary", chunk_strategy="summary-preview", original_tokens=600, compressed_tokens=50),
    ]
    data = _make_run_data(entries)
    profile = build_savings_profile(data)

    sym = profile.by_stage[STAGE_SYMBOL_EXTRACTION]
    assert sym.tokens_saved == 320
    assert sym.file_count == 2

    comp = profile.by_stage[STAGE_COMPRESSION]
    assert comp.tokens_saved == 550
    assert comp.file_count == 1


def test_profiler_savings_pct_is_zero_when_no_files() -> None:
    data = {"command": "pack", "compressed_context": [], "budget": {}}
    profile = build_savings_profile(data)
    assert profile.tokens_before == 0
    assert profile.tokens_after == 0
    assert profile.tokens_saved == 0
    assert profile.savings_pct == 0.0


def test_profiler_negative_savings_clamped_to_zero() -> None:
    # Compressed > original is a valid edge case when overhead is added
    data = _make_run_data(
        [_make_compressed_entry("a.py", strategy="full", chunk_strategy="full-file", original_tokens=10, compressed_tokens=15)]
    )
    profile = build_savings_profile(data)
    assert profile.per_file[0].tokens_saved == 0
    assert profile.tokens_saved == 0


def test_profiler_delta_savings_tracked_from_delta_budget() -> None:
    data = {
        "command": "pack",
        "compressed_context": [],
        "budget": {},
        "delta": {
            "budget": {
                "tokens_saved": 850,
                "delta_tokens": 150,
                "original_tokens": 1000,
            }
        },
    }
    profile = build_savings_profile(data)
    from redcon.core.profiler import STAGE_DELTA
    assert profile.by_stage[STAGE_DELTA].tokens_saved == 850


# ---------------------------------------------------------------------------
# Integration test: real pack run → profile
# ---------------------------------------------------------------------------


def test_profile_from_real_pack_run_shows_savings(tmp_path: Path) -> None:
    # Large file forces compression/summary strategy; small one stays full
    _write(tmp_path / "src" / "router.py", "\n".join(
        [f"def route_{i}(req): return '{i}'" for i in range(100)]
    ))
    _write(tmp_path / "src" / "auth.py", "def login(u, p):\n    return True\n")

    engine = RedconEngine()
    run = engine.pack(task="add auth to router", repo=tmp_path, max_tokens=800)

    profile = engine.profile(run)

    assert profile["tokens_before"] > 0
    assert profile["tokens_after"] <= profile["tokens_before"]
    assert profile["tokens_saved"] >= 0
    assert 0.0 <= profile["savings_pct"] <= 100.0
    assert isinstance(profile["per_file"], list)
    assert len(profile["per_file"]) > 0

    # At least one file should have non-full strategy (compression or slicing)
    stages = {r["stage"] for r in profile["per_file"]}
    assert stages - {STAGE_FULL}


def test_profile_token_savings_decrease_vs_full_file(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "big.py", "\n".join(
        [f"def helper_{i}(): return {i}" for i in range(120)]
    ))

    engine = RedconEngine()
    run = engine.pack(task="refactor helpers", repo=tmp_path, max_tokens=500)
    profile = engine.profile(run)

    assert profile["tokens_saved"] > 0, "expected savings for large compressed file"
    assert profile["tokens_after"] < profile["tokens_before"]


def test_profile_from_json_file(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "service.py", "\n".join(
        [f"def op_{i}(): pass" for i in range(80)]
    ))
    engine = RedconEngine()
    run = engine.pack(task="optimize service", repo=tmp_path, max_tokens=400)

    run_json_path = tmp_path / "run.json"
    import json as _json
    run_json_path.write_text(_json.dumps(run, default=str), encoding="utf-8")

    profile = engine.profile(run_json_path)

    assert profile["run_json"] == str(run_json_path)
    assert profile["tokens_before"] > 0


# ---------------------------------------------------------------------------
# Markdown render tests
# ---------------------------------------------------------------------------


def test_render_profile_markdown_contains_required_sections() -> None:
    entries = [
        _make_compressed_entry("src/big.py", strategy="summary", chunk_strategy="summary-preview", original_tokens=500, compressed_tokens=40),
        _make_compressed_entry("src/small.py", strategy="full", chunk_strategy="full-file", original_tokens=50, compressed_tokens=55),
    ]
    data = _make_run_data(entries)
    profile_data = build_savings_profile(data, run_json="run.json")

    from dataclasses import asdict
    md = render_profile_markdown(asdict(profile_data))

    assert "# Redcon Token Savings Profile" in md
    assert "## Summary" in md
    assert "## Savings by Stage" in md
    assert "## Per-File Breakdown" in md
    assert "Compression" in md
    assert "src/big.py" in md
    assert "src/small.py" in md


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------


def test_cli_profile_writes_json_and_markdown(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "router.py", "\n".join(
        [f"def route_{i}(req): return '{i}'" for i in range(80)]
    ))
    engine = RedconEngine()
    run = engine.pack(task="add auth to router", repo=tmp_path, max_tokens=600)

    run_json_path = tmp_path / "run.json"
    run_json_path.write_text(json.dumps(run, default=str), encoding="utf-8")

    import sys
    from redcon.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["profile", str(run_json_path), "--out-prefix", str(tmp_path / "profile")])
    import os
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rc = args.func(args)
    finally:
        os.chdir(old_cwd)

    assert rc == 0
    assert (tmp_path / "profile.json").exists()
    assert (tmp_path / "profile.md").exists()

    profile_data = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert "tokens_before" in profile_data
    assert "tokens_saved" in profile_data
    assert "by_stage" in profile_data
    assert "per_file" in profile_data

    md = (tmp_path / "profile.md").read_text(encoding="utf-8")
    assert "# Redcon Token Savings Profile" in md
