"""Regression tests for the quality risk estimate.

The old formula scored kept content as risk (a full uncompressed pack
rated worse than one that silently dropped 95% of the code) and never
saw the token budget or the degradation pass, so a blown or heavily
degraded pack still reported "low".
"""

from __future__ import annotations

from pathlib import Path

from redcon.compressors.context_compressor import _build_risk_estimate
from redcon.config import CompressionSettings
from redcon.core.pipeline import as_json_dict, run_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ranked(n: int) -> list:
    # _build_risk_estimate only reads len() of this list.
    return [None] * n


def test_full_uncompressed_pack_is_low_risk() -> None:
    # Everything included, nothing compressed away: the agent sees all
    # source content. The old formula called this medium.
    risk = _build_risk_estimate(
        CompressionSettings(),
        files_skipped=[],
        ranked_files=_ranked(10),
        total_compressed=1000,
        total_raw=1000,
        max_tokens=5000,
        files_included_count=10,
    )
    assert risk == "low"


def test_over_budget_pack_is_always_high_risk() -> None:
    # The budget invariant is broken; no ratio math may soften that.
    risk = _build_risk_estimate(
        CompressionSettings(),
        files_skipped=[],
        ranked_files=_ranked(10),
        total_compressed=6000,
        total_raw=6000,
        max_tokens=5000,
        files_included_count=10,
    )
    assert risk == "high"


def test_routine_compression_stays_low_risk() -> None:
    # A healthy 3x compression with nothing skipped is redcon working
    # as designed, not an alarm. Policy gates on "low" must keep passing.
    risk = _build_risk_estimate(
        CompressionSettings(),
        files_skipped=[],
        ranked_files=_ranked(10),
        total_compressed=333,
        total_raw=1000,
        max_tokens=5000,
        files_included_count=10,
    )
    assert risk == "low"


def test_extreme_loss_and_skips_are_high_risk() -> None:
    # Most ranked files dropped and almost all tokens gone: the old
    # formula called this low because heavy compression scored as safe.
    risk = _build_risk_estimate(
        CompressionSettings(),
        files_skipped=["f"] * 7,
        ranked_files=_ranked(10),
        total_compressed=50,
        total_raw=1000,
        max_tokens=5000,
        files_included_count=3,
    )
    assert risk == "high"


def test_degraded_files_raise_risk_above_low() -> None:
    # Every included file was degraded to a lower tier in Pass 2. Token
    # loss alone sits inside the grace zone, so only the degradation
    # signal can lift this above "low".
    settings = CompressionSettings()
    degraded = [f"src/f{i}.py" for i in range(8)]
    risk = _build_risk_estimate(
        settings,
        files_skipped=[],
        ranked_files=_ranked(10),
        total_compressed=600,
        total_raw=1000,
        max_tokens=5000,
        files_included_count=8,
        degraded_files=degraded,
    )
    assert risk != "low"

    baseline = _build_risk_estimate(
        settings,
        files_skipped=[],
        ranked_files=_ranked(10),
        total_compressed=600,
        total_raw=1000,
        max_tokens=5000,
        files_included_count=8,
    )
    assert baseline == "low"


def test_zero_weights_do_not_crash() -> None:
    settings = CompressionSettings(risk_skip_weight=0.0, risk_compression_weight=0.0)
    risk = _build_risk_estimate(
        settings,
        files_skipped=["a"],
        ranked_files=_ranked(2),
        total_compressed=10,
        total_raw=100,
        max_tokens=50,
        files_included_count=1,
    )
    assert risk in {"low", "medium", "high"}


def test_pack_of_small_repo_reports_low_risk(tmp_path: Path) -> None:
    # End to end: a comfortable budget over a small repo must not warn.
    _write(tmp_path / "src" / "auth.py", "def login():\n    return True\n" * 10)
    _write(tmp_path / "src" / "api.py", "def handler(req):\n    return req\n" * 10)

    data = as_json_dict(run_pack("refactor auth login", repo=tmp_path, max_tokens=30_000))

    assert data["files_included"]
    assert data["budget"]["quality_risk_estimate"] == "low"
