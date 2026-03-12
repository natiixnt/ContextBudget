from __future__ import annotations

"""Public library API for ContextBudget workflows."""

from pathlib import Path
from typing import Any

from contextbudget.config import ContextBudgetConfig, load_config
from contextbudget.core.benchmark import run_benchmark
from contextbudget.core.pipeline import (
    as_json_dict,
    run_diff_from_json,
    run_pack,
    run_plan,
    run_report_from_json,
)
from contextbudget.core.policy import (
    PolicySpec,
    default_strict_policy,
    evaluate_policy as evaluate_policy_artifact,
    load_policy,
    policy_result_to_dict,
)
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

    def _build_policy_telemetry_session(
        self,
        run_data: dict[str, Any],
        *,
        config_path: str | Path | None = None,
    ) -> TelemetrySession:
        repo = self._resolve_repo_from_run_data(run_data)
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
                "task": str(run_data.get("task", "")),
                "repo": str(repo),
            },
        )

    @staticmethod
    def make_policy(
        *,
        max_estimated_input_tokens: int | None = None,
        max_files_included: int | None = None,
        max_quality_risk_level: str | None = None,
        min_estimated_savings_percentage: float | None = None,
    ) -> PolicySpec:
        """Build a policy spec for programmatic policy checks."""

        return PolicySpec(
            max_estimated_input_tokens=max_estimated_input_tokens,
            max_files_included=max_files_included,
            max_quality_risk_level=max_quality_risk_level,
            min_estimated_savings_percentage=min_estimated_savings_percentage,
        )

    def plan(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Rank repository files relevant to a task."""

        repo_path = normalize_repo(repo)
        cfg = self._load_config(repo_path, config_path=config_path)
        effective_top_files = top_files if top_files is not None else cfg.budget.top_files
        return run_plan(
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
        max_tokens: int | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Build compressed context under token and file budgets."""

        repo_path = normalize_repo(repo)
        cfg = self._load_config(repo_path, config_path=config_path)
        report = run_pack(
            task,
            repo=repo_path,
            max_tokens=max_tokens,
            top_files=top_files,
            config=cfg,
            telemetry_sink=self._telemetry_sink,
        )
        return as_json_dict(report)

    def report(self, run_artifact: RunArtifactInput) -> dict[str, Any]:
        """Create a summary report from a run artifact."""

        run_data = self._load_run_artifact(run_artifact)
        return run_report_from_json(run_data)

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
            telemetry.emit(
                "policy_failed",
                violations=list(policy_result.get("violations", [])),
                checks=policy_result.get("checks", {}),
            )
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

    def benchmark(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        max_tokens: int | None = None,
        top_files: int | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Run deterministic strategy benchmark for a task and repository."""

        repo_path = normalize_repo(repo)
        cfg = self._load_config(repo_path, config_path=config_path)
        return run_benchmark(
            task=task,
            repo=repo_path,
            max_tokens=max_tokens,
            top_files=top_files,
            config=cfg,
        )


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
        return spec

    def pack(
        self,
        *,
        task: str,
        repo: str | Path = ".",
        max_tokens: int | None = None,
        top_files: int | None = None,
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
            max_tokens=effective_max_tokens,
            top_files=effective_top_files,
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
