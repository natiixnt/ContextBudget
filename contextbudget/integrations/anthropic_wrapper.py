from __future__ import annotations

"""Anthropic agent wrapper for ContextBudget.

Intercepts task requests, runs the full ContextBudget optimisation pipeline,
calls the Anthropic Messages API with the packed context, and emits run
telemetry to the local observe-history store.

Requires the ``anthropic`` package::

    pip install anthropic

Quick-start
-----------
::

    from contextbudget.integrations import AnthropicAgentWrapper

    agent = AnthropicAgentWrapper(model="claude-sonnet-4-6", repo=".")
    result = agent.run_task("add caching to API")
    print(result.llm_response)
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from contextbudget.core.policy import PolicySpec
from contextbudget.engine import ContextBudgetEngine
from contextbudget.runtime import AgentRuntime, RuntimeResult, RuntimeSession
from contextbudget.telemetry.store import append_observe_entry

if TYPE_CHECKING:
    pass


class AnthropicAgentWrapper:
    """ContextBudget-optimised wrapper around the Anthropic Messages API.

    For every :meth:`run_task` call the wrapper:

    1. Intercepts the task description and repository path.
    2. Runs the full ContextBudget pipeline (scan → rank → compress → cache →
       delta) to produce an optimised context prompt.
    3. Sends the prompt to the configured Anthropic model.
    4. Emits a run telemetry entry to ``.contextbudget/observe-history.json``.

    Parameters
    ----------
    model:
        Anthropic model identifier, e.g. ``"claude-sonnet-4-6"`` or
        ``"claude-opus-4-6"``.
    repo:
        Default repository path scanned for context.  Can be overridden per
        call in :meth:`run_task`.
    max_tokens:
        Token budget for the packed context (input side).
    top_files:
        Maximum number of ranked files the packer considers.
    max_completion_tokens:
        ``max_tokens`` forwarded to the Anthropic ``messages.create`` call.
    system_prompt:
        Optional system prompt string.
    policy:
        :class:`~contextbudget.core.policy.PolicySpec` evaluated after each
        pack call.
    strict:
        If ``True``, raise
        :class:`~contextbudget.engine.BudgetPolicyViolationError` on policy
        violations.
    delta:
        If ``True`` (default), pass the previous run artifact as delta
        context on subsequent turns.
    config_path:
        Path to a ``contextbudget.toml`` configuration file.
    session:
        An existing :class:`~contextbudget.runtime.RuntimeSession` to resume.
    engine:
        An existing :class:`~contextbudget.engine.ContextBudgetEngine` to
        reuse.
    anthropic_client:
        A pre-constructed ``anthropic.Anthropic`` client.  When ``None`` a
        new instance is created on first use.
    telemetry_base_dir:
        Base directory for the observe-history store.  Defaults to *repo*.
    """

    name: str = "anthropic"

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        repo: str | Path = ".",
        max_tokens: int | None = None,
        top_files: int | None = None,
        max_completion_tokens: int = 2048,
        system_prompt: str | None = None,
        policy: PolicySpec | None = None,
        strict: bool = False,
        delta: bool = True,
        config_path: str | Path | None = None,
        session: RuntimeSession | None = None,
        engine: ContextBudgetEngine | None = None,
        anthropic_client: Any | None = None,
        telemetry_base_dir: str | Path | None = None,
    ) -> None:
        self.model = model
        self.default_repo = Path(repo)
        self.max_completion_tokens = max_completion_tokens
        self.system_prompt = system_prompt
        self._telemetry_base_dir = telemetry_base_dir

        self._client = anthropic_client  # lazy-init if None
        self._last_prompt_tokens = 0
        self._last_completion_tokens = 0
        self._runtime = AgentRuntime(
            max_tokens=max_tokens,
            top_files=top_files,
            policy=policy,
            strict=strict,
            delta=delta,
            llm_fn=self._call_anthropic,
            config_path=config_path,
            session=session,
            engine=engine,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_task(
        self,
        task: str,
        repo: str | Path | None = None,
        *,
        max_tokens: int | None = None,
        top_files: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeResult:
        """Run one agent turn: optimise context, call Anthropic, emit telemetry.

        Parameters
        ----------
        task:
            Natural-language description of the coding task.
        repo:
            Repository path override.  Falls back to the constructor *repo*.
        max_tokens:
            Per-call token budget override.
        top_files:
            Per-call top-files override.
        metadata:
            Arbitrary key/value pairs passed through to middleware metadata.

        Returns
        -------
        RuntimeResult
            Contains the :class:`~contextbudget.runtime.PreparedContext`,
            the raw LLM response string, and session tracking fields.
        """
        effective_repo = Path(repo) if repo is not None else self.default_repo
        result = self._runtime.run(
            task,
            effective_repo,
            max_tokens=max_tokens,
            top_files=top_files,
            metadata=metadata,
        )
        self._emit_telemetry(result, task=task)
        return result

    @property
    def session(self) -> RuntimeSession:
        """The underlying :class:`~contextbudget.runtime.RuntimeSession`."""
        return self._runtime.session

    def session_summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of the current session."""
        return self._runtime.session_summary()

    def reset_session(self) -> None:
        """Clear session history and reset cumulative token counters."""
        self._runtime.reset_session()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic  # type: ignore[import]
            except ImportError as exc:
                raise ImportError(
                    "The 'anthropic' package is required for AnthropicAgentWrapper. "
                    "Install it with: pip install anthropic"
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    def _call_anthropic(self, prompt: str) -> str:
        """Send *prompt* to the Anthropic Messages API and return the response."""
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_completion_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.system_prompt:
            kwargs["system"] = self.system_prompt

        response = client.messages.create(**kwargs)
        # Capture actual token usage for telemetry
        usage = getattr(response, "usage", None)
        self._last_prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        self._last_completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        block = response.content[0] if response.content else None
        if block is None:
            return ""
        return str(getattr(block, "text", "") or "")

    def _emit_telemetry(self, result: RuntimeResult, *, task: str) -> None:
        ctx = result.prepared_context
        base_dir = self._telemetry_base_dir if self._telemetry_base_dir is not None else ctx.repo
        entry: dict[str, Any] = {
            "adapter": self.name,
            "model": self.model,
            "task": task,
            "repo": ctx.repo,
            "session_id": result.session_id,
            "turn_number": result.turn_number,
            "session_tokens": result.session_tokens,
            "estimated_tokens": ctx.estimated_tokens,
            "tokens_saved": ctx.tokens_saved,
            "files_included": list(ctx.files_included),
            "quality_risk": ctx.quality_risk,
            "policy_passed": ctx.policy_passed,
            "delta_enabled": ctx.delta_enabled,
            "cache_hits": ctx.cache_hits,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        if self._last_prompt_tokens or self._last_completion_tokens:
            entry["llm_prompt_tokens"] = self._last_prompt_tokens
            entry["llm_completion_tokens"] = self._last_completion_tokens
            entry["llm_total_tokens"] = self._last_prompt_tokens + self._last_completion_tokens
        append_observe_entry(entry, base_dir=base_dir)
