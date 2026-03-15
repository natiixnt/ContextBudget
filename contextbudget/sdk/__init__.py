from __future__ import annotations

"""ContextBudget Agent SDK — canonical integration surface for agent frameworks.

Architecture
------------
    agent → ContextBudgetSDK → model

The three primary entry points mirror the agent lifecycle:

* :func:`prepare_context` — pack repository context under a token budget,
  returning a structured middleware result with compressed prompt material
  and additive metadata.
* :func:`simulate_agent` — estimate token use and API cost step by step
  before committing to a full pack run.
* :func:`profile_run` — pack context and return the run artifact augmented
  with wall-clock timing and compression metrics.

Usage
-----
Low-level module functions (quickest path)::

    from contextbudget.sdk import prepare_context, simulate_agent, profile_run

    result = prepare_context("add Redis caching", repo=".")
    plan   = simulate_agent("add Redis caching", repo=".", model="claude-sonnet-4-6")
    prof   = profile_run("add Redis caching", repo=".")

Class-based SDK (shared config, multi-turn)::

    from contextbudget.sdk import ContextBudgetSDK

    sdk = ContextBudgetSDK(max_tokens=32_000)
    result = sdk.prepare_context("add Redis caching", repo=".")
    runtime = sdk.runtime(llm_fn=my_llm)
    turn = runtime.run("add Redis caching", repo=".")
"""

from pathlib import Path
from typing import Any, Callable, Mapping

from contextbudget.agents.middleware import (
    AgentMiddlewareResult,
    AgentTaskRequest,
    ContextBudgetMiddleware,
    enforce_budget as _enforce_budget,
    prepare_context as _prepare_context,
    record_run as _record_run,
)
from contextbudget.core.policy import PolicySpec
from contextbudget.engine import BudgetGuard, BudgetPolicyViolationError, ContextBudgetEngine
from contextbudget.runtime import AgentRuntime, PreparedContext, RuntimeResult


class ContextBudgetSDK:
    """Unified SDK entry point for agent framework integration.

    Wraps :class:`~contextbudget.engine.BudgetGuard` and
    :class:`~contextbudget.agents.middleware.ContextBudgetMiddleware` behind a
    stable, agent-oriented interface.  All three primary integration methods are
    available as instance methods.

    Parameters
    ----------
    max_tokens:
        Default token budget applied to every :meth:`prepare_context` and
        :meth:`profile_run` call.
    top_files:
        Maximum number of ranked files to consider during packing.
    strict:
        When ``True``, raise
        :class:`~contextbudget.engine.BudgetPolicyViolationError` on policy
        violations.
    config_path:
        Path to a ``contextbudget.toml`` configuration file.
    engine:
        An existing engine instance to reuse (optional).

    Examples
    --------
    Basic integration::

        sdk = ContextBudgetSDK(max_tokens=32_000)
        result = sdk.prepare_context("add caching", repo=".")
        prompt = "\\n".join(f["text"] for f in result.run_artifact["compressed_context"])

    With LLM dispatch::

        runtime = sdk.runtime(llm_fn=my_llm_callable)
        turn = runtime.run("add caching", repo=".")
        print(turn.llm_response)
    """

    def __init__(
        self,
        *,
        max_tokens: int | None = None,
        top_files: int | None = None,
        strict: bool = False,
        config_path: str | Path | None = None,
        engine: ContextBudgetEngine | None = None,
    ) -> None:
        self._guard = BudgetGuard(
            max_tokens=max_tokens,
            top_files=top_files,
            strict=strict,
            config_path=config_path,
            engine=engine,
        )
        self._middleware = ContextBudgetMiddleware(engine=self._guard.engine)

    # ------------------------------------------------------------------
    # Primary SDK interface
    # ------------------------------------------------------------------

    def prepare_context(
        self,
        task: str,
        repo: str | Path = ".",
        *,
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        delta_from: str | Path | dict[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        config_path: str | Path | None = None,
    ) -> AgentMiddlewareResult:
        """Pack repository context for a task under the configured token budget.

        Runs the full ContextBudget pipeline — scan, rank, compress, cache —
        and returns an :class:`~contextbudget.agents.middleware.AgentMiddlewareResult`
        containing the compressed context and additive middleware metadata.

        Parameters
        ----------
        task:
            Natural-language description of the agent's current task.
        repo:
            Path to the repository to scan.
        workspace:
            Optional multi-repo workspace TOML path.
        max_tokens:
            Per-call override (takes precedence over the constructor value).
        top_files:
            Per-call ranked-file limit override.
        delta_from:
            Previous run artifact for incremental delta context.
        metadata:
            Arbitrary key/value pairs recorded in the middleware block.
        config_path:
            Per-call ``contextbudget.toml`` override.

        Returns
        -------
        AgentMiddlewareResult
            Contains ``run_artifact`` (the compressed context), ``metadata``
            (token estimates, file counts, quality risk, cache summary), and an
            optional ``policy_result``.

        Example
        -------
        ::

            result = sdk.prepare_context("refactor auth", repo=".")
            print(result.metadata["estimated_input_tokens"], "tokens")
            print(result.metadata["files_included_count"], "files")

            prompt = "\\n".join(
                f"# {f['path']}\\n{f['text']}"
                for f in result.run_artifact["compressed_context"]
            )
        """
        return self._middleware.prepare_context(
            task,
            repo,
            workspace=workspace,
            max_tokens=max_tokens if max_tokens is not None else self._guard.max_tokens,
            top_files=top_files if top_files is not None else self._guard.top_files,
            delta_from=delta_from if not isinstance(delta_from, dict) else None,
            config_path=config_path,
            metadata=metadata,
        )

    def simulate_agent(
        self,
        task: str,
        repo: str | Path = ".",
        *,
        workspace: str | Path | None = None,
        top_files: int | None = None,
        model: str = "claude-sonnet-4-6",
        price_per_1m_input: float | None = None,
        price_per_1m_output: float | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Simulate a multi-step agent workflow with token and cost estimates.

        Returns a step-by-step breakdown describing token usage and estimated
        API cost across lifecycle steps (inspect, implement, test, validate,
        document) *before* any pack run is executed.

        Parameters
        ----------
        task:
            Natural-language task description.
        repo:
            Repository path to analyse.
        workspace:
            Optional multi-repo workspace TOML path.
        top_files:
            Limit on ranked files to consider.
        model:
            Model identifier for cost lookup (e.g. ``claude-sonnet-4-6``,
            ``gpt-4o``).
        price_per_1m_input / price_per_1m_output:
            Optional custom token prices in USD (overrides built-in model
            prices).
        config_path:
            Per-call config TOML override.

        Returns
        -------
        dict
            Simulation artifact with ``steps``, ``cost_estimate``,
            ``total_tokens``, and summary fields.

        Example
        -------
        ::

            plan = sdk.simulate_agent("add caching", repo=".", model="claude-sonnet-4-6")
            print(f"Estimated cost: ${plan['cost_estimate']['total_cost_usd']:.4f}")
            for step in plan["steps"]:
                print(f"  {step['id']:12}  {step['step_total_tokens']:6} tokens")
        """
        return self._guard.simulate_agent(
            task=task,
            repo=repo,
            workspace=workspace,
            top_files=top_files if top_files is not None else self._guard.top_files,
            model=model,
            price_per_1m_input=price_per_1m_input,
            price_per_1m_output=price_per_1m_output,
            config_path=config_path,
        )

    def profile_run(
        self,
        task: str,
        repo: str | Path = ".",
        *,
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Pack context and return the run artifact augmented with profiling data.

        Measures wall-clock time for the pack operation and derives compression
        and budget metrics in a single ``profile`` block, making it easy for
        agent frameworks to log or display a one-stop run summary without
        navigating the full artifact structure.

        Parameters
        ----------
        task, repo, workspace, max_tokens, top_files, config_path:
            Same semantics as :meth:`prepare_context`.

        Returns
        -------
        dict
            Full run artifact with an additional ``profile`` block::

                {
                    "elapsed_ms": 142,
                    "estimated_input_tokens": 8200,
                    "estimated_saved_tokens": 3100,
                    "compression_ratio": 0.2741,
                    "files_included_count": 6,
                    "files_skipped_count": 2,
                    "quality_risk_estimate": "low"
                }

        Example
        -------
        ::

            prof = sdk.profile_run("add caching", repo=".")
            p = prof["profile"]
            print(f"packed in {p['elapsed_ms']} ms, ratio {p['compression_ratio']:.1%}")
            print(f"files: {p['files_included_count']} included, {p['files_skipped_count']} skipped")
        """
        return self._guard.profile_run(
            task=task,
            repo=repo,
            workspace=workspace,
            max_tokens=max_tokens if max_tokens is not None else self._guard.max_tokens,
            top_files=top_files if top_files is not None else self._guard.top_files,
            config_path=config_path,
        )

    # ------------------------------------------------------------------
    # Runtime factory
    # ------------------------------------------------------------------

    def runtime(
        self,
        *,
        llm_fn: Callable[[str], str] | None = None,
        delta: bool = True,
        strict: bool | None = None,
        policy: PolicySpec | None = None,
    ) -> AgentRuntime:
        """Create an :class:`~contextbudget.runtime.AgentRuntime` wired to this SDK's config.

        The runtime manages multi-turn agent loops with automatic delta context,
        session tracking, and optional LLM dispatch.

        Parameters
        ----------
        llm_fn:
            ``(prompt: str) -> str`` callable that receives the assembled
            context prompt and returns an LLM response.  When ``None``,
            :meth:`AgentRuntime.run` returns ``llm_response=None``.
        delta:
            Pass previous run artifact as ``delta_from`` on subsequent turns
            so only changed files are re-sent.
        strict:
            Override the guard's strict setting for this runtime.
        policy:
            Optional :class:`~contextbudget.core.policy.PolicySpec` to
            evaluate after every turn.

        Returns
        -------
        AgentRuntime

        Example
        -------
        ::

            def call_model(prompt: str) -> str:
                # ... call your LLM here ...
                return response_text

            runtime = sdk.runtime(llm_fn=call_model)
            turn = runtime.run("add caching", repo=".")
            print(turn.llm_response)
            print(f"turn {turn.turn_number}: {turn.prepared_context.estimated_tokens} tokens")
        """
        return AgentRuntime(
            max_tokens=self._guard.max_tokens,
            top_files=self._guard.top_files,
            strict=strict if strict is not None else self._guard.strict,
            delta=delta,
            llm_fn=llm_fn,
            policy=policy,
            engine=self._guard.engine,
        )

    # ------------------------------------------------------------------
    # Middleware helpers
    # ------------------------------------------------------------------

    def middleware(self) -> ContextBudgetMiddleware:
        """Return the underlying :class:`~contextbudget.agents.middleware.ContextBudgetMiddleware`.

        Use this when you need the lower-level ``prepare_request``,
        ``enforce_budget``, and ``record_run`` methods directly.
        """
        return self._middleware


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def prepare_context(
    task: str,
    repo: str | Path = ".",
    *,
    workspace: str | Path | None = None,
    max_tokens: int | None = None,
    top_files: int | None = None,
    delta_from: str | Path | None = None,
    metadata: Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> AgentMiddlewareResult:
    """Prepare packed context for a task (module-level convenience).

    Equivalent to ``ContextBudgetSDK().prepare_context(...)``.  Use this for
    one-off integrations; prefer :class:`ContextBudgetSDK` when you need to
    share token budgets or engine state across calls.

    Returns
    -------
    AgentMiddlewareResult

    Example
    -------
    ::

        from contextbudget.sdk import prepare_context

        result = prepare_context("refactor auth", repo=".", max_tokens=28_000)
        prompt = "\\n".join(f["text"] for f in result.run_artifact["compressed_context"])
        print(f"{result.metadata['estimated_input_tokens']} tokens, "
              f"{result.metadata['files_included_count']} files")
    """
    return _prepare_context(
        task,
        repo,
        workspace=workspace,
        max_tokens=max_tokens,
        top_files=top_files,
        delta_from=delta_from,
        metadata=metadata,
        config_path=config_path,
    )


def simulate_agent(
    task: str,
    repo: str | Path = ".",
    *,
    workspace: str | Path | None = None,
    model: str = "claude-sonnet-4-6",
    top_files: int | None = None,
    price_per_1m_input: float | None = None,
    price_per_1m_output: float | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Simulate agent token and cost estimates (module-level convenience).

    Equivalent to ``ContextBudgetSDK().simulate_agent(...)``.

    Returns
    -------
    dict
        Step-by-step simulation with ``steps``, ``cost_estimate``, and
        ``total_tokens``.

    Example
    -------
    ::

        from contextbudget.sdk import simulate_agent

        plan = simulate_agent("refactor auth", repo=".", model="claude-sonnet-4-6")
        print(f"${plan['cost_estimate']['total_cost_usd']:.4f} estimated")
        for step in plan["steps"]:
            print(f"  {step['id']:12}  {step['step_total_tokens']:6} tokens")
    """
    engine = ContextBudgetEngine(config_path=config_path)
    return engine.simulate_agent(
        task=task,
        repo=repo,
        workspace=workspace,
        top_files=top_files,
        model=model,
        price_per_1m_input=price_per_1m_input,
        price_per_1m_output=price_per_1m_output,
    )


def profile_run(
    task: str,
    repo: str | Path = ".",
    *,
    workspace: str | Path | None = None,
    max_tokens: int | None = None,
    top_files: int | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Pack and return profiling metrics (module-level convenience).

    Equivalent to ``ContextBudgetSDK().profile_run(...)``.

    Returns
    -------
    dict
        Full run artifact with an additional ``profile`` block.

    Example
    -------
    ::

        from contextbudget.sdk import profile_run

        prof = profile_run("refactor auth", repo=".")
        p = prof["profile"]
        print(f"packed in {p['elapsed_ms']} ms, {p['compression_ratio']:.1%} compression")
    """
    guard = BudgetGuard(max_tokens=max_tokens, config_path=config_path)
    return guard.profile_run(
        task=task,
        repo=repo,
        workspace=workspace,
        max_tokens=max_tokens,
        top_files=top_files,
        config_path=config_path,
    )


__all__ = [
    "ContextBudgetSDK",
    "prepare_context",
    "simulate_agent",
    "profile_run",
]
