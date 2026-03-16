# SPDX-License-Identifier: LicenseRef-Redcon-Commercial
# Copyright (c) 2026 nai. All rights reserved.
# See LICENSE-COMMERCIAL for terms.

from __future__ import annotations

"""Node.js agent loop runner for Redcon.

Intercepts task requests, runs the full Redcon optimisation pipeline,
passes the packed context prompt to a Node.js script via stdin, and emits run
telemetry to the local observe-history store.

The Node.js script receives the optimised prompt on stdin and must write its
response to stdout.  This lets any Node.js agent loop (LangChain.js, Vercel
AI SDK, OpenAI Node SDK, custom scripts, …) benefit from Redcon
without any Python knowledge.

Quick-start
-----------
::

    from redcon.integrations import NodeJSAgentRunner

    runner = NodeJSAgentRunner(script="./agent.js", repo=".")
    result = runner.run_task("add caching")
    print(result.llm_response)

Node.js script contract
-----------------------
The runner passes the optimised prompt to the Node.js process via **stdin**
(UTF-8, terminated by EOF when the process input pipe closes).  The script
must write its response to **stdout**.  stderr is forwarded to the Python
process for debugging.

Minimal example ``agent.js``::

    import OpenAI from "openai";

    const prompt = await new Promise((resolve) => {
        let data = "";
        process.stdin.on("data", (chunk) => (data += chunk));
        process.stdin.on("end", () => resolve(data));
    });

    const client = new OpenAI();
    const response = await client.chat.completions.create({
        model: "gpt-4.1",
        messages: [{ role: "user", content: prompt }],
    });

    process.stdout.write(response.choices[0].message.content);
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from redcon.core.policy import PolicySpec
from redcon.engine import RedconEngine
from redcon.runtime import AgentRuntime, RuntimeResult, RuntimeSession
from redcon.telemetry.store import append_observe_entry

_MISSING = object()


class NodeJSAgentRunner:
    """Redcon-optimised runner that delegates to a Node.js agent script.

    For every :meth:`run_task` call the runner:

    1. Intercepts the task description and repository path.
    2. Runs the full Redcon pipeline (scan → rank → compress → cache →
       delta) to produce an optimised context prompt.
    3. Spawns the configured Node.js *script* (or *command*), writes the
       assembled prompt to its **stdin**, and captures **stdout** as the
       response.
    4. Emits a run telemetry entry to ``.redcon/observe-history.json``.

    Parameters
    ----------
    script:
        Path to a ``.js`` / ``.mjs`` / ``.cjs`` entry point, or any shell
        command string that accepts a prompt on stdin and returns its response
        on stdout.  When *command* is provided *script* is ignored.
    repo:
        Default repository path scanned for context.  Can be overridden per
        call in :meth:`run_task`.
    command:
        Full command list, e.g. ``["node", "--experimental-vm-modules",
        "agent.js"]``.  Takes precedence over *script*.
    node_executable:
        Path (or name) of the Node.js executable.  Defaults to ``"node"``.
    adapter_name:
        Label used in telemetry entries to identify this runner.
    max_tokens:
        Token budget for the packed context (input side).
    top_files:
        Maximum number of ranked files the packer considers.
    policy:
        :class:`~redcon.core.policy.PolicySpec` evaluated after each
        pack call.
    strict:
        If ``True``, raise
        :class:`~redcon.engine.BudgetPolicyViolationError` on policy
        violations.
    delta:
        If ``True`` (default), pass the previous run artifact as delta
        context on subsequent turns.
    timeout:
        Seconds to wait for the Node.js process to complete.  ``None``
        means wait indefinitely.
    env:
        Extra environment variables forwarded to the Node.js process.  Merged
        on top of the current process environment.
    config_path:
        Path to a ``redcon.toml`` configuration file.
    session:
        An existing :class:`~redcon.runtime.RuntimeSession` to resume.
    engine:
        An existing :class:`~redcon.engine.RedconEngine` to
        reuse.
    telemetry_base_dir:
        Base directory for the observe-history store.  Defaults to *repo*.
    """

    def __init__(
        self,
        *,
        script: str | Path | None = None,
        repo: str | Path = ".",
        command: Sequence[str] | None = None,
        node_executable: str = "node",
        adapter_name: str = "nodejs",
        max_tokens: int | None = None,
        top_files: int | None = None,
        policy: PolicySpec | None = None,
        strict: bool = False,
        delta: bool = True,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        config_path: str | Path | None = None,
        session: RuntimeSession | None = None,
        engine: RedconEngine | None = None,
        telemetry_base_dir: str | Path | None = None,
    ) -> None:
        if command is None and script is None:
            raise ValueError("Either 'script' or 'command' must be provided.")

        self.name = adapter_name
        self.default_repo = Path(repo)
        self._timeout = timeout
        self._env = env
        self._telemetry_base_dir = telemetry_base_dir

        # Build the subprocess command list
        if command is not None:
            self._command: list[str] = list(command)
        else:
            self._command = [node_executable, str(script)]

        self._runtime = AgentRuntime(
            max_tokens=max_tokens,
            top_files=top_files,
            policy=policy,
            strict=strict,
            delta=delta,
            llm_fn=self._call_nodejs,
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
        """Run one agent turn: optimise context, call Node.js script, emit telemetry.

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
            Contains the :class:`~redcon.runtime.PreparedContext`,
            the stdout of the Node.js script as the LLM response, and session
            tracking fields.
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
        """The underlying :class:`~redcon.runtime.RuntimeSession`."""
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

    def _build_env(self) -> dict[str, str] | None:
        """Merge extra env vars on top of the current process environment."""
        if not self._env:
            return None
        merged = dict(os.environ)
        merged.update(self._env)
        return merged

    def _call_nodejs(self, prompt: str) -> str:
        """Send *prompt* to the Node.js script via stdin and return stdout."""
        try:
            proc = subprocess.run(
                self._command,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout,
                env=self._build_env(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Node.js executable not found.  Ensure Node.js is installed "
                f"and '{self._command[0]}' is on your PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            stderr_hint = ""
            if exc.stderr:
                raw = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
                if raw.strip():
                    stderr_hint = f"\nstderr: {raw.strip()}"
            raise RuntimeError(
                f"Node.js script timed out after {self._timeout}s.{stderr_hint}"
            ) from exc

        # Forward stderr for visibility without raising on non-zero exit
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)

        if proc.returncode != 0:
            raise RuntimeError(
                f"Node.js script exited with code {proc.returncode}."
                + (f"\nstderr: {proc.stderr.strip()}" if proc.stderr else "")
            )

        return proc.stdout

    def _emit_telemetry(self, result: RuntimeResult, *, task: str) -> None:
        ctx = result.prepared_context
        base_dir = self._telemetry_base_dir if self._telemetry_base_dir is not None else ctx.repo
        entry: dict[str, Any] = {
            "adapter": self.name,
            "command": self._command,
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
