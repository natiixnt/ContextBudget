from __future__ import annotations

"""Request and response dataclasses for the Redcon Gateway API."""

from dataclasses import dataclass, field
from typing import Any


# ── Requests ──────────────────────────────────────────────────────────────────


@dataclass
class PrepareContextRequest:
    """Body for ``POST /prepare-context``.

    A stateless request that runs the full optimization pipeline once and
    returns the compressed context without creating a persistent session.
    """

    task: str
    repo: str = "."
    workspace: str | None = None
    max_tokens: int | None = None
    max_files: int | None = None
    max_context_size: int | None = None
    top_files: int | None = None
    delta_from: str | None = None
    config_path: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PrepareContextRequest:
        return cls(
            task=str(d["task"]),
            repo=str(d.get("repo", ".")),
            workspace=d.get("workspace") or None,
            max_tokens=int(d["max_tokens"]) if d.get("max_tokens") is not None else None,
            max_files=int(d["max_files"]) if d.get("max_files") is not None else None,
            max_context_size=(
                int(d["max_context_size"])
                if d.get("max_context_size") is not None
                else None
            ),
            top_files=int(d["top_files"]) if d.get("top_files") is not None else None,
            delta_from=d.get("delta_from") or None,
            config_path=d.get("config_path") or None,
            session_id=d.get("session_id") or None,
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class RunAgentStepRequest:
    """Body for ``POST /run-agent-step``.

    A stateful request tied to a gateway session.  Subsequent calls with the
    same ``session_id`` automatically apply delta context (only changed files
    are re-sent) and accumulate cumulative token counts.
    """

    task: str
    repo: str = "."
    workspace: str | None = None
    session_id: str | None = None
    max_tokens: int | None = None
    max_files: int | None = None
    max_context_size: int | None = None
    top_files: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunAgentStepRequest:
        return cls(
            task=str(d["task"]),
            repo=str(d.get("repo", ".")),
            workspace=d.get("workspace") or None,
            session_id=d.get("session_id") or None,
            max_tokens=int(d["max_tokens"]) if d.get("max_tokens") is not None else None,
            max_files=int(d["max_files"]) if d.get("max_files") is not None else None,
            max_context_size=(
                int(d["max_context_size"])
                if d.get("max_context_size") is not None
                else None
            ),
            top_files=int(d["top_files"]) if d.get("top_files") is not None else None,
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class ReportRunRequest:
    """Body for ``POST /report-run``.

    Sent by the agent after an LLM call completes to acknowledge completion
    and record telemetry.
    """

    session_id: str
    run_id: str
    status: str  # "success" | "error" | "cancelled"
    tokens_used: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReportRunRequest:
        return cls(
            session_id=str(d["session_id"]),
            run_id=str(d["run_id"]),
            status=str(d.get("status", "success")),
            tokens_used=(
                int(d["tokens_used"]) if d.get("tokens_used") is not None else None
            ),
            error=d.get("error") or None,
            metadata=dict(d.get("metadata") or {}),
        )


# ── Shared response components ─────────────────────────────────────────────────


@dataclass
class PolicyStatus:
    """Policy evaluation result embedded in gateway responses."""

    passed: bool
    violations: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "violations": list(self.violations)}


@dataclass
class OptimizedContext:
    """Compressed context payload returned by the gateway.

    Attributes
    ----------
    files:
        Per-file compression entries from the pipeline artifact
        (``path``, ``strategy``, ``original_tokens``, ``compressed_tokens``,
        ``text``).
    prompt_text:
        Assembled prompt string ready to be forwarded to the LLM.
    files_included:
        Ordered list of file paths present in the context.
    """

    files: list[dict[str, Any]]
    prompt_text: str
    files_included: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "prompt_text": self.prompt_text,
            "files_included": list(self.files_included),
        }


# ── Responses ──────────────────────────────────────────────────────────────────


@dataclass
class PrepareContextResponse:
    """Response from ``POST /prepare-context``."""

    optimized_context: OptimizedContext
    token_estimate: int
    policy_status: PolicyStatus
    run_id: str
    session_id: str
    cache_hits: int
    quality_risk: str
    tokens_saved: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "optimized_context": self.optimized_context.as_dict(),
            "token_estimate": self.token_estimate,
            "policy_status": self.policy_status.as_dict(),
            "run_id": self.run_id,
            "session_id": self.session_id,
            "cache_hits": self.cache_hits,
            "quality_risk": self.quality_risk,
            "tokens_saved": self.tokens_saved,
        }


@dataclass
class RunAgentStepResponse:
    """Response from ``POST /run-agent-step``."""

    optimized_context: OptimizedContext
    token_estimate: int
    policy_status: PolicyStatus
    run_id: str
    session_id: str
    turn: int
    session_tokens: int
    cache_hits: int
    quality_risk: str
    tokens_saved: int
    llm_response: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "optimized_context": self.optimized_context.as_dict(),
            "token_estimate": self.token_estimate,
            "policy_status": self.policy_status.as_dict(),
            "run_id": self.run_id,
            "session_id": self.session_id,
            "turn": self.turn,
            "session_tokens": self.session_tokens,
            "cache_hits": self.cache_hits,
            "quality_risk": self.quality_risk,
            "tokens_saved": self.tokens_saved,
            "llm_response": self.llm_response,
        }


@dataclass
class ReportRunResponse:
    """Response from ``POST /report-run``."""

    acknowledged: bool
    session_id: str
    run_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "acknowledged": self.acknowledged,
            "session_id": self.session_id,
            "run_id": self.run_id,
        }
