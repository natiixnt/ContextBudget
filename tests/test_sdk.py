from __future__ import annotations

"""Dedicated tests for the BudgetGuard stable SDK interface.

Covers the three primary agent-framework integration methods:
  - BudgetGuard.pack_context()
  - BudgetGuard.simulate_agent()
  - BudgetGuard.profile_run()

Also covers BudgetPolicyViolationError structure and BudgetGuard
constructor defaults, which are the other surfaces SDK callers rely on.
"""

from pathlib import Path

import pytest

from contextbudget import BudgetGuard, BudgetPolicyViolationError, ContextBudgetEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_repo(tmp_path: Path) -> Path:
    """Small auth-focused repository with an import edge (session → auth)."""
    _write(
        tmp_path / "src" / "auth.py",
        "def login(token: str) -> bool:\n    return token.startswith('prod_')\n",
    )
    _write(
        tmp_path / "src" / "session.py",
        "from .auth import login\n\ndef create_session(token: str) -> bool:\n    return login(token)\n",
    )
    _write(
        tmp_path / "tests" / "test_auth.py",
        "from src.auth import login\n\ndef test_login():\n    assert login('prod_x')\n",
    )
    return tmp_path


@pytest.fixture()
def caching_repo(tmp_path: Path) -> Path:
    """Two-file caching-focused repository."""
    _write(
        tmp_path / "src" / "search_api.py",
        "def search(query: str) -> list[str]:\n    return [query]\n",
    )
    _write(
        tmp_path / "src" / "cache.py",
        "def cache_get(key: str) -> str | None:\n    return None\n",
    )
    return tmp_path


@pytest.fixture()
def strict_policy_repo(tmp_path: Path) -> Path:
    """Repository accompanied by a policy that will always be violated."""
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(tmp_path / "policy.toml", "[policy]\nmax_files_included = 0\n")
    return tmp_path


# ---------------------------------------------------------------------------
# BudgetGuard: constructor defaults
# ---------------------------------------------------------------------------


def test_budget_guard_default_constructor() -> None:
    guard = BudgetGuard()
    assert guard.max_tokens is None
    assert guard.strict is False
    assert guard.top_files is None
    assert isinstance(guard.engine, ContextBudgetEngine)


def test_budget_guard_stores_constructor_params() -> None:
    guard = BudgetGuard(
        max_tokens=20000,
        top_files=10,
        max_files_included=5,
        max_quality_risk_level="low",
        min_estimated_savings_percentage=10.0,
        strict=False,
    )
    assert guard.max_tokens == 20000
    assert guard.top_files == 10
    assert guard.max_files_included == 5
    assert guard.max_quality_risk_level == "low"
    assert guard.min_estimated_savings_percentage == 10.0


def test_budget_guard_accepts_external_engine() -> None:
    engine = ContextBudgetEngine()
    guard = BudgetGuard(engine=engine)
    assert guard.engine is engine


# ---------------------------------------------------------------------------
# pack_context: artifact completeness
# ---------------------------------------------------------------------------


def test_pack_context_returns_command_pack(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="add caching", repo=caching_repo)
    assert result["command"] == "pack"


def test_pack_context_task_echoed_in_artifact(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="add caching", repo=caching_repo)
    assert result["task"] == "add caching"


def test_pack_context_max_tokens_reflected_in_artifact(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="add caching", repo=caching_repo)
    assert result["max_tokens"] == 30000


def test_pack_context_budget_block_schema(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="add caching", repo=caching_repo)

    budget = result["budget"]
    assert isinstance(budget["estimated_input_tokens"], int)
    assert isinstance(budget["estimated_saved_tokens"], int)
    assert isinstance(budget["duplicate_reads_prevented"], int)
    assert isinstance(budget["quality_risk_estimate"], str)
    assert budget["estimated_input_tokens"] >= 0
    assert budget["estimated_saved_tokens"] >= 0


def test_pack_context_ranked_files_and_included_lists_present(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="add caching", repo=caching_repo)

    assert isinstance(result["ranked_files"], list)
    assert isinstance(result["files_included"], list)
    assert isinstance(result["compressed_context"], list)


def test_pack_context_token_estimator_block_present(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="add caching", repo=caching_repo)

    estimator = result.get("token_estimator", {})
    assert estimator.get("selected_backend") == "heuristic"


def test_pack_context_ranked_file_items_have_score_fields(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="add caching", repo=caching_repo)

    for item in result["ranked_files"]:
        assert "path" in item
        assert "score" in item
        assert "heuristic_score" in item


# ---------------------------------------------------------------------------
# pack_context: parameter inheritance and override
# ---------------------------------------------------------------------------


def test_pack_context_inherits_guard_max_tokens(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=8000)
    result = guard.pack_context(task="tighten auth", repo=auth_repo)
    assert result["max_tokens"] == 8000


def test_pack_context_call_overrides_max_tokens(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.pack_context(task="tighten auth", repo=auth_repo, max_tokens=5000)
    assert result["max_tokens"] == 5000


def test_pack_context_inherits_guard_top_files(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000, top_files=1)
    result = guard.pack_context(task="add caching", repo=caching_repo)
    assert len(result["ranked_files"]) <= 1


def test_pack_context_call_overrides_top_files(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000, top_files=10)
    result = guard.pack_context(task="add caching", repo=caching_repo, top_files=1)
    assert len(result["ranked_files"]) <= 1


# ---------------------------------------------------------------------------
# pack_context: strict policy enforcement
# ---------------------------------------------------------------------------


def test_pack_context_non_strict_does_not_raise(strict_policy_repo: Path) -> None:
    guard = BudgetGuard(
        max_tokens=30000,
        policy_path=strict_policy_repo / "policy.toml",
    )
    # strict defaults to False — must not raise even though policy is violated
    result = guard.pack_context(task="update auth", repo=strict_policy_repo)
    assert result["command"] == "pack"


def test_pack_context_strict_raises_budget_policy_error(strict_policy_repo: Path) -> None:
    guard = BudgetGuard(strict=True, policy_path=strict_policy_repo / "policy.toml")
    with pytest.raises(BudgetPolicyViolationError):
        guard.pack_context(task="update auth", repo=strict_policy_repo)


def test_pack_context_strict_call_override_triggers_enforcement(strict_policy_repo: Path) -> None:
    guard = BudgetGuard(strict=False, policy_path=strict_policy_repo / "policy.toml")
    with pytest.raises(BudgetPolicyViolationError):
        guard.pack_context(task="update auth", repo=strict_policy_repo, strict=True)


def test_pack_context_strict_error_exposes_policy_result(strict_policy_repo: Path) -> None:
    guard = BudgetGuard(strict=True, policy_path=strict_policy_repo / "policy.toml")
    with pytest.raises(BudgetPolicyViolationError) as exc_info:
        guard.pack_context(task="update auth", repo=strict_policy_repo)

    err = exc_info.value
    assert err.policy_result["passed"] is False
    assert isinstance(err.policy_result["violations"], list)
    assert err.policy_result["violations"]


def test_pack_context_strict_error_exposes_run_artifact(strict_policy_repo: Path) -> None:
    guard = BudgetGuard(strict=True, policy_path=strict_policy_repo / "policy.toml")
    with pytest.raises(BudgetPolicyViolationError) as exc_info:
        guard.pack_context(task="update auth", repo=strict_policy_repo)

    err = exc_info.value
    assert err.run_artifact["command"] == "pack"
    assert "policy" in err.run_artifact


def test_pack_context_strict_non_override_call_does_not_raise(strict_policy_repo: Path) -> None:
    guard = BudgetGuard(strict=True, policy_path=strict_policy_repo / "policy.toml")
    # Calling with strict=False at call level should suppress enforcement.
    result = guard.pack_context(task="update auth", repo=strict_policy_repo, strict=False)
    assert result["command"] == "pack"


# ---------------------------------------------------------------------------
# pack_context: evaluate_policy on BudgetGuard
# ---------------------------------------------------------------------------


def test_evaluate_policy_returns_result_dict(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    run = guard.pack_context(task="add caching", repo=caching_repo)
    policy_result = guard.evaluate_policy(run)

    assert isinstance(policy_result, dict)
    assert "passed" in policy_result
    assert isinstance(policy_result["passed"], bool)
    assert "violations" in policy_result
    assert isinstance(policy_result["violations"], list)


def test_evaluate_policy_passes_with_permissive_policy(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    run = guard.pack_context(task="add caching", repo=caching_repo)
    policy_result = guard.evaluate_policy(run)
    # No constraints set on guard → empty policy → must pass.
    assert policy_result["passed"] is True


def test_evaluate_policy_fails_with_impossible_constraint(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    run = guard.pack_context(task="add caching", repo=caching_repo)

    constrained_guard = BudgetGuard(max_files_included=0)
    result = constrained_guard.evaluate_policy(run)
    assert result["passed"] is False
    assert result["violations"]


# ---------------------------------------------------------------------------
# simulate_agent: top-level shape
# ---------------------------------------------------------------------------


def test_simulate_agent_returns_simulate_agent_command(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)
    assert plan["command"] == "simulate-agent"


def test_simulate_agent_task_echoed_in_plan(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)
    assert plan["task"] == "update auth flow"


def test_simulate_agent_required_top_level_keys(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)

    for key in ("steps", "total_tokens", "unique_context_tokens",
                "total_context_tokens", "cost_estimate"):
        assert key in plan, f"missing key: {key}"


# ---------------------------------------------------------------------------
# simulate_agent: step structure
# ---------------------------------------------------------------------------


def test_simulate_agent_steps_is_nonempty_list(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)
    assert isinstance(plan["steps"], list)
    assert len(plan["steps"]) > 0


def test_simulate_agent_each_step_has_required_fields(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)

    for step in plan["steps"]:
        for field in ("id", "title", "objective", "files_read",
                      "context_tokens", "step_total_tokens"):
            assert field in step, f"step missing field: {field}"
        assert isinstance(step["files_read"], list)
        assert isinstance(step["context_tokens"], int)
        assert step["context_tokens"] >= 0


def test_simulate_agent_files_read_items_have_required_fields(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)

    for step in plan["steps"]:
        for item in step["files_read"]:
            assert "path" in item
            assert "tokens" in item
            assert "read_type" in item
            assert isinstance(item["tokens"], int)


def test_simulate_agent_standard_step_ids_present(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)

    step_ids = {step["id"] for step in plan["steps"]}
    assert {"inspect", "implement"}.issubset(step_ids)


# ---------------------------------------------------------------------------
# simulate_agent: token accounting invariants
# ---------------------------------------------------------------------------


def test_simulate_agent_total_tokens_positive(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)
    assert plan["total_tokens"] > 0


def test_simulate_agent_unique_context_tokens_positive(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)
    assert plan["unique_context_tokens"] > 0


def test_simulate_agent_token_summary_fields_nonnegative(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)
    for field in ("total_tokens", "unique_context_tokens", "total_context_tokens",
                  "total_prompt_tokens", "total_output_tokens"):
        assert plan[field] >= 0, f"{field} should be non-negative"


# ---------------------------------------------------------------------------
# simulate_agent: cost estimate
# ---------------------------------------------------------------------------


def test_simulate_agent_cost_estimate_present(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)
    assert isinstance(plan["cost_estimate"], dict)


def test_simulate_agent_cost_estimate_has_total_cost(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)
    assert "total_cost_usd" in plan["cost_estimate"]
    assert plan["cost_estimate"]["total_cost_usd"] >= 0.0


# ---------------------------------------------------------------------------
# simulate_agent: parameter inheritance and override
# ---------------------------------------------------------------------------


def test_simulate_agent_inherits_guard_top_files(caching_repo: Path) -> None:
    # top_files limits per-step scoring candidates; shared-context reads are added
    # on top, so we check the step-specific files only.
    guard = BudgetGuard(max_tokens=30000, top_files=1)
    plan = guard.simulate_agent(task="add caching", repo=caching_repo)

    for step in plan["steps"]:
        step_specific = [f for f in step["files_read"] if f.get("read_type") == "step"]
        assert len(step_specific) <= 1


def test_simulate_agent_call_overrides_top_files(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000, top_files=10)
    plan = guard.simulate_agent(task="add caching", repo=caching_repo, top_files=1)

    for step in plan["steps"]:
        step_specific = [f for f in step["files_read"] if f.get("read_type") == "step"]
        assert len(step_specific) <= 1


def test_simulate_agent_no_guard_top_files_uses_config_default(auth_repo: Path) -> None:
    # Guard has no top_files — engine should fall back to the config default.
    guard = BudgetGuard()
    plan = guard.simulate_agent(task="update auth flow", repo=auth_repo)

    assert plan["steps"]
    assert plan["total_tokens"] > 0


# ---------------------------------------------------------------------------
# profile_run: profile block schema
# ---------------------------------------------------------------------------


def test_profile_run_adds_profile_key(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.profile_run(task="add caching", repo=caching_repo)
    assert "profile" in result


def test_profile_run_profile_block_has_all_fields(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    profile = guard.profile_run(task="add caching", repo=caching_repo)["profile"]

    for field in (
        "elapsed_ms",
        "estimated_input_tokens",
        "estimated_saved_tokens",
        "compression_ratio",
        "files_included_count",
        "files_skipped_count",
        "quality_risk_estimate",
    ):
        assert field in profile, f"profile missing field: {field}"


def test_profile_run_field_types(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    profile = guard.profile_run(task="add caching", repo=caching_repo)["profile"]

    assert isinstance(profile["elapsed_ms"], int)
    assert isinstance(profile["estimated_input_tokens"], int)
    assert isinstance(profile["estimated_saved_tokens"], int)
    assert isinstance(profile["compression_ratio"], float)
    assert isinstance(profile["files_included_count"], int)
    assert isinstance(profile["files_skipped_count"], int)
    assert isinstance(profile["quality_risk_estimate"], str)


# ---------------------------------------------------------------------------
# profile_run: numeric invariants
# ---------------------------------------------------------------------------


def test_profile_run_elapsed_ms_nonnegative(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    profile = guard.profile_run(task="add caching", repo=caching_repo)["profile"]
    assert profile["elapsed_ms"] >= 0


def test_profile_run_token_counts_nonnegative(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    profile = guard.profile_run(task="add caching", repo=caching_repo)["profile"]
    assert profile["estimated_input_tokens"] >= 0
    assert profile["estimated_saved_tokens"] >= 0


def test_profile_run_compression_ratio_in_unit_interval(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    profile = guard.profile_run(task="add caching", repo=caching_repo)["profile"]
    assert 0.0 <= profile["compression_ratio"] <= 1.0


def test_profile_run_compression_ratio_formula(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=500)
    profile = guard.profile_run(task="tighten auth checks", repo=auth_repo)["profile"]

    estimated_input = profile["estimated_input_tokens"]
    estimated_saved = profile["estimated_saved_tokens"]
    original = estimated_input + estimated_saved

    if original > 0:
        expected = round(estimated_saved / original, 4)
        assert profile["compression_ratio"] == expected
    else:
        assert profile["compression_ratio"] == 0.0


def test_profile_run_file_counts_nonnegative(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    profile = guard.profile_run(task="add caching", repo=caching_repo)["profile"]
    assert profile["files_included_count"] >= 0
    assert profile["files_skipped_count"] >= 0


# ---------------------------------------------------------------------------
# profile_run: full pack artifact still intact
# ---------------------------------------------------------------------------


def test_profile_run_pack_artifact_fields_present(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.profile_run(task="add caching", repo=caching_repo)

    for field in ("command", "task", "budget", "ranked_files",
                  "files_included", "token_estimator"):
        assert field in result, f"pack artifact missing field: {field}"
    assert result["command"] == "pack"


# ---------------------------------------------------------------------------
# profile_run: parameter inheritance and override
# ---------------------------------------------------------------------------


def test_profile_run_inherits_guard_max_tokens(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=4000)
    result = guard.profile_run(task="update auth", repo=auth_repo)
    assert result["max_tokens"] == 4000


def test_profile_run_call_overrides_max_tokens(auth_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000)
    result = guard.profile_run(task="update auth", repo=auth_repo, max_tokens=4000)
    assert result["max_tokens"] == 4000


def test_profile_run_inherits_guard_top_files(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000, top_files=1)
    result = guard.profile_run(task="add caching", repo=caching_repo)
    assert result["profile"]["files_included_count"] <= 1


def test_profile_run_call_overrides_top_files(caching_repo: Path) -> None:
    guard = BudgetGuard(max_tokens=30000, top_files=10)
    result = guard.profile_run(task="add caching", repo=caching_repo, top_files=1)
    assert result["profile"]["files_included_count"] <= 1


# ---------------------------------------------------------------------------
# BudgetPolicyViolationError: exception contract
# ---------------------------------------------------------------------------


def test_violation_error_message_from_violations_list(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    guard = BudgetGuard(max_tokens=30000)
    run = guard.pack_context(task="update auth", repo=tmp_path)

    policy = ContextBudgetEngine.make_policy(max_files_included=0)
    policy_result = guard.engine.evaluate_policy(run, policy=policy)

    err = BudgetPolicyViolationError(policy_result=policy_result, run_artifact=run)

    assert str(err)
    assert err.policy_result is policy_result
    assert err.run_artifact is run
    assert err.policy_result["passed"] is False


def test_violation_error_generic_fallback_message() -> None:
    policy_result: dict = {"passed": False, "violations": []}
    run_artifact: dict = {}

    err = BudgetPolicyViolationError(policy_result=policy_result, run_artifact=run_artifact)
    assert "policy check failed" in str(err)


def test_violation_error_is_runtime_error() -> None:
    err = BudgetPolicyViolationError(
        policy_result={"passed": False, "violations": ["too many files"]},
        run_artifact={},
    )
    assert isinstance(err, RuntimeError)
