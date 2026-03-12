from __future__ import annotations

import json
from pathlib import Path

from contextbudget.cli import main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_cli_pack_and_report(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "pack", "add caching to search api", "--repo", str(repo), "--out-prefix", "run"],
    )
    assert main() == 0
    assert (tmp_path / "run.json").exists()
    assert (tmp_path / "run.md").exists()

    monkeypatch.setattr("sys.argv", ["contextbudget", "report", "run.json", "--out", "summary.md"])
    assert main() == 0
    assert (tmp_path / "summary.md").exists()


def test_cli_pack_uses_repo_config_default_max_tokens(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "contextbudget.toml", "[budget]\nmax_tokens = 77\n")
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "pack", "add caching to search api", "--repo", str(repo), "--out-prefix", "run2"],
    )
    assert main() == 0
    data = json.loads((tmp_path / "run2.json").read_text(encoding="utf-8"))
    assert data["max_tokens"] == 77


def test_cli_max_tokens_flag_overrides_config(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "contextbudget.toml", "[budget]\nmax_tokens = 77\n")
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "pack",
            "add caching to search api",
            "--repo",
            str(repo),
            "--max-tokens",
            "41",
            "--out-prefix",
            "run3",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "run3.json").read_text(encoding="utf-8"))
    assert data["max_tokens"] == 41


def test_cli_top_files_flag_overrides_config(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "contextbudget.toml", "[budget]\ntop_files = 1\n")
    _write(repo / "src" / "search.py", "def search():\n    return []\n")
    _write(repo / "src" / "cache.py", "def cache_search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "plan",
            "add caching to search api",
            "--repo",
            str(repo),
            "--top-files",
            "2",
            "--out-prefix",
            "plan1",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "plan1.json").read_text(encoding="utf-8"))
    assert len(data["ranked_files"]) == 2


def test_cli_diff_writes_json_and_markdown(tmp_path: Path, monkeypatch) -> None:
    old_run = {
        "task": "old",
        "files_included": ["src/a.py"],
        "ranked_files": [{"path": "src/a.py", "score": 1.0}],
        "budget": {
            "estimated_input_tokens": 100,
            "estimated_saved_tokens": 10,
            "quality_risk_estimate": "medium",
        },
        "cache_hits": 1,
    }
    new_run = {
        "task": "new",
        "files_included": ["src/b.py"],
        "ranked_files": [{"path": "src/b.py", "score": 2.0}],
        "budget": {
            "estimated_input_tokens": 80,
            "estimated_saved_tokens": 20,
            "quality_risk_estimate": "low",
        },
        "cache_hits": 3,
    }
    (tmp_path / "old-run.json").write_text(json.dumps(old_run), encoding="utf-8")
    (tmp_path / "new-run.json").write_text(json.dumps(new_run), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "diff",
            "old-run.json",
            "new-run.json",
            "--out-prefix",
            "delta",
        ],
    )
    assert main() == 0
    assert (tmp_path / "delta.json").exists()
    assert (tmp_path / "delta.md").exists()
    data = json.loads((tmp_path / "delta.json").read_text(encoding="utf-8"))
    assert data["task_diff"]["changed"] is True
    assert data["budget_delta"]["cache_hits"]["delta"] == 2


def test_cli_pack_strict_policy_failure_returns_nonzero(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")
    _write(
        tmp_path / "policy.toml",
        """
[policy]
max_estimated_input_tokens = 1
max_quality_risk_level = "low"
""".strip(),
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "pack",
            "add caching to search api",
            "--repo",
            str(repo),
            "--strict",
            "--policy",
            "policy.toml",
            "--out-prefix",
            "strict-run",
        ],
    )
    assert main() == 2
    data = json.loads((tmp_path / "strict-run.json").read_text(encoding="utf-8"))
    assert data["policy"]["passed"] is False
    assert data["policy"]["violations"]


def test_cli_pack_policy_without_strict_keeps_default_behavior(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")
    _write(
        tmp_path / "policy.toml",
        """
[policy]
max_estimated_input_tokens = 1
""".strip(),
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "pack",
            "add caching to search api",
            "--repo",
            str(repo),
            "--policy",
            "policy.toml",
            "--out-prefix",
            "nostrict-run",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "nostrict-run.json").read_text(encoding="utf-8"))
    assert "policy" not in data


def test_cli_report_policy_failure_returns_nonzero(tmp_path: Path, monkeypatch) -> None:
    run = {
        "task": "x",
        "files_included": ["src/a.py", "src/b.py"],
        "ranked_files": [{"path": "src/a.py", "score": 1.0}],
        "budget": {
            "estimated_input_tokens": 50,
            "estimated_saved_tokens": 0,
            "quality_risk_estimate": "high",
        },
        "cache_hits": 0,
    }
    (tmp_path / "run.json").write_text(json.dumps(run), encoding="utf-8")
    _write(
        tmp_path / "policy.toml",
        """
[policy]
max_files_included = 1
max_quality_risk_level = "medium"
""".strip(),
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "report",
            "run.json",
            "--policy",
            "policy.toml",
            "--out",
            "policy-report.md",
        ],
    )
    assert main() == 2
    assert (tmp_path / "policy-report.md").exists()
