from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextbudget import (
    AgentTaskRequest,
    BudgetPolicyViolationError,
    ContextBudgetEngine,
    ContextBudgetMiddleware,
    LocalDemoAgentAdapter,
    enforce_budget,
    prepare_context,
    record_run,
)
from contextbudget.core.policy import PolicySpec


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fake_run_artifact(repo: Path, *, max_tokens: int = 200) -> dict:
    return {
        "command": "pack",
        "task": "update auth flow",
        "repo": str(repo),
        "max_tokens": max_tokens,
        "ranked_files": [{"path": "src/auth.py", "score": 3.0, "reasons": ["path contains 'auth'"], "line_count": 4}],
        "compressed_context": [],
        "files_included": ["src/auth.py"],
        "files_skipped": [],
        "budget": {
            "estimated_input_tokens": 80,
            "estimated_saved_tokens": 20,
            "duplicate_reads_prevented": 0,
            "quality_risk_estimate": "low",
        },
        "cache": {
            "backend": "local_file",
            "enabled": True,
            "hits": 0,
            "misses": 1,
            "writes": 1,
        },
        "cache_hits": 0,
        "generated_at": "2026-03-15T00:00:00+00:00",
    }


def test_prepare_context_delegates_to_engine_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = ContextBudgetEngine()
    called: dict[str, object] = {}

    def fake_pack(
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict:
        called.update(
            {
                "task": task,
                "repo": repo,
                "workspace": workspace,
                "max_tokens": max_tokens,
                "top_files": top_files,
                "config_path": config_path,
            }
        )
        return _fake_run_artifact(Path(repo), max_tokens=int(max_tokens or 0))

    monkeypatch.setattr(engine, "pack", fake_pack)
    middleware = ContextBudgetMiddleware(engine=engine)

    result = middleware.prepare_context(
        "update auth flow",
        repo=tmp_path,
        max_tokens=123,
        top_files=4,
        metadata={"origin": "middleware-test"},
    )

    assert called["task"] == "update auth flow"
    assert called["repo"] == tmp_path
    assert called["max_tokens"] == 123
    assert called["top_files"] == 4
    assert result.run_artifact["command"] == "pack"
    assert result.metadata["files_included_count"] == 1
    assert result.metadata["request_metadata"] == {"origin": "middleware-test"}


def test_enforce_budget_delegates_to_engine_policy_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ContextBudgetEngine()
    called: dict[str, object] = {}

    def fake_evaluate_policy(
        run_artifact: dict,
        *,
        policy: PolicySpec | None = None,
        policy_path: str | Path | None = None,
        config_path: str | Path | None = None,
    ) -> dict:
        called.update(
            {
                "run_artifact": run_artifact,
                "policy": policy,
                "policy_path": policy_path,
                "config_path": config_path,
            }
        )
        return {"passed": True, "violations": [], "checks": {"max_estimated_input_tokens": {"passed": True}}}

    monkeypatch.setattr(engine, "evaluate_policy", fake_evaluate_policy)
    middleware = ContextBudgetMiddleware(engine=engine)
    result = middleware.prepare_context("update auth flow", repo=tmp_path, max_tokens=200)
    policy = PolicySpec(max_estimated_input_tokens=200)

    checked = middleware.enforce_budget(result, policy=policy)

    assert called["run_artifact"] == result.run_artifact
    assert called["policy"] == policy
    assert checked.policy_result == {"passed": True, "violations": [], "checks": {"max_estimated_input_tokens": {"passed": True}}}
    assert checked.run_artifact["policy"]["passed"] is True


def test_record_run_writes_machine_readable_middleware_artifact(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    result = prepare_context("update auth flow", repo=tmp_path, max_tokens=400)
    policy = ContextBudgetEngine.make_policy(max_estimated_input_tokens=400)
    checked = enforce_budget(result, policy=policy)
    output_path = record_run(checked, tmp_path / "agent-run.json")

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["task"] == "update auth flow"
    assert data["agent_middleware"]["request"]["task"] == "update auth flow"
    assert data["agent_middleware"]["metadata"]["files_included_count"] == len(data["files_included"])
    assert data["agent_middleware"]["recorded_path"] == str(output_path)

    summary = ContextBudgetEngine().report(output_path)
    assert summary["task"] == "update auth flow"


def test_local_demo_adapter_simulates_agent_workflow(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(tmp_path / "src" / "permissions.py", "def allow() -> bool:\n    return True\n")

    middleware = ContextBudgetMiddleware()
    adapter = LocalDemoAgentAdapter()
    request = AgentTaskRequest(task="update auth flow", repo=tmp_path, max_tokens=400)

    run = adapter.run(request, middleware, record_path=tmp_path / "demo-run.json")

    assert run.adapter == "local_demo"
    assert "Simulated agent received" in run.response
    assert "Task: update auth flow" in run.prompt_preview
    assert run.middleware_result.adapter_name == "local_demo"
    assert run.metadata["recorded_artifact"].endswith("demo-run.json")
    assert (tmp_path / "demo-run.json").exists()
    assert run.as_dict()["context"]["agent_middleware"]["adapter"] == "local_demo"


def test_enforce_budget_strict_raises_middleware_violation(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    result = prepare_context("update auth flow", repo=tmp_path, max_tokens=400)
    with pytest.raises(BudgetPolicyViolationError):
        enforce_budget(
            result,
            PolicySpec(max_estimated_input_tokens=1),
            strict=True,
        )
