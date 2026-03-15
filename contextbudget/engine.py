from __future__ import annotations

"""Public library API for ContextBudget workflows."""

import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from contextbudget.cache import update_run_history_artifacts
from contextbudget.config import ContextBudgetConfig, WorkspaceDefinition, load_config, load_workspace
from contextbudget.core.benchmark import run_benchmark
from contextbudget.core.delta import effective_pack_metrics
from contextbudget.core.pipeline import (
    as_json_dict,
    run_diff_from_json,
    run_heatmap,
    run_pack,
    run_plan,
    run_plan_agent,
    run_pr_audit,
    run_report_from_json,
)
from contextbudget.core.policy import (
    PolicySpec,
    default_strict_policy,
    evaluate_policy as evaluate_policy_artifact,
    load_policy,
    policy_result_to_dict,
)
from contextbudget.core.profiler import build_savings_profile, savings_profile_as_dict
from contextbudget.core.render import read_json
from contextbudget.schemas.models import normalize_repo
from contextbudget.telemetry import TelemetrySession, TelemetrySink, build_telemetry_sink


RunArtifactInput = dict[str, Any] | str | Path


class BudgetPolicyViolationError(RuntimeError):
    """Raised when strict budget policy checks fail."""

    def __init__(self, policy_result: dict[str, Any], run_artifact: dict[str, Any]) -> None:
        self.policy_result = policy_result
        self.run_artifact = run_artifact
        violations = policy_result.get("violations", [])
        if isinstance(violations, list) and violations:
            message = "; ".join(str(item) for item in violations)
        else:
            message = "context budget policy check failed"
        super().__init__(message)


class ContextBudgetEngine:
    """Stable programmatic interface for ContextBudget commands."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        telemetry_sink: TelemetrySink | None = None,
    ) -> None:
        self._default_config_path = self._resolve_path(config_path)
        self._telemetry_sink = telemetry_sink

    @staticmethod
    def _resolve_path(path: str | Path | None) -> Path | None:
        if path is None:
            return None
        return Path(path).resolve()

    def _load_config(self, repo: Path, config_path: str | Path | None = None) -> ContextBudgetConfig:
        resolved_config_path = self._resolve_path(config_path) or self._default_config_path
        return load_config(repo, config_path=resolved_config_path)

    def _load_workspace(
        self,
        workspace_path: str | Path,
        config_path: str | Path | None = None,
    ) -> WorkspaceDefinition:
        resolved_config_path = self._resolve_path(config_path) or self._default_config_path
        return load_workspace(Path(workspace_path).resolve(), config_path=resolved_config_path)

    @staticmethod
    def _load_run_artifact(run_artifact: RunArtifactInput) -> dict[str, Any]:
        if isinstance(run_artifact, dict):
            return dict(run_artifact)
        if isinstance(run_artifact, (str, Path)):
            return read_json(Path(run_artifact))
        raise TypeError("run_artifact must be a dict, path string, or Path")

    def _resolve_repo_from_run_data(self, run_data: dict[str, Any]) -> Path:
        raw_repo = run_data.get("repo")
        if isinstance(raw_repo, str) and raw_repo.strip():
            return normalize_repo(raw_repo)
        return Path.cwd()

    def _resolve_workspace_from_run_data(self, run_data: dict[str, Any]) -> Path | None:
        raw_workspace = run_data.get("workspace")
        if isinstance(raw_workspace, str) and raw_workspace.strip():
            return Path(raw_workspace).resolve()
        return None

    def _build_policy_telemetry_session(
        self,
        run_data: dict[str, Any],
        *,
        config_path: str | Path | None = None,
    ) -> TelemetrySession:
        repo = self._resolve_repo_from_run_data(run_data)
        workspace_path = self._resolve_workspace_from_run_data(run_data)
        if workspace_path is not None:
            cfg = self._load_workspace(workspace_path, config_path=config_path).config
        else:
            cfg = self._load_config(repo, config_path=config_path)
        sink = self._telemetry_sink or build_telemetry_sink(
            repo=repo,
            enabled=cfg.telemetry.enabled,
            sink=cfg.telemetry.sink,
            file_path=cfg.telemetry.file_path,
        )
        return TelemetrySession(
            sink=sink,
            base_payload={
                "command": str(run_data.get("command", "policy")),
                "repo": repo,
            },
        )

    @staticmethod
    def make_policy(
        *,
        max_estimated_input_tokens: int | None = None,
        max_files_included: int | None = None,
        max_quality_risk_level: str | None = None,
        min_estimated_savings_percentage: float | None = None,
        max_context_size_bytes: int | None = None,
    ) -> PolicySpec:
        """Build a policy spec for programmatic policy checks."""

        return PolicySpec(
            max_estimated_input_tokens=max_estimated_input_tokens,
            max_files_included=max_files_included,
            max_quality_risk_level=max_quality_risk_level,
            min_estimated_savings_percentage=min_estimated_savings_percentage,
            max_context_size_bytes=max_context_size_bytes,
        )

    def plan(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Rank repository or workspace files relevant to a task."""

        repo_path = normalize_repo(repo)
        if workspace is not None:
            workspace_definition = self._load_workspace(workspace, config_path=config_path)
            effective_top_files = top_files if top_files is not None else workspace_definition.config.budget.top_files
            return run_plan(
                task,
                repo=workspace_definition.root,
                top_n=effective_top_files,
                config=workspace_definition.config,
                telemetry_sink=self._telemetry_sink,
                workspace=workspace_definition,
            )

        cfg = self._load_config(repo_path, config_path=config_path)
        effective_top_files = top_files if top_files is not None else cfg.budget.top_files
        return run_plan(
            task,
            repo=repo_path,
            top_n=effective_top_files,
            config=cfg,
            telemetry_sink=self._telemetry_sink,
        )

    def plan_agent(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Build a multi-step context plan for agent workflows."""

        repo_path = normalize_repo(repo)
        if workspace is not None:
            workspace_definition = self._load_workspace(workspace, config_path=config_path)
            effective_top_files = top_files if top_files is not None else workspace_definition.config.budget.top_files
            return run_plan_agent(
                task,
                repo=workspace_definition.root,
                top_n=effective_top_files,
                config=workspace_definition.config,
                telemetry_sink=self._telemetry_sink,
                workspace=workspace_definition,
            )

        cfg = self._load_config(repo_path, config_path=config_path)
        effective_top_files = top_files if top_files is not None else cfg.budget.top_files
        return run_plan_agent(
            task,
            repo=repo_path,
            top_n=effective_top_files,
            config=cfg,
            telemetry_sink=self._telemetry_sink,
        )

    def pack(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        delta_from: RunArtifactInput | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Build compressed context under token and file budgets."""

        repo_path = normalize_repo(repo)
        if workspace is not None:
            workspace_definition = self._load_workspace(workspace, config_path=config_path)
            report = run_pack(
                task,
                repo=workspace_definition.root,
                max_tokens=max_tokens,
                top_files=top_files,
                delta_from=delta_from,
                config=workspace_definition.config,
                telemetry_sink=self._telemetry_sink,
                workspace=workspace_definition,
            )
            return as_json_dict(report)

        cfg = self._load_config(repo_path, config_path=config_path)
        report = run_pack(
            task,
            repo=repo_path,
            max_tokens=max_tokens,
            top_files=top_files,
            delta_from=delta_from,
            config=cfg,
            telemetry_sink=self._telemetry_sink,
        )
        return as_json_dict(report)

    def report(self, run_artifact: RunArtifactInput) -> dict[str, Any]:
        """Create a summary report from a run artifact."""

        run_data = self._load_run_artifact(run_artifact)
        return run_report_from_json(run_data)

    def record_history_artifacts(
        self,
        run_artifact: RunArtifactInput,
        *,
        artifacts: Mapping[str, str],
        config_path: str | Path | None = None,
    ) -> bool:
        """Attach persisted artifact paths to a previously recorded history entry."""

        run_data = self._load_run_artifact(run_artifact)
        generated_at = str(run_data.get("generated_at", "") or "").strip()
        if not generated_at:
            return False

        repo = self._resolve_repo_from_run_data(run_data)
        workspace_path = self._resolve_workspace_from_run_data(run_data)
        if workspace_path is not None:
            cfg = self._load_workspace(workspace_path, config_path=config_path).config
        else:
            cfg = self._load_config(repo, config_path=config_path)

        return update_run_history_artifacts(
            repo,
            generated_at=generated_at,
            result_artifacts=artifacts,
            enabled=cfg.cache.run_history_enabled,
            history_file=cfg.cache.history_file,
        )

    def evaluate_policy(
        self,
        run_artifact: RunArtifactInput,
        *,
        policy: PolicySpec | None = None,
        policy_path: str | Path | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Evaluate a run artifact against a policy and return serializable result."""

        if policy is not None and policy_path is not None:
            raise ValueError("Provide either policy or policy_path, not both.")

        run_data = self._load_run_artifact(run_artifact)
        if policy is not None:
            spec = policy
        elif policy_path is not None:
            spec = load_policy(Path(policy_path))
        else:
            spec = PolicySpec()

        policy_result = policy_result_to_dict(evaluate_policy_artifact(run_data, spec))
        if not bool(policy_result.get("passed", False)):
            telemetry = self._build_policy_telemetry_session(run_data, config_path=config_path)
            budget = run_data.get("budget", {})
            files_skipped = run_data.get("files_skipped", [])
            effective = effective_pack_metrics(run_data)
            effective_files = effective.get("files_included", [])
            if not isinstance(effective_files, list):
                effective_files = []
            _policy_emit_kwargs: dict = dict(
                violations=list(policy_result.get("violations", [])),
                checks=policy_result.get("checks", {}),
                max_tokens=run_data.get("max_tokens"),
                estimated_input_tokens=effective.get("estimated_input_tokens"),
                estimated_saved_tokens=effective.get("estimated_saved_tokens"),
                files_included=len(effective_files),
                files_skipped=len(files_skipped) if isinstance(files_skipped, list) else None,
                cache_hits=run_data.get("cache_hits"),
                duplicate_reads_prevented=(
                    budget.get("duplicate_reads_prevented", 0) if isinstance(budget, dict) else None
                ),
                quality_risk_estimate=budget.get("quality_risk_estimate") if isinstance(budget, dict) else None,
            )
            telemetry.emit("policy_failed", **_policy_emit_kwargs)
            telemetry.emit("policy_violation", **_policy_emit_kwargs)
        return policy_result

    def diff(
        self,
        old_run_artifact: RunArtifactInput,
        new_run_artifact: RunArtifactInput,
        *,
        old_label: str = "old",
        new_label: str = "new",
    ) -> dict[str, Any]:
        """Compare two run artifacts and return a deterministic diff payload."""

        old_data = self._load_run_artifact(old_run_artifact)
        new_data = self._load_run_artifact(new_run_artifact)
        return run_diff_from_json(old_data, new_data, old_label=old_label, new_label=new_label)

    def pr_audit(
        self,
        *,
        repo: str | Path = ".",
        base_ref: str | None = None,
        head_ref: str | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Analyze a pull-request diff for token and context-growth impact."""

        repo_path = normalize_repo(repo)
        cfg = self._load_config(repo_path, config_path=config_path)
        return run_pr_audit(
            repo_path,
            base_ref=base_ref,
            head_ref=head_ref,
            config=cfg,
        )

    def heatmap(
        self,
        history: Sequence[str | Path] | None = None,
        *,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Aggregate historical pack artifacts into a heatmap report."""

        return run_heatmap(history=history, limit=limit)

    def benchmark(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Run deterministic strategy benchmark for a task and repository or workspace."""

        repo_path = normalize_repo(repo)
        if workspace is not None:
            workspace_definition = self._load_workspace(workspace, config_path=config_path)
            return run_benchmark(
                task=task,
                repo=workspace_definition.root,
                max_tokens=max_tokens,
                top_files=top_files,
                config=workspace_definition.config,
                telemetry_sink=self._telemetry_sink,
                workspace=workspace_definition,
            )

        cfg = self._load_config(repo_path, config_path=config_path)
        return run_benchmark(
            task=task,
            repo=repo_path,
            max_tokens=max_tokens,
            top_files=top_files,
            config=cfg,
            telemetry_sink=self._telemetry_sink,
        )

    def profile(self, run: RunArtifactInput) -> dict[str, Any]:
        """Build a token savings profile from a pack run artifact.

        ``run`` may be a dict (already loaded), a path string, or a Path to a
        run JSON file produced by :meth:`pack`.
        """
        if isinstance(run, dict):
            run_data = run
            run_json = ""
        else:
            run_path = Path(run)
            run_data = read_json(run_path)
            run_json = str(run_path)
        profile = build_savings_profile(run_data, run_json=run_json)
        return savings_profile_as_dict(profile)


class BudgetGuard:
    """High-level helper for budgeted packing and strict policy enforcement."""

    def __init__(
        self,
        *,
        max_tokens: int | None = None,
        top_files: int | None = None,
        max_files_included: int | None = None,
        max_quality_risk_level: str | None = None,
        min_estimated_savings_percentage: float | None = None,
        max_context_size_bytes: int | None = None,
        policy_path: str | Path | None = None,
        strict: bool = False,
        config_path: str | Path | None = None,
        engine: ContextBudgetEngine | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.top_files = top_files
        self.max_files_included = max_files_included
        self.max_quality_risk_level = max_quality_risk_level
        self.min_estimated_savings_percentage = min_estimated_savings_percentage
        self.max_context_size_bytes = max_context_size_bytes
        self.policy_path = Path(policy_path).resolve() if policy_path is not None else None
        self.strict = strict
        self.engine = engine if engine is not None else ContextBudgetEngine(config_path=config_path)

    def _build_policy_spec(
        self,
        *,
        fallback_max_tokens: int | None = None,
        policy_path: str | Path | None = None,
    ) -> PolicySpec:
        effective_policy_path = Path(policy_path).resolve() if policy_path is not None else self.policy_path
        if effective_policy_path is not None:
            spec = load_policy(effective_policy_path)
        else:
            spec = PolicySpec()

        if spec.max_estimated_input_tokens is None:
            if self.max_tokens is not None:
                spec.max_estimated_input_tokens = self.max_tokens
            elif fallback_max_tokens is not None:
                spec.max_estimated_input_tokens = fallback_max_tokens

        if self.max_files_included is not None:
            spec.max_files_included = self.max_files_included
        if self.max_quality_risk_level is not None:
            spec.max_quality_risk_level = self.max_quality_risk_level
        if self.min_estimated_savings_percentage is not None:
            spec.min_estimated_savings_percentage = self.min_estimated_savings_percentage
        if self.max_context_size_bytes is not None:
            spec.max_context_size_bytes = self.max_context_size_bytes
        return spec

    def pack(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        delta_from: RunArtifactInput | None = None,
        strict: bool | None = None,
        policy_path: str | Path | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """
        Run packing with configured defaults.

        When strict mode is enabled, this method evaluates the run against the
        resolved policy and raises ``BudgetPolicyViolationError`` on violations.
        """

        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        effective_top_files = top_files if top_files is not None else self.top_files
        run_data = self.engine.pack(
            task=task,
            repo=repo,
            workspace=workspace,
            max_tokens=effective_max_tokens,
            top_files=effective_top_files,
            delta_from=delta_from,
            config_path=config_path,
        )

        enforce = self.strict if strict is None else strict
        if not enforce:
            return run_data

        fallback_max_tokens: int | None
        try:
            fallback_max_tokens = int(run_data.get("max_tokens", 0))
        except (TypeError, ValueError):
            fallback_max_tokens = None

        if self.policy_path is None and policy_path is None:
            policy_spec = default_strict_policy(max_estimated_input_tokens=fallback_max_tokens)
            if self.max_files_included is not None:
                policy_spec.max_files_included = self.max_files_included
            if self.max_quality_risk_level is not None:
                policy_spec.max_quality_risk_level = self.max_quality_risk_level
            if self.min_estimated_savings_percentage is not None:
                policy_spec.min_estimated_savings_percentage = self.min_estimated_savings_percentage
            if self.max_context_size_bytes is not None:
                policy_spec.max_context_size_bytes = self.max_context_size_bytes
        else:
            policy_spec = self._build_policy_spec(
                fallback_max_tokens=fallback_max_tokens,
                policy_path=policy_path,
            )

        policy_result = self.engine.evaluate_policy(run_data, policy=policy_spec)
        run_data["policy"] = policy_result
        if not bool(policy_result.get("passed", False)):
            raise BudgetPolicyViolationError(policy_result=policy_result, run_artifact=run_data)
        return run_data

    def evaluate_policy(
        self,
        run_artifact: RunArtifactInput,
        *,
        policy_path: str | Path | None = None,
        strict: bool = False,
    ) -> dict[str, Any]:
        """Evaluate a run artifact against this guard's policy settings."""

        policy_spec = self._build_policy_spec(policy_path=policy_path)
        policy_result = self.engine.evaluate_policy(run_artifact, policy=policy_spec)
        if strict and not bool(policy_result.get("passed", False)):
            run_data = self.engine._load_run_artifact(run_artifact)
            run_data["policy"] = policy_result
            raise BudgetPolicyViolationError(policy_result=policy_result, run_artifact=run_data)
        return policy_result

    # ------------------------------------------------------------------
    # SDK interface methods for agent framework integration
    # ------------------------------------------------------------------

    def pack_context(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        delta_from: RunArtifactInput | None = None,
        strict: bool | None = None,
        policy_path: str | Path | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Pack repository context for a task, respecting the configured token budget.

        Primary entry point for SDK consumers and agent frameworks.  Equivalent
        to :meth:`pack` but named to align with the agent SDK interface pattern.

        Returns the packed-context run artifact as a dictionary.  When *strict*
        mode is active (or inherited from the guard), a
        :class:`BudgetPolicyViolationError` is raised on policy violations.

        Example::

            from contextbudget import BudgetGuard

            guard = BudgetGuard(max_tokens=30000)
            result = guard.pack_context(task="add caching", repo=".")
            print(result["budget"]["estimated_input_tokens"])
        """

        return self.pack(
            task=task,
            repo=repo,
            workspace=workspace,
            max_tokens=max_tokens,
            top_files=top_files,
            delta_from=delta_from,
            strict=strict,
            policy_path=policy_path,
            config_path=config_path,
        )

    def simulate_agent(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Simulate a multi-step agent workflow for a task.

        Returns a step-by-step workflow plan describing how context would be
        distributed across lifecycle steps such as *inspect*, *implement*,
        *test*, *validate*, and *document*.  Each step includes assigned context
        files with token estimates so the caller can plan prompt budgets before
        packing individual prompts.

        Artifact keys:

        - ``steps`` – ordered workflow steps, each with ``id``, ``context``, and
          ``estimated_tokens``
        - ``shared_context`` – files reused across multiple steps
        - ``total_estimated_tokens`` – sum across all steps (including reuse)
        - ``unique_context_tokens`` – tokens counted once per unique file
        - ``reused_context_tokens`` – tokens attributed to shared context reuse

        Example::

            from contextbudget import BudgetGuard

            guard = BudgetGuard(max_tokens=30000)
            plan = guard.simulate_agent(task="refactor auth flow", repo=".")
            for step in plan["steps"]:
                print(step["id"], step["estimated_tokens"])
        """

        effective_top_files = top_files if top_files is not None else self.top_files
        return self.engine.plan_agent(
            task=task,
            repo=repo,
            workspace=workspace,
            top_files=effective_top_files,
            config_path=config_path,
        )

    def profile_run(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        workspace: str | Path | None = None,
        max_tokens: int | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Pack context and return the run artifact augmented with profiling data.

        Measures wall-clock time for the pack operation and derives compression
        and budget metrics, making it easy for agent frameworks to log or display
        a one-stop summary without navigating the full artifact structure.

        The run artifact is returned with an additional ``profile`` block::

            {
                "elapsed_ms": 142,
                "estimated_input_tokens": 8200,
                "estimated_saved_tokens": 3100,
                "compression_ratio": 0.2741,
                "files_included_count": 6,
                "files_skipped_count": 2,
                "quality_risk_estimate": "low"
            }

        Example::

            from contextbudget import BudgetGuard

            guard = BudgetGuard(max_tokens=30000)
            result = guard.profile_run(task="add caching", repo=".")
            p = result["profile"]
            print(f"packed in {p['elapsed_ms']} ms, ratio {p['compression_ratio']:.1%}")
        """

        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        effective_top_files = top_files if top_files is not None else self.top_files

        t0 = time.perf_counter()
        run_data = self.engine.pack(
            task=task,
            repo=repo,
            workspace=workspace,
            max_tokens=effective_max_tokens,
            top_files=effective_top_files,
            config_path=config_path,
        )
        elapsed_ms = round((time.perf_counter() - t0) * 1000)

        budget = run_data.get("budget", {})
        if not isinstance(budget, dict):
            budget = {}

        estimated_input = int(budget.get("estimated_input_tokens", 0) or 0)
        estimated_saved = int(budget.get("estimated_saved_tokens", 0) or 0)
        original_tokens = estimated_input + estimated_saved
        compression_ratio = round(estimated_saved / original_tokens, 4) if original_tokens > 0 else 0.0

        files_included = run_data.get("files_included", [])
        files_skipped = run_data.get("files_skipped", [])

        run_data["profile"] = {
            "elapsed_ms": elapsed_ms,
            "estimated_input_tokens": estimated_input,
            "estimated_saved_tokens": estimated_saved,
            "compression_ratio": compression_ratio,
            "files_included_count": len(files_included) if isinstance(files_included, list) else 0,
            "files_skipped_count": len(files_skipped) if isinstance(files_skipped, list) else 0,
            "quality_risk_estimate": budget.get("quality_risk_estimate", "unknown"),
        }

        return run_data
