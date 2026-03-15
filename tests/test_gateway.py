from __future__ import annotations

"""Tests for the ContextBudget Runtime Gateway.

Covers:
- GatewayConfig construction and env-var loading
- GatewayHandlers: prepare-context, run-step, report-run
- GatewayServer: HTTP routing (start in background thread, send real HTTP requests)
- Response shape: optimized_context, token_estimate, cache_hits always present
- /run-step alias behaves identically to /run-agent-step
- Error handling: missing required fields → 400, unknown path → 404
- Telemetry emission on every handler call
- Session reuse across /run-step calls
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contextbudget.agents.middleware import AgentMiddlewareResult, AgentTaskRequest
from contextbudget.gateway import GatewayConfig, GatewayHandlers, GatewayServer
from contextbudget.gateway.models import (
    OptimizedContext,
    PolicyStatus,
    PrepareContextRequest,
    PrepareContextResponse,
    ReportRunRequest,
    ReportRunResponse,
    RunAgentStepRequest,
    RunAgentStepResponse,
)
from contextbudget.runtime.context import PreparedContext, RuntimeResult
from contextbudget.runtime.session import RuntimeSession
from contextbudget.telemetry import TelemetryEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_run_artifact(
    *,
    estimated_tokens: int = 5000,
    saved_tokens: int = 2000,
    cache_hits: int = 3,
    files: list[str] | None = None,
) -> dict[str, Any]:
    files = files or ["src/main.py", "src/utils.py"]
    compressed = [
        {
            "path": p,
            "strategy": "full_file",
            "original_tokens": 1000,
            "compressed_tokens": 800,
            "text": f"# content of {p}",
        }
        for p in files
    ]
    return {
        "task": "add caching",
        "repo": ".",
        "estimated_input_tokens": estimated_tokens,
        "estimated_saved_tokens": saved_tokens,
        "compressed_context": compressed,
        "files_included": files,
        "budget": {"quality_risk_estimate": "low"},
        "cache": {"hits": cache_hits, "misses": 1, "tokens_saved": 400},
    }


def _fake_middleware_result(artifact: dict[str, Any] | None = None) -> AgentMiddlewareResult:
    artifact = artifact or _fake_run_artifact()
    return AgentMiddlewareResult(
        request=AgentTaskRequest(task="add caching", repo="."),
        run_artifact=artifact,
        metadata={
            "estimated_input_tokens": artifact.get("estimated_input_tokens", 0),
            "estimated_saved_tokens": artifact.get("estimated_saved_tokens", 0),
            "cache": artifact.get("cache", {}),
        },
        policy_result=None,
    )


def _fake_prepared_context(artifact: dict[str, Any] | None = None) -> PreparedContext:
    artifact = artifact or _fake_run_artifact()
    files = artifact.get("files_included", [])
    return PreparedContext(
        task="add caching",
        repo=".",
        prompt_text="# File: src/main.py\n# content of src/main.py\n",
        files_included=files,
        estimated_tokens=artifact.get("estimated_input_tokens", 0),
        tokens_saved=artifact.get("estimated_saved_tokens", 0),
        quality_risk="low",
        policy_passed=None,
        policy_violations=[],
        cache_hits=artifact.get("cache", {}).get("hits", 0),
        run_artifact=artifact,
    )


def _fake_runtime_result(ctx: PreparedContext | None = None) -> RuntimeResult:
    ctx = ctx or _fake_prepared_context()
    return RuntimeResult(
        prepared_context=ctx,
        llm_response=None,
        turn_number=1,
        session_tokens=ctx.estimated_tokens,
        session_id="test-session-id",
    )


# ---------------------------------------------------------------------------
# GatewayConfig
# ---------------------------------------------------------------------------


class TestGatewayConfig:
    def test_defaults(self):
        cfg = GatewayConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8787
        assert cfg.max_tokens == 128_000
        assert cfg.max_files == 100
        assert cfg.telemetry_enabled is False
        assert cfg.log_requests is True

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("CB_GATEWAY_HOST", "0.0.0.0")
        monkeypatch.setenv("CB_GATEWAY_PORT", "9090")
        monkeypatch.setenv("CB_GATEWAY_MAX_TOKENS", "64000")
        monkeypatch.setenv("CB_GATEWAY_MAX_FILES", "50")
        monkeypatch.setenv("CB_GATEWAY_TELEMETRY", "true")
        monkeypatch.setenv("CB_GATEWAY_LOG_REQUESTS", "false")

        cfg = GatewayConfig.from_env()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9090
        assert cfg.max_tokens == 64_000
        assert cfg.max_files == 50
        assert cfg.telemetry_enabled is True
        assert cfg.log_requests is False

    def test_from_dict_ignores_unknown_keys(self):
        cfg = GatewayConfig.from_dict({"port": 9000, "unknown_key": "ignored"})
        assert cfg.port == 9000

    def test_from_env_defaults_when_vars_absent(self, monkeypatch):
        for key in [
            "CB_GATEWAY_HOST", "CB_GATEWAY_PORT", "CB_GATEWAY_MAX_TOKENS",
            "CB_GATEWAY_MAX_FILES", "CB_GATEWAY_TELEMETRY", "CB_GATEWAY_LOG_REQUESTS",
        ]:
            monkeypatch.delenv(key, raising=False)
        cfg = GatewayConfig.from_env()
        assert cfg == GatewayConfig()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_prepare_context_request_from_dict_minimal(self):
        req = PrepareContextRequest.from_dict({"task": "fix bug"})
        assert req.task == "fix bug"
        assert req.repo == "."
        assert req.max_tokens is None
        assert req.metadata == {}

    def test_prepare_context_request_from_dict_full(self):
        req = PrepareContextRequest.from_dict({
            "task": "fix bug",
            "repo": "/tmp/repo",
            "max_tokens": 16_000,
            "max_files": 20,
            "top_files": 10,
            "session_id": "abc",
            "metadata": {"key": "val"},
        })
        assert req.max_tokens == 16_000
        assert req.max_files == 20
        assert req.top_files == 10
        assert req.session_id == "abc"

    def test_run_agent_step_request_missing_task_raises(self):
        with pytest.raises(KeyError):
            RunAgentStepRequest.from_dict({})

    def test_report_run_request_from_dict(self):
        req = ReportRunRequest.from_dict({
            "session_id": "s1",
            "run_id": "r1",
            "status": "success",
            "tokens_used": 8000,
        })
        assert req.status == "success"
        assert req.tokens_used == 8000

    def test_optimized_context_as_dict(self):
        ctx = OptimizedContext(
            files=[{"path": "a.py", "text": "x"}],
            prompt_text="# File: a.py\nx",
            files_included=["a.py"],
        )
        d = ctx.as_dict()
        assert "files" in d
        assert "prompt_text" in d
        assert "files_included" in d

    def test_prepare_context_response_as_dict_has_required_keys(self):
        resp = PrepareContextResponse(
            optimized_context=OptimizedContext(files=[], prompt_text="", files_included=[]),
            token_estimate=5000,
            policy_status=PolicyStatus(passed=True, violations=[]),
            run_id="r1",
            session_id="s1",
            cache_hits=2,
            quality_risk="low",
            tokens_saved=1000,
        )
        d = resp.as_dict()
        # Required keys per spec
        assert "optimized_context" in d
        assert "token_estimate" in d
        assert "cache_hits" in d


# ---------------------------------------------------------------------------
# GatewayHandlers (unit tests with mocked engine)
# ---------------------------------------------------------------------------


class TestGatewayHandlers:
    @pytest.fixture
    def config(self):
        return GatewayConfig(max_tokens=32_000, max_files=50)

    @pytest.fixture
    def telemetry_sink(self):
        sink = MagicMock()
        sink.emit = MagicMock()
        return sink

    @pytest.fixture
    def handlers(self, config, telemetry_sink):
        with patch("contextbudget.gateway.handlers.ContextBudgetEngine"):
            with patch("contextbudget.gateway.handlers.ContextBudgetMiddleware") as MockMW:
                middleware_instance = MagicMock()
                MockMW.return_value = middleware_instance
                h = GatewayHandlers(config, telemetry_sink=telemetry_sink)
                h._middleware = middleware_instance
                return h, middleware_instance

    def test_prepare_context_returns_response(self, handlers):
        h, mw = handlers
        artifact = _fake_run_artifact(estimated_tokens=8000, cache_hits=4)
        mw.prepare_context.return_value = _fake_middleware_result(artifact)
        mw.enforce_budget.return_value = _fake_middleware_result(artifact)

        req = PrepareContextRequest(task="add caching", repo=".", max_tokens=32_000)
        resp = h.handle_prepare_context(req)

        assert isinstance(resp, PrepareContextResponse)
        assert resp.token_estimate == 8000
        assert resp.cache_hits == 4
        assert resp.quality_risk == "low"
        assert isinstance(resp.optimized_context, OptimizedContext)

    def test_prepare_context_response_dict_has_spec_keys(self, handlers):
        h, mw = handlers
        artifact = _fake_run_artifact()
        mw.prepare_context.return_value = _fake_middleware_result(artifact)
        mw.enforce_budget.return_value = _fake_middleware_result(artifact)

        resp = h.handle_prepare_context(PrepareContextRequest(task="fix", repo="."))
        d = resp.as_dict()

        assert "optimized_context" in d
        assert "token_estimate" in d
        assert "cache_hits" in d

    def test_prepare_context_emits_telemetry(self, handlers, telemetry_sink):
        h, mw = handlers
        artifact = _fake_run_artifact()
        mw.prepare_context.return_value = _fake_middleware_result(artifact)
        mw.enforce_budget.return_value = _fake_middleware_result(artifact)

        h.handle_prepare_context(PrepareContextRequest(task="task", repo="."))

        assert telemetry_sink.emit.call_count >= 2
        names = [c.args[0].name for c in telemetry_sink.emit.call_args_list]
        assert any("started" in n for n in names)
        assert any("completed" in n for n in names)

    def test_handle_run_agent_step_creates_session(self, handlers):
        h, mw = handlers
        ctx = _fake_prepared_context()
        with patch.object(h, "_sessions", {}):
            with patch("contextbudget.gateway.handlers.AgentRuntime") as MockRT:
                rt_instance = MagicMock()
                rt_instance.session.session_id = "new-session"
                rt_instance.session.turn_number = 0
                rt_instance.run.return_value = _fake_runtime_result(ctx)
                MockRT.return_value = rt_instance

                req = RunAgentStepRequest(task="fix bug", repo=".", session_id=None)
                resp = h.handle_run_agent_step(req)

                assert isinstance(resp, RunAgentStepResponse)
                assert resp.token_estimate == ctx.estimated_tokens
                assert resp.cache_hits == ctx.cache_hits

    def test_report_run_returns_acknowledged(self, handlers, telemetry_sink):
        h, _ = handlers
        req = ReportRunRequest(
            session_id="s1", run_id="r1", status="success", tokens_used=5000
        )
        resp = h.handle_report_run(req)

        assert isinstance(resp, ReportRunResponse)
        assert resp.acknowledged is True
        assert resp.session_id == "s1"
        assert resp.run_id == "r1"
        telemetry_sink.emit.assert_called_once()
        event: TelemetryEvent = telemetry_sink.emit.call_args.args[0]
        assert "reported" in event.name

    def test_effective_max_tokens_falls_back_to_config(self, handlers):
        h, _ = handlers
        assert h._effective_max_tokens(None) == 32_000
        assert h._effective_max_tokens(8_000) == 8_000

    def test_effective_top_files_falls_back_to_config(self, handlers):
        h, _ = handlers
        assert h._effective_top_files(None) == 50
        assert h._effective_top_files(10) == 10


# ---------------------------------------------------------------------------
# GatewayServer HTTP integration (real socket, background thread)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Find a free TCP port on localhost."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Send a POST request and return (status_code, parsed_body)."""
    raw = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestGatewayServerHTTP:
    """Integration tests: real HTTP server on a free port, mocked handlers."""

    @pytest.fixture(autouse=True)
    def server(self):
        port = _free_port()
        config = GatewayConfig(host="127.0.0.1", port=port, log_requests=False)

        # Build a handlers instance with the engine mocked out
        artifact = _fake_run_artifact(estimated_tokens=7500, cache_hits=5)
        mw_result = _fake_middleware_result(artifact)
        ctx = _fake_prepared_context(artifact)
        rt_result = _fake_runtime_result(ctx)

        with patch("contextbudget.gateway.handlers.ContextBudgetEngine"):
            with patch("contextbudget.gateway.handlers.ContextBudgetMiddleware") as MockMW:
                mw_inst = MagicMock()
                mw_inst.prepare_context.return_value = mw_result
                mw_inst.enforce_budget.return_value = mw_result
                MockMW.return_value = mw_inst

                with patch("contextbudget.gateway.handlers.AgentRuntime") as MockRT:
                    rt_inst = MagicMock()
                    rt_inst.session.session_id = "srv-session"
                    rt_inst.session.turn_number = 0
                    rt_inst.run.return_value = rt_result
                    MockRT.return_value = rt_inst

                    handlers = GatewayHandlers(config)
                    handlers._middleware = mw_inst
                    srv = GatewayServer(config, handlers=handlers)
                    srv.start(block=False)
                    time.sleep(0.1)  # let the thread bind
                    self._base = f"http://127.0.0.1:{port}"
                    yield
                    srv.stop()

    def test_prepare_context_200(self):
        status, body = _post(f"{self._base}/prepare-context", {"task": "add caching"})
        assert status == 200
        assert "optimized_context" in body
        assert "token_estimate" in body
        assert "cache_hits" in body

    def test_run_step_200(self):
        """POST /run-step must work (canonical spec endpoint)."""
        status, body = _post(f"{self._base}/run-step", {"task": "add caching"})
        assert status == 200
        assert "optimized_context" in body
        assert "token_estimate" in body
        assert "cache_hits" in body

    def test_run_agent_step_200(self):
        """Legacy /run-agent-step must still work."""
        status, body = _post(f"{self._base}/run-agent-step", {"task": "add caching"})
        assert status == 200

    def test_report_run_200(self):
        status, body = _post(
            f"{self._base}/report-run",
            {"session_id": "s1", "run_id": "r1", "status": "success"},
        )
        assert status == 200
        assert body["acknowledged"] is True

    def test_unknown_endpoint_404(self):
        status, body = _post(f"{self._base}/does-not-exist", {"task": "x"})
        assert status == 404
        assert "error" in body

    def test_missing_required_field_400(self):
        # /prepare-context requires "task"
        status, body = _post(f"{self._base}/prepare-context", {})
        assert status == 400

    def test_invalid_json_400(self):
        raw = b"not-json"
        req = urllib.request.Request(
            f"{self._base}/prepare-context",
            data=raw,
            headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status, body = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            status, body = exc.code, json.loads(exc.read())
        assert status == 400
        assert "error" in body

    def test_optimized_context_shape(self):
        _, body = _post(f"{self._base}/prepare-context", {"task": "fix"})
        oc = body["optimized_context"]
        assert "files" in oc
        assert "prompt_text" in oc
        assert "files_included" in oc

    def test_token_estimate_is_int(self):
        _, body = _post(f"{self._base}/prepare-context", {"task": "fix"})
        assert isinstance(body["token_estimate"], int)

    def test_cache_hits_is_int(self):
        _, body = _post(f"{self._base}/prepare-context", {"task": "fix"})
        assert isinstance(body["cache_hits"], int)


# ---------------------------------------------------------------------------
# SDK gateway exports
# ---------------------------------------------------------------------------


class TestSDKGatewayExports:
    def test_gateway_config_importable_from_sdk(self):
        from contextbudget.sdk import GatewayConfig as SDKGatewayConfig
        assert SDKGatewayConfig is GatewayConfig

    def test_gateway_server_importable_from_sdk(self):
        from contextbudget.sdk import GatewayServer as SDKGatewayServer
        assert SDKGatewayServer is GatewayServer

    def test_start_gateway_importable(self):
        from contextbudget.sdk import start_gateway
        assert callable(start_gateway)

    def test_start_gateway_starts_server(self):
        from contextbudget.sdk import start_gateway

        port = _free_port()
        with patch("contextbudget.gateway.handlers.ContextBudgetEngine"):
            with patch("contextbudget.gateway.handlers.ContextBudgetMiddleware") as MockMW:
                mw_inst = MagicMock()
                artifact = _fake_run_artifact()
                mw_inst.prepare_context.return_value = _fake_middleware_result(artifact)
                mw_inst.enforce_budget.return_value = _fake_middleware_result(artifact)
                MockMW.return_value = mw_inst

                srv = start_gateway(port=port, block=False)
                time.sleep(0.1)
                try:
                    status, _ = _post(
                        f"http://127.0.0.1:{port}/prepare-context", {"task": "test"}
                    )
                    assert status == 200
                finally:
                    srv.stop()
