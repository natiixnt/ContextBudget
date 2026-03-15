from __future__ import annotations

from pathlib import Path

import pytest

from contextbudget import BudgetGuard, BudgetPolicyViolationError, ContextBudgetEngine


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

    summary = engine.report(pack_data)
    assert summary["task"] == pack_data["task"]
    assert summary["estimated_input_tokens"] == pack_data["budget"]["estimated_input_tokens"]
    assert summary["cache"]["backend"] == "local_file"
    assert summary["summarizer"]["selected_backend"] == "deterministic"


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
