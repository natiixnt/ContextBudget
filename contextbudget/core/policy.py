from __future__ import annotations

"""Strict budget policy loading and enforcement helpers."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextbudget.core.delta import effective_pack_metrics

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found, assignment]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

_RISK_ORDER = {
    "low": 1,
    "medium": 2,
    "high": 3,
}


@dataclass(slots=True)
class PolicySpec:
    """Policy thresholds for strict enforcement."""

    max_estimated_input_tokens: int | None = None
    max_files_included: int | None = None
    max_quality_risk_level: str | None = None
    min_estimated_savings_percentage: float | None = None


@dataclass(slots=True)
class PolicyResult:
    """Policy evaluation result for a run artifact."""

    passed: bool
    violations: list[str]
    checks: dict[str, dict[str, Any]]


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_risk(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in _RISK_ORDER:
        return normalized
    return None


def _risk_value(label: str | None) -> int:
    if label is None:
        return 0
    return _RISK_ORDER.get(label.lower(), 0)


def _parse_policy_dict(raw: dict[str, Any]) -> PolicySpec:
    policy_block = raw.get("policy")
    if isinstance(policy_block, dict):
        data = policy_block
    else:
        data = raw

    max_input_tokens = _to_int(data.get("max_estimated_input_tokens"))
    max_files = _to_int(data.get("max_files_included"))
    max_quality_risk = _normalize_risk(data.get("max_quality_risk_level"))
    min_savings = _to_float(data.get("min_estimated_savings_percentage"))

    return PolicySpec(
        max_estimated_input_tokens=max_input_tokens,
        max_files_included=max_files,
        max_quality_risk_level=max_quality_risk,
        min_estimated_savings_percentage=min_savings,
    )


def load_policy(path: Path) -> PolicySpec:
    """Load policy specification from TOML file."""

    if tomllib is None:
        raise RuntimeError("TOML parser unavailable. Install 'tomli' for Python < 3.11.")

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return PolicySpec()
    return _parse_policy_dict(raw)


def default_strict_policy(max_estimated_input_tokens: int | None = None) -> PolicySpec:
    """Return conservative strict policy used when --strict is enabled without a file."""

    return PolicySpec(max_estimated_input_tokens=max_estimated_input_tokens)


def _extract_metrics(run_data: dict[str, Any]) -> dict[str, Any]:
    budget = run_data.get("budget", {})
    if not isinstance(budget, dict):
        budget = {}
    effective = effective_pack_metrics(run_data)
    files_included = effective.get("files_included", [])
    if not isinstance(files_included, list):
        files_included = []
    estimated_input_tokens = int(effective.get("estimated_input_tokens", budget.get("estimated_input_tokens", 0)) or 0)
    estimated_saved_tokens = int(
        effective.get("estimated_saved_tokens", budget.get("estimated_saved_tokens", 0)) or 0
    )

    total_tokens = estimated_input_tokens + estimated_saved_tokens
    if total_tokens > 0:
        savings_pct = (estimated_saved_tokens / total_tokens) * 100.0
    else:
        savings_pct = 0.0

    return {
        "estimated_input_tokens": estimated_input_tokens,
        "files_included_count": len(files_included),
        "quality_risk_estimate": str(budget.get("quality_risk_estimate", "unknown")).lower(),
        "estimated_savings_percentage": round(savings_pct, 3),
    }


def evaluate_policy(run_data: dict[str, Any], policy: PolicySpec) -> PolicyResult:
    """Evaluate policy against run artifact data."""

    metrics = _extract_metrics(run_data)
    violations: list[str] = []
    checks: dict[str, dict[str, Any]] = {}

    if policy.max_estimated_input_tokens is not None:
        actual = int(metrics["estimated_input_tokens"])
        limit = int(policy.max_estimated_input_tokens)
        passed = actual <= limit
        checks["max_estimated_input_tokens"] = {"actual": actual, "limit": limit, "passed": passed}
        if not passed:
            violations.append(f"estimated input tokens {actual} exceed max {limit}")

    if policy.max_files_included is not None:
        actual = int(metrics["files_included_count"])
        limit = int(policy.max_files_included)
        passed = actual <= limit
        checks["max_files_included"] = {"actual": actual, "limit": limit, "passed": passed}
        if not passed:
            violations.append(f"files included {actual} exceed max {limit}")

    if policy.max_quality_risk_level is not None:
        actual_label = str(metrics["quality_risk_estimate"])
        limit_label = str(policy.max_quality_risk_level)
        passed = _risk_value(actual_label) <= _risk_value(limit_label)
        checks["max_quality_risk_level"] = {
            "actual": actual_label,
            "limit": limit_label,
            "passed": passed,
        }
        if not passed:
            violations.append(f"quality risk '{actual_label}' exceeds allowed '{limit_label}'")

    if policy.min_estimated_savings_percentage is not None:
        actual = float(metrics["estimated_savings_percentage"])
        limit = float(policy.min_estimated_savings_percentage)
        passed = actual >= limit
        checks["min_estimated_savings_percentage"] = {"actual": actual, "limit": limit, "passed": passed}
        if not passed:
            violations.append(f"estimated savings {actual:.2f}% is below minimum {limit:.2f}%")

    return PolicyResult(passed=not violations, violations=violations, checks=checks)


def policy_result_to_dict(result: PolicyResult) -> dict[str, Any]:
    """Convert policy result into serializable dict."""

    return {
        "passed": result.passed,
        "violations": list(result.violations),
        "checks": result.checks,
    }
