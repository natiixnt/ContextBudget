# SPDX-License-Identifier: LicenseRef-Redcon-Commercial
# Copyright (c) 2026 nai. All rights reserved.
# See LICENSE-COMMERCIAL for terms.

from __future__ import annotations

"""AgentRuntime — the agent/LLM middleware layer.

Architecture
------------
    agent → AgentRuntime → LLM

AgentRuntime sits between the coding agent and the downstream LLM.  For every
agent turn it:

1. Intercepts the task description + repository path.
2. Runs the full Redcon optimisation pipeline
   (scan → rank → symbol extraction → context slicing → compression →
   cache reuse → delta prompts).
3. Applies token budget and context policy constraints.
4. Returns a :class:`PreparedContext` — the optimised prompt payload — and
   optionally dispatches it to a registered LLM callable.
5. Records the turn in a :class:`RuntimeSession` so cumulative token usage
   is visible across the full agent session.

Quick-start
-----------
::

    from redcon.runtime import AgentRuntime

    # Minimal — no LLM dispatch, just prepare context
    runtime = AgentRuntime(max_tokens=32_000)
    result = runtime.run("add Redis caching to the session store", repo=".")
    print(result.prepared_context.prompt_text[:500])
    print(f"Tokens used: {result.prepared_context.estimated_tokens}")

    # With LLM dispatch
    import anthropic
    client = anthropic.Anthropic()

    def call_llm(prompt: str) -> str:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    runtime = AgentRuntime(max_tokens=32_000, llm_fn=call_llm)
    result = runtime.run("add Redis caching to the session store", repo=".")
    print(result.llm_response)
"""

import logging
from pathlib import Path
from typing import Any, Callable, Mapping

from redcon.agents.middleware import (
    AgentMiddlewareResult,
    AgentTaskRequest,
    RedconMiddleware,
    _build_middleware_metadata,
)
from redcon.core.delta import effective_pack_metrics
from redcon.core.policy import PolicySpec, load_policy
from redcon.engine import BudgetPolicyViolationError, RedconEngine
from redcon.runtime.context import PreparedContext, RuntimeResult
from redcon.runtime.session import RuntimeSession


_log = logging.getLogger(__name__)


def _build_prompt_text(run_artifact: dict[str, Any]) -> str:
    """Assemble a plain-text prompt body from a pack run artifact.

    Walks the ``compressed_context`` list in include order, prefixing each
    entry with a ``# File: <path>`` header and appending its ``text`` block.
    Entries whose text is an unresolved ``@cached-summary:`` marker are
    skipped with a warning - the compressor should never emit these.
    """
    lines: list[str] = []
    for entry in run_artifact.get("compressed_context") or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        text = str(entry.get("text") or "")
        if text.startswith("@cached-summary:"):
            _log.warning(
                "Skipping unresolved cache marker for %s - prompt must be self-contained",
                path,
            )
            continue
        if path:
            lines.append(f"# File: {path}")
        if text:
            lines.append(text)
        lines.append("")
    return "\n".join(lines)


class AgentRuntime:
    """Runtime layer that manages context for AI agents.

    AgentRuntime is the primary entry-point for the
    ``agent → Redcon → LLM`` architecture.  It wraps
    :class:`~redcon.agents.middleware.RedconMiddleware` with:

    * **Session tracking** — cumulative token and turn history via
      :class:`~redcon.runtime.session.RuntimeSession`.
    * **Delta context** — after the first turn the runtime automatically
      passes the previous run artifact as ``delta_from`` so only changed
      files are re-sent.
    * **LLM dispatch** — an optional ``llm_fn`` callable receives the
      assembled prompt and its response is returned in
      :class:`~redcon.runtime.context.RuntimeResult`.
    * **Policy enforcement** — token budget and quality-risk policies are
      evaluated on every turn; ``strict=True`` raises
      :class:`~redcon.engine.BudgetPolicyViolationError` on
      violations.

    Parameters
    ----------
    max_tokens:
        Hard token budget for the packed context (default: engine/config
        default of 128 000).
    top_files:
        Maximum number of ranked files considered during packing.
    policy:
        A :class:`~redcon.core.policy.PolicySpec` to evaluate after
        every pack call.
    policy_path:
        Path to a TOML policy file (alternative to passing ``policy``
        directly).
    strict:
        If ``True``, raise :class:`~redcon.engine.BudgetPolicyViolationError`
        when policy checks fail.
    delta:
        If ``True`` (default), pass the previous turn's run artifact as
        ``delta_from`` on subsequent turns so only changed context is
        re-sent to the LLM.
    llm_fn:
        Optional callable ``(prompt: str) -> str`` that receives the
        assembled context prompt and returns an LLM response.  When
        ``None``, :meth:`run` returns ``llm_response=None``.
    config_path:
        Path to a ``redcon.toml`` configuration file.
    session:
        An existing :class:`~redcon.runtime.session.RuntimeSession`
        to resume.  A fresh session is created when ``None``.
    engine:
        An existing :class:`~redcon.engine.RedconEngine` to
        reuse.  A new one is created when ``None``.
    """

    def __init__(
        self,
        *,
        max_tokens: int | None = None,
        top_files: int | None = None,
        policy: PolicySpec | None = None,
        policy_path: str | Path | None = None,
        strict: bool = False,
        delta: bool = True,
        llm_fn: Callable[[str], str] | None = None,
        config_path: str | Path | None = None,
        session: RuntimeSession | None = None,
        engine: RedconEngine | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._top_files = top_files
        self._strict = strict
        self._delta = delta
        self._llm_fn = llm_fn
        self._config_path = Path(config_path).resolve() if config_path else None
        self.session = session if session is not None else RuntimeSession()

        self._engine = engine if engine is not None else RedconEngine(
            config_path=self._config_path,
        )
        self._middleware = RedconMiddleware(engine=self._engine)

        # Resolve policy once at construction time
        self._policy: PolicySpec | None = policy
        if self._policy is None and policy_path is not None:
            self._policy = load_policy(Path(policy_path).resolve())

    # ------------------------------------------------------------------
    # Primary interface
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
        config_path: str | Path | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> PreparedContext:
        """Intercept a task + repo, run the full pipeline, return optimised context.

        This is the core interception point in the
        ``agent → Redcon → LLM`` pipeline.  Call this when you want
        the packed context *without* dispatching to an LLM.

        Parameters
        ----------
        task:
            Natural-language description of the agent's current task.
        repo:
            Path to the repository to scan.
        workspace:
            Optional multi-repo workspace TOML path.
        max_tokens:
            Per-call token budget override (takes precedence over the
            constructor ``max_tokens``).
        top_files:
            Per-call top-files override.
        delta_from:
            Previous run artifact to use for incremental delta context.
            When ``None`` *and* ``self._delta`` is ``True``, the runtime
            automatically uses the last recorded run artifact.
        config_path:
            Per-call config TOML override.
        metadata:
            Arbitrary key/value pairs passed through to the middleware
            result metadata.

        Returns
        -------
        PreparedContext
            The assembled, compressed context ready for LLM consumption.
        """
        effective_max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        effective_top_files = top_files if top_files is not None else self._top_files
        effective_config = config_path or self._config_path

        # Auto-delta: use previous run artifact on second+ turns
        effective_delta_from = delta_from
        if effective_delta_from is None and self._delta and self.session.last_run_artifact is not None:
            effective_delta_from = self.session.last_run_artifact

        request = AgentTaskRequest(
            task=task,
            repo=repo,
            workspace=workspace,
            max_tokens=effective_max_tokens,
            top_files=effective_top_files,
            delta_from=effective_delta_from if not isinstance(effective_delta_from, dict) else None,
            config_path=effective_config,
            metadata=dict(metadata or {}),
        )

        # Pack — handle dict delta_from by injecting it before pack
        if isinstance(effective_delta_from, dict):
            run_artifact = self._engine.pack(
                task=request.task,
                repo=request.repo,
                workspace=request.workspace,
                max_tokens=request.max_tokens,
                top_files=request.top_files,
                delta_from=effective_delta_from,
                config_path=request.config_path,
            )
            mw_metadata = _build_middleware_metadata(request, run_artifact)
            middleware_result = AgentMiddlewareResult(
                request=request,
                run_artifact=run_artifact,
                metadata=mw_metadata,
            )
        else:
            middleware_result = self._middleware.prepare_request(request)

        # Policy evaluation
        if self._policy is not None or self._strict:
            middleware_result = self._middleware.enforce_budget(
                middleware_result,
                policy=self._policy,
                strict=self._strict,
                config_path=effective_config,
            )

        return self._to_prepared_context(middleware_result)

    def run(
        self,
        task: str,
        repo: str | Path = ".",
        *,
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeResult:
        """Run one agent turn: prepare context, optionally call LLM, record session.

        This is the main dispatch method for the runtime.  It:

        1. Runs :meth:`prepare_context` through the full pipeline.
        2. Passes the assembled prompt to ``llm_fn`` (if configured).
        3. Records the turn in :attr:`session`.
        4. Returns a :class:`RuntimeResult`.

        Parameters
        ----------
        task:
            Natural-language description of the current agent task.
        repo:
            Repository path to scan and pack.
        workspace:
            Optional multi-repo workspace TOML.
        max_tokens, top_files, metadata:
            Per-call overrides; see :meth:`prepare_context`.

        Returns
        -------
        RuntimeResult
            Contains the :class:`PreparedContext`, optional LLM response,
            turn number, and cumulative session token count.
        """
        ctx = self.prepare_context(
            task,
            repo,
            workspace=workspace,
            max_tokens=max_tokens,
            top_files=top_files,
            metadata=metadata,
        )

        llm_response: str | None = None
        if self._llm_fn is not None:
            llm_response = self._llm_fn(ctx.prompt_text)

        self.session.record_turn(
            task=task,
            repo=str(repo),
            estimated_tokens=ctx.estimated_tokens,
            tokens_saved=ctx.tokens_saved,
            files_included=list(ctx.files_included),
            quality_risk=ctx.quality_risk,
            policy_passed=ctx.policy_passed,
            delta_enabled=ctx.delta_enabled,
            cache_hits=ctx.cache_hits,
            llm_response=llm_response,
            run_artifact=ctx.run_artifact,
        )

        return RuntimeResult(
            prepared_context=ctx,
            llm_response=llm_response,
            turn_number=self.session.turn_number - 1,
            session_tokens=self.session.cumulative_tokens,
            session_id=self.session.session_id,
        )

    # ------------------------------------------------------------------
    # Session utilities
    # ------------------------------------------------------------------

    def session_summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of the current session."""
        return self.session.as_dict()

    def reset_session(self) -> None:
        """Clear session history and reset cumulative token counters."""
        self.session.reset()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_prepared_context(result: AgentMiddlewareResult) -> PreparedContext:
        """Convert an AgentMiddlewareResult into a PreparedContext."""
        run_artifact = result.run_artifact
        meta = result.metadata

        effective = effective_pack_metrics(run_artifact)
        files_included: list[str] = effective.get("files_included") or []
        if not isinstance(files_included, list):
            files_included = []

        budget = run_artifact.get("budget") or {}
        cache_report = meta.get("cache") or {}

        policy_passed: bool | None = None
        policy_violations: list[str] = []
        if result.policy_result is not None:
            policy_passed = bool(result.policy_result.get("passed", False))
            raw_violations = result.policy_result.get("violations") or []
            policy_violations = [str(v) for v in raw_violations if v]

        return PreparedContext(
            task=result.request.task,
            repo=str(run_artifact.get("repo") or result.request.repo),
            prompt_text=_build_prompt_text(run_artifact),
            files_included=files_included,
            estimated_tokens=int(meta.get("estimated_input_tokens") or 0),
            tokens_saved=int(meta.get("estimated_saved_tokens") or 0),
            quality_risk=str(budget.get("quality_risk_estimate") or "unknown"),
            policy_passed=policy_passed,
            policy_violations=policy_violations,
            delta_enabled=bool(meta.get("delta_enabled", False)),
            cache_hits=int(
                cache_report.get("hits") if isinstance(cache_report, dict) else 0
            ),
            metadata=dict(meta),
            run_artifact=dict(run_artifact),
        )
