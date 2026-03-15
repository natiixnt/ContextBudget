from __future__ import annotations

"""Tests for redcon.integrations — all four wrappers.

Tests use monkeypatching to avoid real LLM / subprocess calls:
- OpenAIAgentWrapper  — patches _call_openai
- AnthropicAgentWrapper — patches _call_anthropic
- GenericAgentRunner — passes a no-op llm_fn
- NodeJSAgentRunner — patches _call_nodejs (and subprocess.run for error paths)
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from redcon.integrations import (
    AnthropicAgentWrapper,
    GenericAgentRunner,
    NodeJSAgentRunner,
    OpenAIAgentWrapper,
)
from redcon.runtime import RuntimeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def simple_repo(tmp_path: Path) -> Path:
    _write(tmp_path / "src" / "cache.py", "def cache_get(key: str) -> str | None:\n    return None\n")
    _write(tmp_path / "src" / "api.py", "def get_item(key: str) -> str:\n    return cache_get(key) or 'miss'\n")
    return tmp_path


# ---------------------------------------------------------------------------
# OpenAIAgentWrapper
# ---------------------------------------------------------------------------


class TestOpenAIAgentWrapper:
    def test_run_task_returns_runtime_result(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = OpenAIAgentWrapper(model="gpt-4.1", repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "openai response")

        result = agent.run_task("add caching", repo=simple_repo)

        assert isinstance(result, RuntimeResult)
        assert result.llm_response == "openai response"
        assert result.turn_number == 1
        assert result.session_id

    def test_run_task_uses_default_repo_when_none(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = OpenAIAgentWrapper(model="gpt-4.1", repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")

        result = agent.run_task("add caching")

        assert result.prepared_context.repo == str(simple_repo)

    def test_run_task_accumulates_session_turns(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = OpenAIAgentWrapper(model="gpt-4.1", repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")

        agent.run_task("task 1", repo=simple_repo)
        result2 = agent.run_task("task 2", repo=simple_repo)

        assert result2.turn_number == 2
        assert result2.session_tokens > 0

    def test_session_summary_reflects_turns(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = OpenAIAgentWrapper(repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")

        agent.run_task("add caching", repo=simple_repo)
        summary = agent.session_summary()

        assert summary["turn_count"] == 1
        assert "session_id" in summary

    def test_reset_session_clears_state(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = OpenAIAgentWrapper(repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")
        agent.run_task("task", repo=simple_repo)

        agent.reset_session()

        # After reset, turns is empty so turn_number returns 1 (len([]) + 1)
        assert agent.session.turn_number == 1

    def test_telemetry_written_to_observe_history(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = OpenAIAgentWrapper(repo=simple_repo, telemetry_base_dir=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")
        agent.run_task("add caching", repo=simple_repo)

        history_path = simple_repo / ".redcon" / "observe-history.json"
        assert history_path.exists()
        import json
        data = json.loads(history_path.read_text())
        assert data["entries"][-1]["adapter"] == "openai"

    def test_prepared_context_contains_prompt_text(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}
        agent = OpenAIAgentWrapper(repo=simple_repo)

        def capture_prompt(prompt: str) -> str:
            captured["prompt"] = prompt
            return "response"

        monkeypatch.setattr(agent._runtime, "_llm_fn", capture_prompt)
        agent.run_task("add caching", repo=simple_repo)

        assert "prompt" in captured
        assert isinstance(captured["prompt"], str)

    def test_get_client_raises_import_error_when_openai_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins
        real_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        agent = OpenAIAgentWrapper()
        monkeypatch.setattr(builtins, "__import__", mock_import)

        with pytest.raises(ImportError, match="openai"):
            agent._get_client()


# ---------------------------------------------------------------------------
# AnthropicAgentWrapper
# ---------------------------------------------------------------------------


class TestAnthropicAgentWrapper:
    def test_run_task_returns_runtime_result(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = AnthropicAgentWrapper(model="claude-sonnet-4-6", repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "anthropic response")

        result = agent.run_task("add caching", repo=simple_repo)

        assert isinstance(result, RuntimeResult)
        assert result.llm_response == "anthropic response"

    def test_run_task_uses_default_repo_when_none(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = AnthropicAgentWrapper(repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")

        result = agent.run_task("add caching")

        assert result.prepared_context.repo == str(simple_repo)

    def test_multi_turn_session_increments(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = AnthropicAgentWrapper(repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")

        r1 = agent.run_task("task 1", repo=simple_repo)
        r2 = agent.run_task("task 2", repo=simple_repo)

        assert r1.turn_number == 1
        assert r2.turn_number == 2

    def test_telemetry_written_to_observe_history(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = AnthropicAgentWrapper(repo=simple_repo, telemetry_base_dir=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")
        agent.run_task("add caching", repo=simple_repo)

        history_path = simple_repo / ".redcon" / "observe-history.json"
        assert history_path.exists()
        import json
        data = json.loads(history_path.read_text())
        assert data["entries"][-1]["adapter"] == "anthropic"

    def test_get_client_raises_import_error_when_anthropic_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins
        real_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        agent = AnthropicAgentWrapper()
        monkeypatch.setattr(builtins, "__import__", mock_import)

        with pytest.raises(ImportError, match="anthropic"):
            agent._get_client()

    def test_reset_session(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = AnthropicAgentWrapper(repo=simple_repo)
        monkeypatch.setattr(agent._runtime, "_llm_fn", lambda prompt: "ok")
        agent.run_task("task", repo=simple_repo)

        agent.reset_session()

        assert agent.session.turn_number == 1


# ---------------------------------------------------------------------------
# GenericAgentRunner
# ---------------------------------------------------------------------------


class TestGenericAgentRunner:
    def test_run_task_calls_llm_fn(self, simple_repo: Path) -> None:
        calls: list[str] = []

        def my_llm(prompt: str) -> str:
            calls.append(prompt)
            return "generic response"

        runner = GenericAgentRunner(llm_fn=my_llm, repo=simple_repo)
        result = runner.run_task("add caching", repo=simple_repo)

        assert result.llm_response == "generic response"
        assert len(calls) == 1

    def test_run_task_uses_default_repo(self, simple_repo: Path) -> None:
        runner = GenericAgentRunner(llm_fn=lambda p: "ok", repo=simple_repo)
        result = runner.run_task("add caching")

        assert result.prepared_context.repo == str(simple_repo)

    def test_adapter_name_appears_in_telemetry(
        self, simple_repo: Path
    ) -> None:
        runner = GenericAgentRunner(
            llm_fn=lambda p: "ok",
            repo=simple_repo,
            adapter_name="my-custom-llm",
            telemetry_base_dir=simple_repo,
        )
        runner.run_task("add caching", repo=simple_repo)

        history_path = simple_repo / ".redcon" / "observe-history.json"
        assert history_path.exists()
        import json
        data = json.loads(history_path.read_text())
        assert data["entries"][-1]["adapter"] == "my-custom-llm"

    def test_multi_turn_delta_context(self, simple_repo: Path) -> None:
        runner = GenericAgentRunner(llm_fn=lambda p: "ok", repo=simple_repo, delta=True)

        r1 = runner.run_task("task 1", repo=simple_repo)
        r2 = runner.run_task("task 2", repo=simple_repo)

        assert r1.turn_number == 1
        assert r2.turn_number == 2

    def test_session_summary_returns_dict(self, simple_repo: Path) -> None:
        runner = GenericAgentRunner(llm_fn=lambda p: "ok", repo=simple_repo)
        runner.run_task("task", repo=simple_repo)

        summary = runner.session_summary()

        assert isinstance(summary, dict)
        assert summary["turn_count"] == 1

    def test_reset_session(self, simple_repo: Path) -> None:
        runner = GenericAgentRunner(llm_fn=lambda p: "ok", repo=simple_repo)
        runner.run_task("task", repo=simple_repo)

        runner.reset_session()

        # After reset turns list is empty; turn_number is len([]) + 1 = 1
        assert runner.session.turn_number == 1


# ---------------------------------------------------------------------------
# NodeJSAgentRunner
# ---------------------------------------------------------------------------


class TestNodeJSAgentRunner:
    def test_run_task_returns_runtime_result(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)
        monkeypatch.setattr(runner._runtime, "_llm_fn", lambda prompt: "node response")

        result = runner.run_task("add caching", repo=simple_repo)

        assert isinstance(result, RuntimeResult)
        assert result.llm_response == "node response"

    def test_run_task_uses_default_repo_when_none(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)
        monkeypatch.setattr(runner._runtime, "_llm_fn", lambda prompt: "ok")

        result = runner.run_task("add caching")

        assert result.prepared_context.repo == str(simple_repo)

    def test_command_list_takes_precedence_over_script(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(
            command=["node", "--experimental-vm-modules", "agent.js"],
            repo=simple_repo,
        )
        assert runner._command == ["node", "--experimental-vm-modules", "agent.js"]

    def test_script_builds_default_command(self, simple_repo: Path) -> None:
        runner = NodeJSAgentRunner(script="my_agent.js", repo=simple_repo)
        assert runner._command == ["node", "my_agent.js"]

    def test_custom_node_executable(self, simple_repo: Path) -> None:
        runner = NodeJSAgentRunner(
            script="agent.js",
            node_executable="/usr/local/bin/node",
            repo=simple_repo,
        )
        assert runner._command[0] == "/usr/local/bin/node"

    def test_missing_script_and_command_raises(self) -> None:
        with pytest.raises(ValueError, match="script.*command"):
            NodeJSAgentRunner()  # type: ignore[call-arg]

    def test_multi_turn_session_increments(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)
        monkeypatch.setattr(runner._runtime, "_llm_fn", lambda prompt: "ok")

        r1 = runner.run_task("task 1", repo=simple_repo)
        r2 = runner.run_task("task 2", repo=simple_repo)

        assert r1.turn_number == 1
        assert r2.turn_number == 2

    def test_telemetry_written_with_command_field(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(
            script="agent.js",
            repo=simple_repo,
            adapter_name="nodejs-test",
            telemetry_base_dir=simple_repo,
        )
        monkeypatch.setattr(runner._runtime, "_llm_fn", lambda prompt: "ok")
        runner.run_task("add caching", repo=simple_repo)

        history_path = simple_repo / ".redcon" / "observe-history.json"
        assert history_path.exists()
        import json
        data = json.loads(history_path.read_text())
        entry = data["entries"][-1]
        assert entry["adapter"] == "nodejs-test"
        assert isinstance(entry["command"], list)

    def test_reset_session(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)
        monkeypatch.setattr(runner._runtime, "_llm_fn", lambda prompt: "ok")
        runner.run_task("task", repo=simple_repo)

        runner.reset_session()

        assert runner.session.turn_number == 1

    def test_call_nodejs_raises_on_nonzero_exit(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)
        failed = subprocess.CompletedProcess(
            args=["node", "agent.js"],
            returncode=1,
            stdout="",
            stderr="SyntaxError: Unexpected token",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: failed)

        with pytest.raises(RuntimeError, match="exited with code 1"):
            runner._call_nodejs("prompt text")

    def test_call_nodejs_raises_when_node_not_found(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)

        def raise_file_not_found(*args: Any, **kwargs: Any) -> None:
            raise FileNotFoundError("node: not found")

        monkeypatch.setattr(subprocess, "run", raise_file_not_found)

        with pytest.raises(RuntimeError, match="Node.js executable not found"):
            runner._call_nodejs("prompt text")

    def test_call_nodejs_raises_on_timeout(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo, timeout=5.0)

        def raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=["node", "agent.js"], timeout=5.0)

        monkeypatch.setattr(subprocess, "run", raise_timeout)

        with pytest.raises(RuntimeError, match="timed out"):
            runner._call_nodejs("prompt text")

    def test_call_nodejs_returns_stdout_on_success(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)
        success = subprocess.CompletedProcess(
            args=["node", "agent.js"],
            returncode=0,
            stdout="Hello from Node.js",
            stderr="",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: success)

        response = runner._call_nodejs("prompt text")

        assert response == "Hello from Node.js"

    def test_env_vars_merged_with_os_env(self, simple_repo: Path) -> None:
        import os
        runner = NodeJSAgentRunner(
            script="agent.js",
            repo=simple_repo,
            env={"MY_API_KEY": "secret"},
        )
        merged = runner._build_env()

        assert merged is not None
        assert merged["MY_API_KEY"] == "secret"
        # Original env vars are preserved
        assert "PATH" in merged or len(os.environ) == 0

    def test_no_env_returns_none(self, simple_repo: Path) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)
        assert runner._build_env() is None

    def test_session_summary_returns_dict(
        self, simple_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = NodeJSAgentRunner(script="agent.js", repo=simple_repo)
        monkeypatch.setattr(runner._runtime, "_llm_fn", lambda prompt: "ok")
        runner.run_task("task", repo=simple_repo)

        summary = runner.session_summary()

        assert isinstance(summary, dict)
        assert summary["turn_count"] == 1
