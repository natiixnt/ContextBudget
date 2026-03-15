from __future__ import annotations

"""Runtime session — tracks multi-turn agent state across AgentRuntime calls."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RuntimeSession:
    """Stateful session tracking cumulative context across multiple agent turns.

    A session is created automatically when an :class:`AgentRuntime` is
    instantiated and persists for the lifetime of that runtime instance.
    Callers that need explicit session lifecycle control can construct a
    ``RuntimeSession`` themselves and pass it into ``AgentRuntime``.

    Attributes
    ----------
    session_id:
        A unique identifier for this session (UUID4 by default).
    created_at:
        ISO-8601 timestamp of session creation.
    updated_at:
        ISO-8601 timestamp of the last turn.
    turns:
        Ordered list of per-turn summary dicts (one entry per
        :meth:`AgentRuntime.run` call).
    cumulative_tokens:
        Running total of estimated input tokens across all turns.
    last_run_artifact:
        The raw ``run_artifact`` dict from the most recent turn, kept so the
        next turn can request a *delta* context (only changed files).
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    turns: list[dict[str, Any]] = field(default_factory=list)
    cumulative_tokens: int = 0
    last_run_artifact: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    @property
    def turn_number(self) -> int:
        """1-based index of the *next* turn (i.e. ``len(turns) + 1``)."""
        return len(self.turns) + 1

    def record_turn(
        self,
        *,
        task: str,
        repo: str,
        estimated_tokens: int,
        tokens_saved: int,
        files_included: list[str],
        quality_risk: str,
        policy_passed: bool | None,
        delta_enabled: bool,
        cache_hits: int,
        llm_response: str | None,
        run_artifact: dict[str, Any],
    ) -> None:
        """Append one completed turn to the session history."""

        self.cumulative_tokens += estimated_tokens
        self.last_run_artifact = run_artifact
        self.updated_at = datetime.now(tz=timezone.utc).isoformat()

        self.turns.append(
            {
                "turn": len(self.turns) + 1,
                "task": task,
                "repo": repo,
                "estimated_tokens": estimated_tokens,
                "tokens_saved": tokens_saved,
                "files_included_count": len(files_included),
                "quality_risk": quality_risk,
                "policy_passed": policy_passed,
                "delta_enabled": delta_enabled,
                "cache_hits": cache_hits,
                "has_llm_response": llm_response is not None,
                "timestamp": self.updated_at,
            }
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable session summary."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": len(self.turns),
            "cumulative_tokens": self.cumulative_tokens,
            "turns": list(self.turns),
        }

    def reset(self) -> None:
        """Clear all turn history and reset cumulative counters."""
        self.turns.clear()
        self.cumulative_tokens = 0
        self.last_run_artifact = None
        self.updated_at = datetime.now(tz=timezone.utc).isoformat()
