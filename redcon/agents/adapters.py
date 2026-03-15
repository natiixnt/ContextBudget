from __future__ import annotations

"""Adapter abstractions for local agent-tool integrations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from redcon.core.delta import effective_pack_metrics
from redcon.core.policy import PolicySpec

from redcon.agents.middleware import AgentMiddlewareResult, AgentTaskRequest, RedconMiddleware


@dataclass(slots=True)
class AgentAdapterRun:
    """Result returned by an adapter simulation or integration."""

    adapter: str
    task: str
    middleware_result: AgentMiddlewareResult
    prompt_preview: str
    response: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly adapter result payload."""

        return {
            "adapter": self.adapter,
            "task": self.task,
            "prompt_preview": self.prompt_preview,
            "response": self.response,
            "metadata": dict(self.metadata),
            "context": self.middleware_result.as_record(),
        }


class AgentAdapter(ABC):
    """Abstract adapter for embedding Redcon into external agent tools."""

    name: str = "agent_adapter"

    @abstractmethod
    def run(
        self,
        request: AgentTaskRequest,
        middleware: RedconMiddleware,
        *,
        policy: PolicySpec | None = None,
        policy_path: str | Path | None = None,
        strict: bool = False,
        record_path: str | Path | None = None,
    ) -> AgentAdapterRun:
        """Run an adapter workflow for a request."""


class LocalDemoAgentAdapter(AgentAdapter):
    """Local-only adapter that simulates an agent workflow without vendor APIs."""

    name = "local_demo"

    def run(
        self,
        request: AgentTaskRequest,
        middleware: RedconMiddleware,
        *,
        policy: PolicySpec | None = None,
        policy_path: str | Path | None = None,
        strict: bool = False,
        record_path: str | Path | None = None,
    ) -> AgentAdapterRun:
        """Prepare context, optionally enforce policy, and simulate agent handoff."""

        result = middleware.handle(
            request,
            policy=policy,
            policy_path=policy_path,
            strict=strict,
        )

        recorded_artifact = ""
        if record_path is not None:
            recorded_artifact = str(middleware.record_run(result, path=record_path))

        effective = effective_pack_metrics(result.run_artifact)
        included = effective.get("files_included", [])
        if not isinstance(included, list):
            included = []
        removed = effective.get("files_removed", [])
        if not isinstance(removed, list):
            removed = []
        preview_files = ", ".join(str(item) for item in included[:3]) if included else "none"
        if len(included) > 3:
            preview_files = f"{preview_files}, +{len(included) - 3} more"
        if removed:
            preview_files = f"{preview_files}; removed={len(removed)}"

        prompt_preview = (
            f"Task: {request.task}\n"
            f"Packed files: {preview_files}\n"
            f"Estimated input tokens: {result.metadata.get('estimated_input_tokens', 0)}"
        )
        response = (
            f"Simulated agent received {len(included)} packed files "
            f"for task '{request.task}'."
        )

        adapter_metadata = {
            "recorded_artifact": recorded_artifact,
            "files_included_count": len(included),
            "selected_repos": list(result.metadata.get("selected_repos", [])),
            "policy_passed": (
                bool(result.policy_result.get("passed", False)) if result.policy_result is not None else None
            ),
        }
        result.adapter_name = self.name
        result.adapter_metadata = adapter_metadata
        return AgentAdapterRun(
            adapter=self.name,
            task=request.task,
            middleware_result=result,
            prompt_preview=prompt_preview,
            response=response,
            metadata=adapter_metadata,
        )
