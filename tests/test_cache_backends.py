from __future__ import annotations

from pathlib import Path

from redcon.cache import InMemorySummaryCacheBackend
from redcon.config import default_config
from redcon.stages.workflow import run_pack_stage, run_scan_stage, run_score_stage


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_run_pack_stage_supports_in_memory_cache_backend(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "large.py", "\n".join(f"line {index}" for index in range(2000)) + "\n")

    cfg = default_config()
    files = run_scan_stage(tmp_path, cfg)
    ranked = run_score_stage("touch unrelated", files, cfg)
    cache = InMemorySummaryCacheBackend()

    first = run_pack_stage("touch unrelated", tmp_path, ranked, 500, cache, cfg)
    second = run_pack_stage("touch unrelated", tmp_path, ranked, 500, cache, cfg)

    assert first.cache.backend == "memory"
    assert first.cache.hits == 0
    assert first.cache.misses >= 1
    assert second.cache.backend == "memory"
    assert second.cache.hits >= 1


def test_in_memory_cache_tracks_fragment_reuse_stats(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")

    cfg = default_config()
    cfg.compression.full_file_threshold_tokens = 1000
    cfg.compression.snippet_score_threshold = 999.0
    files = run_scan_stage(tmp_path, cfg)
    ranked = run_score_stage("update auth flow", files, cfg)
    cache = InMemorySummaryCacheBackend()

    first = run_pack_stage("update auth flow", tmp_path, ranked, 1000, cache, cfg)
    second = run_pack_stage("update auth flow", tmp_path, ranked, 1000, cache, cfg)

    assert first.cache.fragment_misses >= 1
    assert first.cache.tokens_saved == 0
    assert first.compressed_files[0].cache_status == "stored"
    assert second.cache.fragment_hits >= 1
    assert second.compressed_files[0].cache_status == "reused"
    # Self-contained cache keeps real text - no fake token savings from markers.
    assert second.compressed_files[0].compressed_tokens == first.compressed_files[0].compressed_tokens
    assert not second.compressed_files[0].text.startswith("@cached-summary:")
