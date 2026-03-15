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
    assert data["token_estimator"]["selected_backend"] == "heuristic"
    assert data["estimator_samples"]


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
    assert data["token_estimator"]["selected_backend"] == "heuristic"


def test_run_benchmark_supports_workspace(tmp_path: Path) -> None:
    _write(tmp_path / "app" / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(tmp_path / "shared" / "src" / "auth.py", "def validate() -> bool:\n    return True\n")
    _write(
        tmp_path / "workspace.toml",
        """
[scan]
include_globs = ["**/*.py"]

[[repos]]
label = "app"
path = "app"

[[repos]]
label = "shared"
path = "shared"
""".strip(),
    )

    from contextbudget.config import load_workspace

    workspace = load_workspace(tmp_path / "workspace.toml")
    data = run_benchmark("update auth flow", repo=workspace.root, config=workspace.config, workspace=workspace)

    assert data["workspace"].endswith("workspace.toml")
    assert {item["label"] for item in data["scanned_repos"]} == {"app", "shared"}


def test_benchmark_records_model_profile_assumptions(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        'model_profile = "mistral-small"\n',
    )
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n" * 20)

    data = run_benchmark("update auth flow", repo=tmp_path)

    assert data["max_tokens"] == 111616
    assert data["model_profile"]["selected_profile"] == "mistral-small"
    assert data["model_profile"]["resolved_profile"] == "mistral-small"
    assert data["model_profile"]["family"] == "mistral"
    assert data["model_profile"]["tokenizer"] == "tekken"
    assert data["model_profile"]["recommended_compression_strategy"] == "balanced"
