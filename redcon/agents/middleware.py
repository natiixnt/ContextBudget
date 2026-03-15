from __future__ import annotations

"""Agent-facing middleware helpers built on top of the core engine."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from redcon.cache import normalize_cache_report
from redcon.core.delta import effective_pack_metrics
from redcon.core.policy import PolicySpec, default_strict_policy
from redcon.core.render import write_json
from redcon.engine import BudgetPolicyViolationError, RedconEngine


def _stringify_path(value: str | Path | None) -> str:
    if value is None:
        return ""
    return str(value)


@dataclass(slots=True)
class AgentTaskRequest:
    """Machine-readable task request passed through middleware."""

    task: str
    repo: str | Path = "."
    workspace: str | Path | None = None
    max_tokens: int | None = None
    top_files: int | None = None
    delta_from: str | Path | None = None
    config_path: str | Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly request payload."""

        data = {
            "task": self.task,
            "repo": _stringify_path(self.repo),
            "max_tokens": self.max_tokens,
            "top_files": self.top_files,
            "metadata": dict(self.metadata),
        }
        if self.workspace is not None:
            data["workspace"] = _stringify_path(self.workspace)
        if self.delta_from is not None:
            data["delta_from"] = _stringify_path(self.delta_from)
        if self.config_path is not None:
            data["config_path"] = _stringify_path(self.config_path)
        return data


@dataclass(slots=True)
class AgentMiddlewareResult:
    """Prepared context plus middleware metadata for agent integrations."""

    request: AgentTaskRequest
    run_artifact: dict[str, Any]
    metadata: dict[str, Any]
    policy_result: dict[str, Any] | None = None
    recorded_path: str = ""
    adapter_name: str = ""
    adapter_metadata: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        """Return a persisted artifact payload with additive middleware metadata."""

        data = dict(self.run_artifact)
        if self.policy_result is not None:
            data["policy"] = self.policy_result

        middleware_block = {
            "request": self.request.as_dict(),
            "metadata": dict(self.metadata),
        }
        if self.recorded_path:
            middleware_block["recorded_path"] = self.recorded_path
        if self.adapter_name:
            middleware_block["adapter"] = self.adapter_name
        if self.adapter_metadata:
            middleware_block["adapter_metadata"] = dict(self.adapter_metadata)

        data["agent_middleware"] = middleware_block
        return data


def _build_middleware_metadata(
    request: AgentTaskRequest,
    run_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    budget = run_artifact.get("budget", {})
    if not isinstance(budget, Mapping):
        budget = {}
    effective = effective_pack_metrics(run_artifact)
    files_included = effective.get("files_included", [])
    if not isinstance(files_included, list):
        files_included = []
    files_removed = effective.get("files_removed", [])
    if not isinstance(files_removed, list):
        files_removed = []
    files_skipped = run_artifact.get("files_skipped", [])
    if not isinstance(files_skipped, list):
        files_skipped = []
    ranked_files = run_artifact.get("ranked_files", [])
    if not isinstance(ranked_files, list):
        ranked_files = []

    return {
        "task": request.task,
        "repo": str(run_artifact.get("repo", "")),
        "workspace": str(run_artifact.get("workspace", "")),
        "max_tokens": int(run_artifact.get("max_tokens", 0) or 0),
        "files_included_count": len(files_included),
        "files_removed_count": len(files_removed),
        "files_skipped_count": len(files_skipped),
        "ranked_files_count": len(ranked_files),
        "selected_repos": list(run_artifact.get("selected_repos", []))
        if isinstance(run_artifact.get("selected_repos", []), list)
        else [],
        "scanned_repos": list(run_artifact.get("scanned_repos", []))
        if isinstance(run_artifact.get("scanned_repos", []), list)
        else [],
        "delta_enabled": bool(effective.get("delta_enabled", False)),
        "estimated_input_tokens": int(effective.get("estimated_input_tokens", budget.get("estimated_input_tokens", 0)) or 0),
        "estimated_saved_tokens": int(effective.get("estimated_saved_tokens", budget.get("estimated_saved_tokens", 0)) or 0),
        "original_input_tokens": int(effective.get("original_input_tokens", budget.get("estimated_input_tokens", 0)) or 0),
        "duplicate_reads_prevented": int(budget.get("duplicate_reads_prevented", 0) or 0),
        "quality_risk_estimate": str(budget.get("quality_risk_estimate", "unknown")),
        "cache": normalize_cache_report(run_artifact),
        "request_metadata": dict(request.metadata),
    }


class RedconMiddleware:
    """High-level middleware wrapper for agent-oriented context preparation."""

    def __init__(
        self,
        *,
        engine: RedconEngine | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        self.engine = engine if engine is not None else RedconEngine(config_path=config_path)

    def prepare_context(
        self,
        task: str,
        repo: str | Path = ".",
        *,
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        delta_from: str | Path | None = None,
        config_path: str | Path | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AgentMiddlewareResult:
        """Prepare packed context for an agent task using the existing engine."""

        request = AgentTaskRequest(
            task=task,
            repo=repo,
            workspace=workspace,
            max_tokens=max_tokens,
            top_files=top_files,
            delta_from=delta_from,
            config_path=config_path,
            metadata=dict(metadata or {}),
        )
        return self.prepare_request(request)

    def prepare_request(self, request: AgentTaskRequest) -> AgentMiddlewareResult:
        """Prepare packed context from a typed request."""

        run_artifact = self.engine.pack(
            task=request.task,
            repo=request.repo,
            workspace=request.workspace,
            max_tokens=request.max_tokens,
            top_files=request.top_files,
            delta_from=request.delta_from,
            config_path=request.config_path,
        )
        metadata = _build_middleware_metadata(request, run_artifact)
        return AgentMiddlewareResult(
            request=request,
            run_artifact=run_artifact,
            metadata=metadata,
        )

    def enforce_budget(
        self,
        result: AgentMiddlewareResult,
        policy: PolicySpec | None = None,
        *,
        policy_path: str | Path | None = None,
        strict: bool = False,
        config_path: str | Path | None = None,
    ) -> AgentMiddlewareResult:
        """Evaluate policy for a prepared result and optionally raise on violations."""

        effective_policy = policy
        if effective_policy is None and policy_path is None and strict:
            fallback_max_tokens = int(result.run_artifact.get("max_tokens", 0) or 0)
            effective_policy = default_strict_policy(max_estimated_input_tokens=fallback_max_tokens)

        policy_result = self.engine.evaluate_policy(
            result.run_artifact,
            policy=effective_policy,
            policy_path=policy_path,
            config_path=config_path or result.request.config_path,
        )
        result.policy_result = policy_result
        result.run_artifact["policy"] = policy_result
        if strict and not bool(policy_result.get("passed", False)):
            raise BudgetPolicyViolationError(policy_result=policy_result, run_artifact=result.as_record())
        return result

    def record_run(
        self,
        result: AgentMiddlewareResult,
        path: str | Path = "run.json",
    ) -> Path:
        """Persist a middleware result as a machine-readable JSON artifact."""

        output_path = Path(path).resolve()
        result.recorded_path = str(output_path)
        write_json(output_path, result.as_record())
        self.engine.record_history_artifacts(
            result.run_artifact,
            artifacts={
                "run_json": str(output_path),
            },
            config_path=result.request.config_path,
        )
        return output_path

    def handle(
        self,
        request: AgentTaskRequest,
        *,
        policy: PolicySpec | None = None,
        policy_path: str | Path | None = None,
        strict: bool = False,
    ) -> AgentMiddlewareResult:
        """Run the prepare-and-enforce flow for a single request."""

        result = self.prepare_request(request)
        if policy is None and policy_path is None and not strict:
            return result
        return self.enforce_budget(
            result,
            policy=policy,
            policy_path=policy_path,
            strict=strict,
            config_path=request.config_path,
        )


def prepare_context(
    task: str,
    repo: str | Path = ".",
    *,
    workspace: str | Path | None = None,
    max_tokens: int | None = None,
    top_files: int | None = None,
    delta_from: str | Path | None = None,
    config_path: str | Path | None = None,
    metadata: Mapping[str, Any] | None = None,
    middleware: RedconMiddleware | None = None,
) -> AgentMiddlewareResult:
    """Convenience wrapper for preparing packed context."""

    active_middleware = middleware if middleware is not None else RedconMiddleware(config_path=config_path)
    return active_middleware.prepare_context(
        task,
        repo,
        workspace=workspace,
        max_tokens=max_tokens,
        top_files=top_files,
        delta_from=delta_from,
        config_path=config_path,
        metadata=metadata,
    )


def enforce_budget(
    result: AgentMiddlewareResult,
    policy: PolicySpec | None = None,
    *,
    policy_path: str | Path | None = None,
    strict: bool = False,
    middleware: RedconMiddleware | None = None,
    config_path: str | Path | None = None,
) -> AgentMiddlewareResult:
    """Convenience wrapper for policy enforcement on middleware results."""

    active_middleware = middleware if middleware is not None else RedconMiddleware(config_path=config_path)
    return active_middleware.enforce_budget(
        result,
        policy=policy,
        policy_path=policy_path,
        strict=strict,
        config_path=config_path,
    )


def record_run(
    result: AgentMiddlewareResult,
    path: str | Path = "run.json",
    *,
    middleware: RedconMiddleware | None = None,
) -> Path:
    """Convenience wrapper for persisting middleware results."""

    active_middleware = middleware if middleware is not None else RedconMiddleware()
    return active_middleware.record_run(result, path=path)
