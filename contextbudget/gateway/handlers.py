from __future__ import annotations

"""Endpoint handlers for the ContextBudget Runtime Gateway."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from contextbudget.agents.middleware import ContextBudgetMiddleware
from contextbudget.core.policy import PolicySpec
from contextbudget.engine import ContextBudgetEngine
from contextbudget.runtime import AgentRuntime
from contextbudget.telemetry import NoOpTelemetrySink, TelemetryEvent, TelemetrySink

from contextbudget.gateway.config import GatewayConfig
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

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_compressed_files(run_artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull per-file compression entries from a pipeline run artifact."""
    files = []
    for entry in run_artifact.get("compressed_context") or []:
        if not isinstance(entry, dict):
            continue
        files.append(
            {
                "path": str(entry.get("path") or ""),
                "strategy": str(entry.get("strategy") or "full_file"),
                "original_tokens": int(entry.get("original_tokens") or 0),
                "compressed_tokens": int(entry.get("compressed_tokens") or 0),
                "text": str(entry.get("text") or ""),
            }
        )
    return files


def _build_policy_spec(
    max_tokens: int | None,
    max_files: int | None,
    max_context_size: int | None,
) -> PolicySpec | None:
    """Return a PolicySpec from the effective budget constraints, or None."""
    if max_tokens is None and max_files is None and max_context_size is None:
        return None
    return PolicySpec(
        max_estimated_input_tokens=max_tokens,
        max_files_included=max_files,
        max_context_size_bytes=max_context_size,
    )


def _policy_status_from_result(policy_result: dict[str, Any] | None) -> PolicyStatus:
    if policy_result is None:
        return PolicyStatus(passed=True, violations=[])
    passed = bool(policy_result.get("passed", True))
    violations = [str(v) for v in (policy_result.get("violations") or []) if v]
    return PolicyStatus(passed=passed, violations=violations)


def _build_prompt_text(files: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for f in files:
        if f["path"]:
            parts.append(f"# File: {f['path']}")
        if f["text"]:
            parts.append(f["text"])
        parts.append("")
    return "\n".join(parts)


class GatewayHandlers:
    """Stateful container that implements the three gateway endpoint handlers.

    A single :class:`ContextBudgetEngine` is shared across all requests so
    that the on-disk summary cache is reused without additional coordination.
    Each ``/run-agent-step`` session maps to a dedicated
    :class:`~contextbudget.runtime.AgentRuntime` stored in an in-memory
    session registry; the runtime handles delta-context propagation between
    turns automatically.

    Parameters
    ----------
    config:
        Gateway configuration (host, port, budget defaults).
    telemetry_sink:
        Optional sink for gateway-level telemetry events.  Defaults to the
        no-op sink so events are silently dropped when telemetry is disabled.
    """

    def __init__(
        self,
        config: GatewayConfig,
        *,
        telemetry_sink: TelemetrySink | None = None,
    ) -> None:
        self._config = config
        self._sink: TelemetrySink = telemetry_sink or NoOpTelemetrySink()

        # Shared engine — all requests reuse the same on-disk cache
        self._engine = ContextBudgetEngine(
            config_path=config.config_path or None,
        )
        self._middleware = ContextBudgetMiddleware(engine=self._engine)

        # session_id → AgentRuntime (multi-turn state)
        self._sessions: dict[str, AgentRuntime] = {}

    # ── Telemetry ──────────────────────────────────────────────────────────────

    def _emit(self, name: str, **payload: Any) -> None:
        self._sink.emit(
            TelemetryEvent(
                name=name,
                schema_version="1.0",
                timestamp=_utc_now(),
                run_id=str(payload.get("run_id", uuid.uuid4().hex)),
                payload={k: v for k, v in payload.items() if v is not None},
            )
        )

    # ── Budget resolution ──────────────────────────────────────────────────────

    def _effective_max_tokens(self, request_value: int | None) -> int:
        return request_value if request_value is not None else self._config.max_tokens

    def _effective_top_files(self, request_value: int | None) -> int:
        return request_value if request_value is not None else self._config.max_files

    def _effective_max_context_size(self, request_value: int | None) -> int:
        return (
            request_value
            if request_value is not None
            else self._config.max_context_size
        )

    # ── Endpoint handlers ──────────────────────────────────────────────────────

    def handle_prepare_context(
        self, req: PrepareContextRequest
    ) -> PrepareContextResponse:
        """Handle ``POST /prepare-context``.

        Runs the full optimization pipeline (scan → rank → compress → cache)
        and evaluates the budget policy.  Stateless — no session is created.
        """
        run_id = uuid.uuid4().hex
        max_tokens = self._effective_max_tokens(req.max_tokens)
        top_files = self._effective_top_files(req.top_files)
        max_context_size = self._effective_max_context_size(req.max_context_size)
        max_files = req.max_files if req.max_files is not None else self._config.max_files

        self._emit(
            "gateway.prepare_context.started",
            run_id=run_id,
            task=req.task,
            repo=req.repo,
            max_tokens=max_tokens,
        )

        policy = _build_policy_spec(
            max_tokens=max_tokens,
            max_files=max_files,
            max_context_size=max_context_size,
        )

        result = self._middleware.prepare_context(
            req.task,
            req.repo,
            workspace=req.workspace,
            max_tokens=max_tokens,
            top_files=top_files,
            delta_from=req.delta_from,
            config_path=req.config_path,
            metadata=req.metadata,
        )

        if policy is not None:
            result = self._middleware.enforce_budget(result, policy=policy, strict=False)

        run_artifact = result.run_artifact
        meta = result.metadata
        budget = run_artifact.get("budget") or {}

        compressed_files = _extract_compressed_files(run_artifact)
        prompt_text = _build_prompt_text(compressed_files)
        files_included = [f["path"] for f in compressed_files if f["path"]]

        policy_status = _policy_status_from_result(result.policy_result)
        session_id = req.session_id or uuid.uuid4().hex
        estimated_tokens = int(meta.get("estimated_input_tokens") or 0)
        tokens_saved = int(meta.get("estimated_saved_tokens") or 0)
        cache_report = meta.get("cache") or {}
        cache_hits = int(
            cache_report.get("hits") if isinstance(cache_report, dict) else 0
        )
        quality_risk = str(budget.get("quality_risk_estimate") or "unknown")

        self._emit(
            "gateway.prepare_context.completed",
            run_id=run_id,
            session_id=session_id,
            estimated_tokens=estimated_tokens,
            tokens_saved=tokens_saved,
            policy_passed=policy_status.passed,
            cache_hits=cache_hits,
        )

        return PrepareContextResponse(
            optimized_context=OptimizedContext(
                files=compressed_files,
                prompt_text=prompt_text,
                files_included=files_included,
            ),
            token_estimate=estimated_tokens,
            policy_status=policy_status,
            run_id=run_id,
            session_id=session_id,
            cache_hits=cache_hits,
            quality_risk=quality_risk,
            tokens_saved=tokens_saved,
        )

    def handle_run_agent_step(
        self, req: RunAgentStepRequest
    ) -> RunAgentStepResponse:
        """Handle ``POST /run-agent-step``.

        Runs one agent turn against the optimization pipeline.  On subsequent
        calls with the same ``session_id`` the runtime automatically uses the
        previous run artifact as delta context so only changed files are
        re-sent.
        """
        max_tokens = self._effective_max_tokens(req.max_tokens)
        top_files = self._effective_top_files(req.top_files)
        max_context_size = self._effective_max_context_size(req.max_context_size)
        max_files = req.max_files if req.max_files is not None else self._config.max_files

        policy = _build_policy_spec(
            max_tokens=max_tokens,
            max_files=max_files,
            max_context_size=max_context_size,
        )

        # Retrieve or create the AgentRuntime for this session
        session_id = req.session_id
        if session_id and session_id in self._sessions:
            runtime = self._sessions[session_id]
        else:
            runtime = AgentRuntime(
                max_tokens=max_tokens,
                top_files=top_files,
                policy=policy,
                engine=self._engine,
            )
            session_id = runtime.session.session_id
            self._sessions[session_id] = runtime

        run_id = uuid.uuid4().hex
        self._emit(
            "gateway.run_agent_step.started",
            run_id=run_id,
            session_id=session_id,
            task=req.task,
            repo=req.repo,
            turn=runtime.session.turn_number,
        )

        runtime_result = runtime.run(
            req.task,
            req.repo,
            workspace=req.workspace,
            max_tokens=max_tokens,
            top_files=top_files,
            metadata=req.metadata,
        )

        ctx = runtime_result.prepared_context
        compressed_files = _extract_compressed_files(ctx.run_artifact)

        policy_status = PolicyStatus(
            passed=ctx.policy_passed if ctx.policy_passed is not None else True,
            violations=list(ctx.policy_violations),
        )

        self._emit(
            "gateway.run_agent_step.completed",
            run_id=run_id,
            session_id=session_id,
            turn=runtime_result.turn_number,
            estimated_tokens=ctx.estimated_tokens,
            tokens_saved=ctx.tokens_saved,
            policy_passed=policy_status.passed,
            cache_hits=ctx.cache_hits,
            session_tokens=runtime_result.session_tokens,
        )

        return RunAgentStepResponse(
            optimized_context=OptimizedContext(
                files=compressed_files,
                prompt_text=ctx.prompt_text,
                files_included=list(ctx.files_included),
            ),
            token_estimate=ctx.estimated_tokens,
            policy_status=policy_status,
            run_id=run_id,
            session_id=session_id,
            turn=runtime_result.turn_number,
            session_tokens=runtime_result.session_tokens,
            cache_hits=ctx.cache_hits,
            quality_risk=ctx.quality_risk,
            tokens_saved=ctx.tokens_saved,
            llm_response=runtime_result.llm_response,
        )

    def handle_report_run(self, req: ReportRunRequest) -> ReportRunResponse:
        """Handle ``POST /report-run``.

        Records the run outcome as a telemetry event and acknowledges receipt.
        """
        self._emit(
            "gateway.run_reported",
            session_id=req.session_id,
            run_id=req.run_id,
            status=req.status,
            tokens_used=req.tokens_used,
        )
        logger.info(
            "run reported  session=%s run=%s status=%s tokens=%s",
            req.session_id,
            req.run_id,
            req.status,
            req.tokens_used,
        )
        return ReportRunResponse(
            acknowledged=True,
            session_id=req.session_id,
            run_id=req.run_id,
        )
