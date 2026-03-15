from __future__ import annotations

"""Endpoint handlers for the Redcon Runtime Gateway."""

import json
import logging
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from redcon.agents.middleware import RedconMiddleware
from redcon.core.policy import PolicySpec
from redcon.engine import RedconEngine
from redcon.runtime import AgentRuntime
from redcon.telemetry import NoOpTelemetrySink, TelemetryEvent, TelemetrySink

from redcon.core.webhooks import dispatch_budget_overrun, dispatch_policy_violation
from redcon.gateway.config import GatewayConfig
from redcon.gateway.session_store import SessionStore
from redcon.gateway.models import (
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


def _fetch_remote_policy(config: GatewayConfig, repository_id: str | None = None) -> PolicySpec | None:
    """Fetch the active PolicySpec from the cloud control plane.

    Returns ``None`` if the cloud URL is not configured, the request fails,
    or no active policy exists for the given scope.  Failures are logged at
    WARNING level so the gateway keeps running with no policy rather than
    refusing requests.
    """
    if not config.cloud_policy_url or config.cloud_policy_org_id is None:
        return None

    params: list[str] = [f"org_id={config.cloud_policy_org_id}"]
    if repository_id:
        params.append(f"repository_id={urllib.request.quote(repository_id, safe='')}")
    url = f"{config.cloud_policy_url.rstrip('/')}/policies/active?{'&'.join(params)}"

    req = urllib.request.Request(url)
    if config.cloud_api_key:
        req.add_header("Authorization", f"Bearer {config.cloud_api_key}")

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        logger.warning("Remote policy fetch failed: HTTP %s from %s", exc.code, url)
        return None
    except Exception as exc:
        logger.warning("Remote policy fetch error: %s", exc)
        return None

    if not body or not isinstance(body, dict):
        return None

    spec = body.get("spec") or {}
    if not spec:
        return None

    try:
        return PolicySpec(
            max_estimated_input_tokens=spec.get("max_estimated_input_tokens"),
            max_files_included=spec.get("max_files_included"),
            max_quality_risk_level=spec.get("max_quality_risk_level"),
            min_estimated_savings_percentage=spec.get("min_estimated_savings_percentage"),
            max_context_size_bytes=spec.get("max_context_size_bytes"),
        )
    except Exception as exc:
        logger.warning("Could not build PolicySpec from remote spec: %s", exc)
        return None


def _fire_webhooks(
    config: GatewayConfig,
    *,
    run_id: str,
    endpoint: str,
    policy_status: "PolicyStatus",
    tokens_used: int,
    max_tokens: int,
) -> None:
    """Fire webhook notifications for policy violations and budget overruns."""
    if not config.webhook_url:
        return
    if not policy_status.passed:
        dispatch_policy_violation(
            config.webhook_url,
            secret=config.webhook_secret,
            run_id=run_id,
            endpoint=endpoint,
            violations=policy_status.violations,
            tokens_used=tokens_used,
        )
    if tokens_used > max_tokens:
        dispatch_budget_overrun(
            config.webhook_url,
            secret=config.webhook_secret,
            run_id=run_id,
            endpoint=endpoint,
            tokens_used=tokens_used,
            max_tokens=max_tokens,
        )


def _push_audit_entry(config: GatewayConfig, **fields: Any) -> None:
    """Fire-and-forget audit push to the cloud control plane.

    Silently does nothing if cloud audit is not configured.  All network
    failures are logged at WARNING level and swallowed so gateway requests
    are never blocked by audit delivery.
    """
    if not config.cloud_policy_url or config.cloud_policy_org_id is None or not config.cloud_api_key:
        return
    url = f"{config.cloud_policy_url.rstrip('/')}/orgs/{config.cloud_policy_org_id}/audit-log"
    data = json.dumps(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {config.cloud_api_key}")
    try:
        with urllib.request.urlopen(req, timeout=2) as _:  # noqa: S310
            pass
    except Exception as exc:
        logger.warning("Audit push failed: %s", exc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _restore_session(session: Any, stored: dict[str, Any]) -> None:
    """Restore persisted session fields into a freshly-created RuntimeSession."""
    if "session_id" in stored:
        session.session_id = stored["session_id"]
    if "created_at" in stored:
        session.created_at = stored["created_at"]
    if "updated_at" in stored:
        session.updated_at = stored["updated_at"]
    if "turns" in stored:
        session.turns = list(stored["turns"])
    if "cumulative_tokens" in stored:
        session.cumulative_tokens = int(stored["cumulative_tokens"])
    if "last_run_artifact" in stored:
        session.last_run_artifact = stored["last_run_artifact"]


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

    A single :class:`RedconEngine` is shared across all requests so
    that the on-disk summary cache is reused without additional coordination.
    Each ``/run-agent-step`` session maps to a dedicated
    :class:`~redcon.runtime.AgentRuntime` stored in an in-memory
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
        session_store: SessionStore | None = None,
    ) -> None:
        self._config = config
        self._sink: TelemetrySink = telemetry_sink or NoOpTelemetrySink()

        # Shared engine — all requests reuse the same on-disk cache
        self._engine = RedconEngine(
            config_path=config.config_path or None,
        )
        self._middleware = RedconMiddleware(engine=self._engine)

        # Session store: Redis-backed (distributed) or in-memory (single-node)
        self._session_store: SessionStore = session_store or SessionStore.from_env()

        # Local cache of live AgentRuntime objects (avoids re-creating per turn
        # when the same node handles consecutive turns for the same session)
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
        # Override with remote policy if the cloud control plane is configured
        remote = _fetch_remote_policy(self._config)
        if remote is not None:
            policy = remote

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

        _push_audit_entry(
            self._config,
            endpoint="/prepare-context",
            run_id=run_id,
            tokens_used=estimated_tokens,
            tokens_saved=tokens_saved,
            violation_count=len(policy_status.violations),
            policy_passed=policy_status.passed,
            status_code=200,
        )
        _fire_webhooks(
            self._config,
            run_id=run_id,
            endpoint="/prepare-context",
            policy_status=policy_status,
            tokens_used=estimated_tokens,
            max_tokens=max_tokens,
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
        remote = _fetch_remote_policy(self._config)
        if remote is not None:
            policy = remote

        # Retrieve or create the AgentRuntime for this session.
        # When the session store is Redis-backed, restore session state from
        # Redis so any replica can continue a session started on another node.
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
            # Restore persisted session state from store (Redis or in-memory)
            if session_id:
                stored = self._session_store.load(session_id)
                if stored is not None:
                    _restore_session(runtime.session, stored)
                    session_id = stored.get("session_id", runtime.session.session_id)
                else:
                    session_id = runtime.session.session_id
            else:
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

        _push_audit_entry(
            self._config,
            endpoint="/run-agent-step",
            run_id=run_id,
            tokens_used=ctx.estimated_tokens,
            tokens_saved=ctx.tokens_saved,
            violation_count=len(policy_status.violations),
            policy_passed=policy_status.passed,
            status_code=200,
        )
        _fire_webhooks(
            self._config,
            run_id=run_id,
            endpoint="/run-agent-step",
            policy_status=policy_status,
            tokens_used=ctx.estimated_tokens,
            max_tokens=max_tokens,
        )
        # Persist session state so other replicas can continue this session
        try:
            self._session_store.save(session_id, runtime.session.as_dict())
        except Exception as exc:
            logger.warning("Session persist failed for %s: %s", session_id, exc)
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
