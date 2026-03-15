from __future__ import annotations

from pathlib import Path

import pytest

from contextbudget import BudgetGuard, BudgetPolicyViolationError, ContextBudgetEngine

from tests.support_git import build_pr_audit_repo


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_engine_plan_pack_report_flow(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "search_api.py", "def search(query: str) -> list[str]:\n    return [query]\n")
    _write(tmp_path / "src" / "cache.py", "def cache_get(key: str) -> str | None:\n    return None\n")

    engine = ContextBudgetEngine()
    plan_data = engine.plan(task="add caching to search api", repo=tmp_path, top_files=2)
    assert plan_data["task"] == "add caching to search api"
    assert len(plan_data["ranked_files"]) <= 2

    pack_data = engine.pack(task="add caching to search api", repo=tmp_path, max_tokens=500)
    assert pack_data["command"] == "pack"
    assert pack_data["max_tokens"] == 500
    assert pack_data["cache"]["backend"] == "local_file"
    assert pack_data["summarizer"]["selected_backend"] == "deterministic"
    assert {"score", "heuristic_score", "historical_score"}.issubset(pack_data["ranked_files"][0])

    summary = engine.report(pack_data)
    assert summary["task"] == pack_data["task"]
    assert summary["estimated_input_tokens"] == pack_data["budget"]["estimated_input_tokens"]
    assert summary["cache"]["backend"] == "local_file"
    assert summary["summarizer"]["selected_backend"] == "deterministic"
    assert summary["ranked_files"][0]["heuristic_score"] == pack_data["ranked_files"][0]["heuristic_score"]


def test_engine_plan_agent_returns_stepwise_context_estimates(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")
    _write(tmp_path / "src" / "session.py", "from .auth import login\n\n\ndef create_session(token: str) -> bool:\n    return login(token)\n")
    _write(tmp_path / "tests" / "test_auth.py", "from src.auth import login\n\n\ndef test_login() -> None:\n    assert login('prod_x')\n")
    _write(tmp_path / "README.md", "Authentication flow overview\n")

    engine = ContextBudgetEngine()
    plan = engine.plan_agent(task="update auth flow docs", repo=tmp_path, top_files=4)

    assert plan["command"] == "plan_agent"
    assert plan["task"] == "update auth flow docs"
    assert plan["steps"]
    assert {step["id"] for step in plan["steps"]} >= {"inspect", "implement", "test", "validate"}
    assert any(step["id"] == "document" for step in plan["steps"])
    assert plan["shared_context"]
    assert plan["total_estimated_tokens"] >= plan["unique_context_tokens"] > 0
    assert plan["reused_context_tokens"] == plan["total_estimated_tokens"] - plan["unique_context_tokens"]
    assert any(
        item["path"] == "src/auth.py"
        for step in plan["steps"]
        for item in step["context"]
    )
    assert plan["token_estimator"]["selected_backend"] == "heuristic"


def test_engine_policy_evaluation_with_make_policy(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")

    engine = ContextBudgetEngine()
    pack_data = engine.pack(task="tighten auth checks", repo=tmp_path, max_tokens=800)

    policy = engine.make_policy(max_estimated_input_tokens=1)
    result = engine.evaluate_policy(pack_data, policy=policy)
    assert result["passed"] is False
    assert any("estimated input tokens" in item for item in result["violations"])


def test_budget_guard_pack_usage_example(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "search_api.py", "def search() -> list[str]:\n    return []\n")

    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack(task="add caching to search API", repo=tmp_path)
    assert result["command"] == "pack"
    assert result["max_tokens"] == 30000


def test_budget_guard_strict_raises_on_policy_violation(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(
        tmp_path / "policy.toml",
        """
[policy]
max_files_included = 0
""".strip(),
    )

    guard = BudgetGuard(strict=True, policy_path=tmp_path / "policy.toml")
    with pytest.raises(BudgetPolicyViolationError) as exc:
        guard.pack(task="update auth", repo=tmp_path)

    assert exc.value.policy_result["passed"] is False
    assert exc.value.policy_result["violations"]
    assert "policy" in exc.value.run_artifact


def test_engine_pack_supports_workspace(tmp_path: Path) -> None:
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

    engine = ContextBudgetEngine()
    pack_data = engine.pack(task="update auth flow", workspace=tmp_path / "workspace.toml", max_tokens=500)

    assert pack_data["workspace"].endswith("workspace.toml")
    assert set(pack_data["selected_repos"]) == {"app", "shared"}
    assert any(item["repo"] == "app" for item in pack_data["ranked_files"])


def test_engine_report_preserves_model_profile_assumptions(tmp_path: Path) -> None:
    _write(tmp_path / "contextbudget.toml", 'model_profile = "gpt-4.1"\n')
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    engine = ContextBudgetEngine()
    pack_data = engine.pack(task="update auth flow", repo=tmp_path)
    summary = engine.report(pack_data)

    assert pack_data["model_profile"]["selected_profile"] == "gpt-4.1"
    assert summary["model_profile"]["selected_profile"] == "gpt-4.1"
    assert summary["model_profile"]["context_window"] == 1_047_576


def test_engine_pr_audit_returns_comment_and_summary(tmp_path: Path) -> None:
    repo, base_commit, head_commit = build_pr_audit_repo(tmp_path)

    engine = ContextBudgetEngine()
    data = engine.pr_audit(repo=repo, base_ref=base_commit, head_ref=head_commit)

    assert data["command"] == "pr-audit"
    assert data["comment_markdown"].startswith("## ContextBudget Analysis")
    assert data["summary"]["estimated_token_delta"] > 0


def test_engine_pack_can_emit_delta_context_package(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
snippet_total_line_limit = 40
""".strip(),
    )
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")
    _write(tmp_path / "src" / "middleware.py", "def auth_middleware(token: str) -> bool:\n    return login(token)\n")

    engine = ContextBudgetEngine()
    first = engine.pack(task="update auth middleware", repo=tmp_path, max_tokens=500)

    (tmp_path / "src" / "middleware.py").unlink()
    _write(
        tmp_path / "src" / "auth.py",
        "class AuthService:\n    def login_user(self, token: str) -> bool:\n        return token.startswith('prod_')\n",
    )
    _write(tmp_path / "src" / "permissions.py", "def allow_auth(token: str) -> bool:\n    return token.startswith('prod_')\n")

    second = engine.pack(task="update auth middleware", repo=tmp_path, max_tokens=500, delta_from=first)

    assert second["delta"]["files_added"] == ["src/permissions.py"]
    assert second["delta"]["files_removed"] == ["src/middleware.py"]
    assert second["delta"]["changed_files"] == ["src/auth.py"]
    assert second["delta"]["budget"]["original_tokens"] > 0
    assert second["delta"]["budget"]["delta_tokens"] > 0
    assert second["delta"]["budget"]["tokens_saved"] >= 0


def test_engine_plan_agent_supports_workspace(tmp_path: Path) -> None:
    _write(tmp_path / "app" / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(tmp_path / "shared" / "src" / "auth.py", "def validate() -> bool:\n    return True\n")
    _write(tmp_path / "app" / "tests" / "test_auth.py", "def test_login() -> None:\n    assert True\n")
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

    engine = ContextBudgetEngine()
    plan = engine.plan_agent(task="update auth flow across services", workspace=tmp_path / "workspace.toml", top_files=3)

    assert plan["workspace"].endswith("workspace.toml")
    assert set(plan["selected_repos"]) == {"app", "shared"}
    assert any(item.get("repo") == "app" for step in plan["steps"] for item in step["context"])


def test_engine_heatmap_aggregates_run_history(tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.mkdir()
    (history / "run-one.json").write_text(
        """
{
  "command": "pack",
  "generated_at": "2026-03-10T10:00:00+00:00",
  "task": "run one",
  "repo": "repo",
  "files_included": ["src/auth.py"],
  "compressed_context": [
    {"path": "src/auth.py", "original_tokens": 100, "compressed_tokens": 60}
  ],
  "budget": {"estimated_input_tokens": 60, "estimated_saved_tokens": 40}
}
""".strip(),
        encoding="utf-8",
    )
    (history / "run-two.json").write_text(
        """
{
  "command": "pack",
  "generated_at": "2026-03-11T10:00:00+00:00",
  "task": "run two",
  "repo": "repo",
  "files_included": ["src/auth.py", "src/cache.py"],
  "compressed_context": [
    {"path": "src/auth.py", "original_tokens": 120, "compressed_tokens": 70},
    {"path": "src/cache.py", "original_tokens": 40, "compressed_tokens": 20}
  ],
  "budget": {"estimated_input_tokens": 90, "estimated_saved_tokens": 70}
}
""".strip(),
        encoding="utf-8",
    )

    engine = ContextBudgetEngine()
    heatmap = engine.heatmap([history], limit=2)

    assert heatmap["runs_analyzed"] == 2
    assert heatmap["top_token_heavy_files"][0]["path"] == "src/auth.py"
    assert heatmap["top_token_heavy_directories"][0]["path"] == "src"


# ---------------------------------------------------------------------------
# BudgetGuard SDK interface: pack_context, simulate_agent, profile_run
# ---------------------------------------------------------------------------


def test_budget_guard_pack_context_returns_packed_artifact(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "search_api.py", "def search(query: str) -> list[str]:\n    return [query]\n")
    _write(tmp_path / "src" / "cache.py", "def cache_get(key: str) -> str | None:\n    return None\n")

    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="add caching", repo=tmp_path)

    assert result["command"] == "pack"
    assert result["max_tokens"] == 30000
    assert result["task"] == "add caching"
    assert isinstance(result["ranked_files"], list)


def test_budget_guard_pack_context_inherits_max_tokens(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    guard = BudgetGuard(max_tokens=8000)
    result = guard.pack_context(task="tighten auth", repo=tmp_path)

    assert result["max_tokens"] == 8000


def test_budget_guard_pack_context_override_max_tokens(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="tighten auth", repo=tmp_path, max_tokens=5000)

    assert result["max_tokens"] == 5000


def test_budget_guard_pack_context_strict_raises_on_violation(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(
        tmp_path / "policy.toml",
        "[policy]\nmax_files_included = 0\n",
    )

    guard = BudgetGuard(strict=True, policy_path=tmp_path / "policy.toml")
    with pytest.raises(BudgetPolicyViolationError):
        guard.pack_context(task="update auth", repo=tmp_path)


def test_budget_guard_simulate_agent_returns_workflow_steps(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")
    _write(tmp_path / "src" / "session.py", "from .auth import login\n\ndef create_session(token):\n    return login(token)\n")
    _write(tmp_path / "tests" / "test_auth.py", "from src.auth import login\ndef test_login():\n    assert login('prod_x')\n")

    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=tmp_path)

    assert plan["command"] == "plan_agent"
    assert plan["task"] == "update auth flow"
    assert isinstance(plan["steps"], list)
    assert len(plan["steps"]) > 0
    assert {step["id"] for step in plan["steps"]} >= {"inspect", "implement"}
    assert "total_estimated_tokens" in plan
    assert "shared_context" in plan


def test_budget_guard_simulate_agent_inherits_top_files(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(tmp_path / "src" / "cache.py", "def get(k: str) -> None:\n    return None\n")

    guard = BudgetGuard(max_tokens=30000, top_files=2)
    plan = guard.simulate_agent(task="add caching", repo=tmp_path)

    all_context_paths = {item["path"] for step in plan["steps"] for item in step["context"]}
    assert len(all_context_paths) <= 2


def test_budget_guard_profile_run_adds_profile_block(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "search_api.py", "def search(query: str) -> list[str]:\n    return [query]\n")
    _write(tmp_path / "src" / "cache.py", "def cache_get(key: str) -> str | None:\n    return None\n")

    guard = BudgetGuard(max_tokens=30000)
    result = guard.profile_run(task="add caching", repo=tmp_path)

    assert result["command"] == "pack"
    assert "profile" in result
    profile = result["profile"]
    assert isinstance(profile["elapsed_ms"], int)
    assert profile["elapsed_ms"] >= 0
    assert isinstance(profile["estimated_input_tokens"], int)
    assert isinstance(profile["estimated_saved_tokens"], int)
    assert isinstance(profile["compression_ratio"], float)
    assert 0.0 <= profile["compression_ratio"] <= 1.0
    assert isinstance(profile["files_included_count"], int)
    assert isinstance(profile["files_skipped_count"], int)
    assert isinstance(profile["quality_risk_estimate"], str)


def test_budget_guard_profile_run_compression_ratio_bounds(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")

    guard = BudgetGuard(max_tokens=500)
    result = guard.profile_run(task="tighten auth checks", repo=tmp_path)

    profile = result["profile"]
    estimated_input = profile["estimated_input_tokens"]
    estimated_saved = profile["estimated_saved_tokens"]
    original = estimated_input + estimated_saved

    if original > 0:
        expected_ratio = round(estimated_saved / original, 4)
        assert profile["compression_ratio"] == expected_ratio
    else:
        assert profile["compression_ratio"] == 0.0


def test_budget_guard_profile_run_inherits_max_tokens(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    guard = BudgetGuard(max_tokens=4000)
    result = guard.profile_run(task="update auth", repo=tmp_path)

    assert result["max_tokens"] == 4000
