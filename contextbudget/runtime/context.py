from __future__ import annotations

"""Runtime context dataclasses.

PreparedContext   - the optimised context package delivered to an LLM.
RuntimeResult     - the full result of one AgentRuntime.run() cycle.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PreparedContext:
    """Optimised context ready for LLM consumption.

    Produced by :meth:`AgentRuntime.prepare_context` after running the full
    ContextBudget pipeline (scan → rank → compress → cache → delta).

    Attributes
    ----------
    task:
        The original task description passed by the agent.
    repo:
        Repository path that was scanned.
    prompt_text:
        The assembled, compressed context string ready to be sent to the LLM
        as the user/system message body.
    files_included:
        Ordered list of file paths included in the context.
    estimated_tokens:
        Estimated token count of the packed context.
    tokens_saved:
        Tokens eliminated by compression, caching, and delta optimisation.
    quality_risk:
        Risk level of the compression applied (``low``, ``medium``, ``high``).
    policy_passed:
        ``True`` if all policy checks passed, ``False`` if any failed,
        ``None`` if no policy was evaluated.
    policy_violations:
        List of policy violation messages (empty when ``policy_passed`` is
        ``True`` or ``None``).
    delta_enabled:
        Whether incremental delta context was applied for this turn.
    cache_hits:
        Number of context fragments served from the warm cache.
    metadata:
        Additional middleware metrics (token counts, cache stats, …).
    run_artifact:
        Full pipeline run artifact dict for downstream auditing or replay.
    """

    task: str
    repo: str
    prompt_text: str
    files_included: list[str]
    estimated_tokens: int
    tokens_saved: int
    quality_risk: str
    policy_passed: bool | None = None
    policy_violations: list[str] = field(default_factory=list)
    delta_enabled: bool = False
    cache_hits: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    run_artifact: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation (without the full run artifact)."""
        return {
            "task": self.task,
            "repo": self.repo,
            "files_included": list(self.files_included),
            "estimated_tokens": self.estimated_tokens,
            "tokens_saved": self.tokens_saved,
            "quality_risk": self.quality_risk,
            "policy_passed": self.policy_passed,
            "policy_violations": list(self.policy_violations),
            "delta_enabled": self.delta_enabled,
            "cache_hits": self.cache_hits,
            "metadata": dict(self.metadata),
            "prompt_text_length": len(self.prompt_text),
        }


@dataclass(slots=True)
class RuntimeResult:
    """Full result of one :meth:`AgentRuntime.run` cycle.

    Attributes
    ----------
    prepared_context:
        The optimised context package.
    llm_response:
        The LLM response string, or ``None`` if no LLM callable was registered.
    turn_number:
        1-based index of this turn within the current runtime session.
    session_tokens:
        Cumulative estimated input tokens consumed across all turns so far.
    session_id:
        Identifier of the runtime session this result belongs to.
    """

    prepared_context: PreparedContext
    llm_response: str | None
    turn_number: int
    session_tokens: int
    session_id: str

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary."""
        return {
            "session_id": self.session_id,
            "turn_number": self.turn_number,
            "session_tokens": self.session_tokens,
            "llm_response": self.llm_response,
            "context": self.prepared_context.as_dict(),
        }
