from __future__ import annotations

from pathlib import Path

from contextbudget.cache import InMemorySummaryCacheBackend
from contextbudget.config import default_config
from contextbudget.stages.workflow import run_pack_stage, run_scan_stage, run_score_stage


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
