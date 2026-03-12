from __future__ import annotations

import json
from pathlib import Path

from contextbudget.cli import main
from contextbudget.core.benchmark import run_benchmark


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_run_benchmark_includes_required_strategies(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")
    _write(tmp_path / "src" / "middleware.py", "from .auth import login\n")
    # Large low-signal file to exercise summary cache behavior.
    _write(tmp_path / "src" / "large.py", "\n".join([f"line {i}" for i in range(2000)]) + "\n")

    data = run_benchmark("refactor auth middleware", repo=tmp_path)

    assert data["command"] == "benchmark"
    strategies = {item["strategy"]: item for item in data["strategies"]}
    assert {"naive_full_context", "top_k_selection", "compressed_pack", "cache_assisted_pack"}.issubset(
        strategies.keys()
    )

    naive = strategies["naive_full_context"]
    compressed = strategies["compressed_pack"]
    cache_assisted = strategies["cache_assisted_pack"]

    assert naive["estimated_saved_tokens"] == 0
    assert compressed["estimated_input_tokens"] <= naive["estimated_input_tokens"]
    assert isinstance(compressed["quality_risk_estimate"], str)
    assert cache_assisted["cache_hits"] >= compressed["cache_hits"]


def test_cli_benchmark_writes_artifacts(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "benchmark",
            "add rate limiting to auth API",
            "--repo",
            str(repo),
            "--out-prefix",
            "bench",
        ],
    )
    assert main() == 0

    json_path = tmp_path / "bench.json"
    md_path = tmp_path / "bench.md"
    assert json_path.exists()
    assert md_path.exists()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["command"] == "benchmark"
    assert data["strategies"]
