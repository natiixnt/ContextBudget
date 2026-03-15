from __future__ import annotations

"""Generic agent runner wrapper for ContextBudget.

Provides a vendor-neutral runner that accepts any callable ``llm_fn`` as the
backend.  Useful for integrating with local models, custom API wrappers, or
any LLM provider not covered by the first-party wrappers.

Quick-start
-----------
::

    from contextbudget.integrations import GenericAgentRunner

    def my_llm(prompt: str) -> str:
        # call any model you like
        return call_my_model(prompt)

    runner = GenericAgentRunner(llm_fn=my_llm, repo=".")
    result = runner.run_task("add caching to API")
    print(result.llm_response)
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from contextbudget.core.policy import PolicySpec
from contextbudget.engine import ContextBudgetEngine
from contextbudget.runtime import AgentRuntime, RuntimeResult, RuntimeSession
from contextbudget.telemetry.store import append_observe_entry


class GenericAgentRunner:
    """ContextBudget-optimised runner that delegates to any ``llm_fn`` callable.

    For every :meth:`run_task` call the runner:

    1. Intercepts the task description and repository path.
    2. Runs the full ContextBudget pipeline (scan → rank → compress → cache →
       delta) to produce an optimised context prompt.
    3. Passes the assembled prompt to *llm_fn* and captures its return value.
    4. Emits a run telemetry entry to ``.contextbudget/observe-history.json``.

    Parameters
    ----------
    llm_fn:
        Callable with signature ``(prompt: str) -> str`` that sends the
        context prompt to any LLM backend and returns its response.
    repo:
        Default repository path scanned for context.  Can be overridden per
        call in :meth:`run_task`.
    adapter_name:
        Label used in telemetry entries to identify this runner.
    max_tokens:
        Token budget for the packed context (input side).
    top_files:
        Maximum number of ranked files the packer considers.
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
    telemetry_base_dir:
        Base directory for the observe-history store.  Defaults to *repo*.
    """

    def __init__(
        self,
        *,
        llm_fn: Callable[[str], str],
        repo: str | Path = ".",
        adapter_name: str = "generic",
        max_tokens: int | None = None,
        top_files: int | None = None,
        policy: PolicySpec | None = None,
        strict: bool = False,
        delta: bool = True,
        config_path: str | Path | None = None,
        session: RuntimeSession | None = None,
        engine: ContextBudgetEngine | None = None,
        telemetry_base_dir: str | Path | None = None,
    ) -> None:
        self.name = adapter_name
        self.default_repo = Path(repo)
        self._telemetry_base_dir = telemetry_base_dir

        self._runtime = AgentRuntime(
            max_tokens=max_tokens,
            top_files=top_files,
            policy=policy,
            strict=strict,
            delta=delta,
            llm_fn=llm_fn,
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
        """Run one agent turn: optimise context, call llm_fn, emit telemetry.

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
            the LLM response string from *llm_fn*, and session tracking fields.
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

    def _emit_telemetry(self, result: RuntimeResult, *, task: str) -> None:
        ctx = result.prepared_context
        base_dir = self._telemetry_base_dir if self._telemetry_base_dir is not None else ctx.repo
        entry: dict[str, Any] = {
            "adapter": self.name,
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
        append_observe_entry(entry, base_dir=base_dir)
