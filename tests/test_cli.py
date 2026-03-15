from __future__ import annotations

import json
from pathlib import Path

from contextbudget.cli import main
from tests.support_git import build_pr_audit_repo


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
    run_data = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert {"score", "heuristic_score", "historical_score"}.issubset(run_data["ranked_files"][0])
    history = json.loads((repo / ".contextbudget" / "history.json").read_text(encoding="utf-8"))
    entry = history["entries"][-1]
    assert entry["task"] == "add caching to search api"
    assert entry["result_artifacts"]["run_json"] == str((tmp_path / "run.json").resolve())
    assert entry["result_artifacts"]["run_markdown"] == str((tmp_path / "run.md").resolve())

    monkeypatch.setattr("sys.argv", ["contextbudget", "report", "run.json", "--out", "summary.md"])
    assert main() == 0
    assert (tmp_path / "summary.md").exists()
    assert "combined:" in (tmp_path / "summary.md").read_text(encoding="utf-8")


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


def test_cli_plan_agent_writes_json_and_markdown(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(repo / "tests" / "test_auth.py", "def test_login() -> None:\n    assert True\n")
    _write(repo / "README.md", "auth flow notes\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "plan-agent",
            "update auth flow docs",
            "--repo",
            str(repo),
            "--out-prefix",
            "agent-plan",
        ],
    )
    assert main() == 0

    output = capsys.readouterr().out
    assert "Wrote agent plan JSON: agent-plan.json" in output
    assert "Total estimated tokens:" in output
    assert "context:" in output
    data = json.loads((tmp_path / "agent-plan.json").read_text(encoding="utf-8"))
    assert data["command"] == "plan_agent"
    assert data["steps"]
    assert data["total_estimated_tokens"] >= data["unique_context_tokens"] > 0
    assert (tmp_path / "agent-plan.md").exists()


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


def test_cli_pr_audit_writes_outputs_and_can_fail_gate(tmp_path: Path, monkeypatch) -> None:
    repo, base_commit, head_commit = build_pr_audit_repo(tmp_path)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "pr-audit",
            "--repo",
            str(repo),
            "--base",
            base_commit,
            "--head",
            head_commit,
            "--out-prefix",
            "pr-audit",
        ],
    )
    assert main() == 0
    assert (tmp_path / "pr-audit.json").exists()
    assert (tmp_path / "pr-audit.md").exists()
    assert (tmp_path / "pr-audit.comment.md").exists()
    data = json.loads((tmp_path / "pr-audit.json").read_text(encoding="utf-8"))
    assert data["summary"]["estimated_token_delta"] > 0
    assert "ContextBudget Analysis" in data["comment_markdown"]

    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "pr-audit",
            "--repo",
            str(repo),
            "--base",
            base_commit,
            "--head",
            head_commit,
            "--max-token-increase",
            "1",
        ],
    )
    assert main() == 2


def test_cli_heatmap_writes_json_and_markdown(tmp_path: Path, monkeypatch, capsys) -> None:
    history = tmp_path / "history"
    history.mkdir()
    run_one = {
        "command": "pack",
        "generated_at": "2026-03-10T10:00:00+00:00",
        "task": "run one",
        "repo": str(tmp_path / "repo"),
        "files_included": ["src/auth.py", "src/cache.py"],
        "compressed_context": [
            {"path": "src/auth.py", "original_tokens": 100, "compressed_tokens": 60},
            {"path": "src/cache.py", "original_tokens": 40, "compressed_tokens": 20},
        ],
        "budget": {"estimated_input_tokens": 80, "estimated_saved_tokens": 60},
    }
    run_two = {
        "command": "pack",
        "generated_at": "2026-03-11T10:00:00+00:00",
        "task": "run two",
        "repo": str(tmp_path / "repo"),
        "files_included": ["src/auth.py"],
        "compressed_context": [
            {"path": "src/auth.py", "original_tokens": 120, "compressed_tokens": 70},
        ],
        "budget": {"estimated_input_tokens": 70, "estimated_saved_tokens": 50},
    }
    (history / "run-one.json").write_text(json.dumps(run_one), encoding="utf-8")
    (history / "run-two.json").write_text(json.dumps(run_two), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "heatmap",
            "history",
            "--limit",
            "2",
            "--out-prefix",
            "heatmap",
        ],
    )
    assert main() == 0

    output = capsys.readouterr().out
    assert "Top token-heavy files:" in output
    assert "Top token-heavy directories:" in output
    assert (tmp_path / "heatmap.json").exists()
    assert (tmp_path / "heatmap.md").exists()

    data = json.loads((tmp_path / "heatmap.json").read_text(encoding="utf-8"))
    assert data["runs_analyzed"] == 2
    assert data["top_token_heavy_files"][0]["path"] == "src/auth.py"
    assert data["most_frequently_included_files"][0]["path"] == "src/auth.py"


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


def test_cli_pack_workspace_records_repo_provenance(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path / "services" / "auth" / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(tmp_path / "services" / "billing" / "src" / "auth.py", "def verify() -> bool:\n    return True\n")
    _write(
        tmp_path / "workspace.toml",
        """
[scan]
include_globs = ["**/*.py"]

[[repos]]
label = "auth-service"
path = "services/auth"

[[repos]]
label = "billing-service"
path = "services/billing"
""".strip(),
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "pack",
            "update auth flow across services",
            "--workspace",
            "workspace.toml",
            "--out-prefix",
            "workspace-run",
        ],
    )
    assert main() == 0
    data = json.loads((tmp_path / "workspace-run.json").read_text(encoding="utf-8"))

    assert data["workspace"].endswith("workspace.toml")
    assert {item["label"] for item in data["scanned_repos"]} == {"auth-service", "billing-service"}
    assert set(data["selected_repos"]) == {"auth-service", "billing-service"}
    assert any(path.startswith("auth-service:") for path in data["files_included"])
    assert any(path.startswith("billing-service:") for path in data["files_included"])


def test_cli_pack_can_emit_delta_context_package(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo / "contextbudget.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
snippet_total_line_limit = 40
""".strip(),
    )
    _write(repo / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")
    _write(repo / "src" / "middleware.py", "def auth_middleware(token: str) -> bool:\n    return login(token)\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "pack", "update auth middleware", "--repo", str(repo), "--out-prefix", "first"],
    )
    assert main() == 0

    (repo / "src" / "middleware.py").unlink()
    _write(
        repo / "src" / "auth.py",
        "class AuthService:\n    def login_user(self, token: str) -> bool:\n        return token.startswith('prod_')\n",
    )
    _write(repo / "src" / "permissions.py", "def allow_auth(token: str) -> bool:\n    return token.startswith('prod_')\n")

    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "pack",
            "update auth middleware",
            "--repo",
            str(repo),
            "--delta",
            "first.json",
            "--out-prefix",
            "delta-run",
        ],
    )
    assert main() == 0

    data = json.loads((tmp_path / "delta-run.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "delta-run.md").read_text(encoding="utf-8")

    assert data["delta"]["previous_run"] == "first.json"
    assert data["delta"]["files_added"] == ["src/permissions.py"]
    assert data["delta"]["files_removed"] == ["src/middleware.py"]
    assert data["delta"]["budget"]["original_tokens"] > 0
    assert data["delta"]["budget"]["delta_tokens"] > 0
    assert data["delta"]["budget"]["tokens_saved"] >= 0
    assert "## Delta Context" in markdown
    assert "Delta tokens:" in markdown


def test_cli_watch_once_writes_scan_index(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "watch", "--repo", str(repo), "--once"],
    )
    assert main() == 0

    output = capsys.readouterr().out
    assert "Initial scan:" in output
    assert "Scan index:" in output
    assert (repo / ".contextbudget" / "scan-index.json").exists()


def test_cli_pack_reports_token_estimator_fallback(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo / "contextbudget.toml",
        """
[tokens]
backend = "exact"
model = "gpt-4o-mini"
fallback_backend = "model_aligned"
""".strip(),
    )
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n" * 20)

    from contextbudget.core import tokens as token_module

    monkeypatch.setattr("contextbudget.core.tokens._load_tiktoken", lambda: None)
    token_module._resolve_builtin_token_estimator.cache_clear()
    try:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["contextbudget", "pack", "update auth flow", "--repo", str(repo), "--out-prefix", "token-run"],
        )
        assert main() == 0
    finally:
        token_module._resolve_builtin_token_estimator.cache_clear()

    output = capsys.readouterr().out
    assert "Token estimator: selected=exact_tiktoken effective=model_aligned fallback=True" in output
    assert "Token estimator note:" in output


def test_cli_pack_reports_model_profile_assumptions(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo / "contextbudget.toml",
        'model_profile = "claude-sonnet-4"\n',
    )
    _write(repo / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "contextbudget",
            "pack",
            "update auth flow",
            "--repo",
            str(repo),
            "--max-tokens",
            "500000",
            "--out-prefix",
            "model-run",
        ],
    )
    assert main() == 0

    output = capsys.readouterr().out
    assert "Model profile: selected=claude-sonnet-4" in output


# ---------------------------------------------------------------------------
# enforce command
# ---------------------------------------------------------------------------


def _make_run_json(tmp_path: Path, *, repo: Path) -> Path:
    """Pack a repo and return the path to the generated run.json."""
    monkeypatch_obj = None  # helper used inline below
    import contextbudget.cli as cli_mod
    import sys

    run_prefix = str(tmp_path / "enforce-run")
    old_argv = sys.argv[:]
    sys.argv = [
        "contextbudget",
        "pack",
        "search feature",
        "--repo",
        str(repo),
        "--out-prefix",
        run_prefix,
    ]
    try:
        cli_mod.main()
    finally:
        sys.argv = old_argv
    return tmp_path / "enforce-run.json"


def test_cli_enforce_pass(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "pack", "search feature", "--repo", str(repo), "--out-prefix", "enf-run"],
    )
    assert main() == 0
    run_json = tmp_path / "enf-run.json"
    assert run_json.exists()

    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(
        "[policy]\nmax_estimated_input_tokens = 999999\nmax_files_included = 999\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "enforce", str(policy_path), str(run_json)],
    )
    assert main() == 0


def test_cli_enforce_fail_token_limit(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "pack", "search feature", "--repo", str(repo), "--out-prefix", "enf-fail"],
    )
    assert main() == 0
    run_json = tmp_path / "enf-fail.json"

    # Set a token limit of 1 so it always fails.
    policy_path = tmp_path / "policy-fail.toml"
    policy_path.write_text(
        "[policy]\nmax_estimated_input_tokens = 1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "enforce", str(policy_path), str(run_json)],
    )
    assert main() == 2


def test_cli_enforce_fail_context_size_bytes(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "search.py", "def search():\n    return []\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "pack", "search feature", "--repo", str(repo), "--out-prefix", "enf-bytes"],
    )
    assert main() == 0
    run_json = tmp_path / "enf-bytes.json"

    # 1 byte limit always fails.
    policy_path = tmp_path / "policy-bytes.toml"
    policy_path.write_text(
        "[policy]\nmax_context_size_bytes = 1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "enforce", str(policy_path), str(run_json)],
    )
    assert main() == 2


def test_cli_enforce_missing_policy_file(tmp_path: Path, monkeypatch) -> None:
    run_json = tmp_path / "run.json"
    run_json.write_text("{}", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "enforce", str(tmp_path / "nonexistent.toml"), str(run_json)],
    )
    assert main() == 2


def test_cli_enforce_missing_run_file(tmp_path: Path, monkeypatch) -> None:
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text("[policy]\nmax_estimated_input_tokens = 30000\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["contextbudget", "enforce", str(policy_path), str(tmp_path / "nonexistent.json")],
    )
    assert main() == 2
