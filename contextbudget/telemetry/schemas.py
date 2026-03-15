from __future__ import annotations

"""Versioned analytics event schema definitions and builders."""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Mapping


EventName = Literal[
    "run_started",
    "scan_completed",
    "scoring_completed",
    "pack_completed",
    "plan_completed",
    "cache_hit",
    "delta_applied",
    "benchmark_completed",
    "policy_failed",
    "policy_violation",
]

ANALYTICS_EVENT_NAMES: tuple[EventName, ...] = (
    "run_started",
    "scan_completed",
    "scoring_completed",
    "pack_completed",
    "plan_completed",
    "cache_hit",
    "delta_applied",
    "benchmark_completed",
    "policy_failed",
    "policy_violation",
)
ANALYTICS_SCHEMA_V1 = "v1"
EVENT_SCHEMA_VERSIONS: dict[str, str] = {name: ANALYTICS_SCHEMA_V1 for name in ANALYTICS_EVENT_NAMES}


@dataclass(slots=True)
class RepositoryIdentifiers:
    """Privacy-safe repository identifiers derived from local paths."""

    repository_id: str
    workspace_id: str


@dataclass(slots=True)
class TokenEstimates:
    """Token estimate metrics included in analytics events."""

    max_tokens: int | None = None
    estimated_input_tokens: int | None = None
    estimated_saved_tokens: int | None = None
    baseline_full_context_tokens: int | None = None


@dataclass(slots=True)
class FileCounts:
    """Repository and output file count metrics included in analytics events."""

    scanned_files: int | None = None
    ranked_files: int | None = None
    included_files: int | None = None
    skipped_files: int | None = None
    top_files: int | None = None
    strategy_count: int | None = None


@dataclass(slots=True)
class CacheStats:
    """Cache-related metrics included in analytics events."""

    cache_hits: int | None = None
    duplicate_reads_prevented: int | None = None
    tokens_saved: int | None = None
    backend: str | None = None
    fragment_hits: int | None = None
    fragment_misses: int | None = None


@dataclass(slots=True)
class PolicyOutcome:
    """Policy evaluation outcome included in analytics events."""

    evaluated: bool = False
    passed: bool | None = None
    violation_count: int = 0
    violations: list[str] = field(default_factory=list)
    failing_checks: list[str] = field(default_factory=list)
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class DeltaMetrics:
    """Delta context metrics included in delta_applied events."""

    files_added: int | None = None
    files_removed: int | None = None
    files_changed: int | None = None
    delta_tokens: int | None = None
    tokens_saved: int | None = None
    has_previous_run: bool = False
    slices_changed: int | None = None
    symbols_changed: int | None = None


@dataclass(slots=True)
class BenchmarkStrategySummary:
    """Stable, privacy-safe benchmark strategy summary."""

    name: str
    estimated_input_tokens: int | None = None
    estimated_saved_tokens: int | None = None
    included_files: int | None = None
    skipped_files: int | None = None
    cache_hits: int | None = None
    duplicate_reads_prevented: int | None = None
    quality_risk_estimate: str | None = None
    runtime_ms: int | None = None


@dataclass(slots=True)
class BenchmarkSummary:
    """Benchmark-specific analytics payload section."""

    scan_runtime_ms: int | None = None
    strategies: list[BenchmarkStrategySummary] = field(default_factory=list)


@dataclass(slots=True)
class AnalyticsEventPayload:
    """Stable payload shared by all analytics events."""

    command: str
    repository: RepositoryIdentifiers
    tokens: TokenEstimates = field(default_factory=TokenEstimates)
    files: FileCounts = field(default_factory=FileCounts)
    cache: CacheStats = field(default_factory=CacheStats)
    policy: PolicyOutcome = field(default_factory=PolicyOutcome)
    quality_risk_estimate: str | None = None
    benchmark: BenchmarkSummary = field(default_factory=BenchmarkSummary)
    delta: DeltaMetrics = field(default_factory=DeltaMetrics)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _path_digest(path: Path) -> str:
    return f"sha256:{sha256(str(path).encode('utf-8')).hexdigest()}"


def _normalize_repo_path(repo: str | Path | None) -> Path:
    candidate = Path("." if repo is None else repo).expanduser()
    try:
        return candidate.resolve()
    except OSError:
        return candidate.absolute()


def build_repository_identifiers(repo: str | Path | None) -> RepositoryIdentifiers:
    """Build deterministic repository/workspace identifiers without exposing raw paths."""

    normalized_repo = _normalize_repo_path(repo)
    workspace = normalized_repo.parent
    return RepositoryIdentifiers(
        repository_id=_path_digest(normalized_repo),
        workspace_id=_path_digest(workspace),
    )


def _policy_checks(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for check_name, check_data in value.items():
        if not isinstance(check_data, Mapping):
            continue
        result[str(check_name)] = {str(key): raw for key, raw in check_data.items()}
    return result


def _failing_checks(checks: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return sorted(name for name, data in checks.items() if data.get("passed") is False)


def _strategy_summaries(value: Any) -> list[BenchmarkStrategySummary]:
    if not isinstance(value, list):
        return []

    summaries: list[BenchmarkStrategySummary] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        files_included = item.get("files_included", [])
        files_skipped = item.get("files_skipped", [])
        summaries.append(
            BenchmarkStrategySummary(
                name=str(item.get("strategy", "")),
                estimated_input_tokens=_int_or_none(item.get("estimated_input_tokens")),
                estimated_saved_tokens=_int_or_none(item.get("estimated_saved_tokens")),
                included_files=len(files_included) if isinstance(files_included, list) else None,
                skipped_files=len(files_skipped) if isinstance(files_skipped, list) else None,
                cache_hits=_int_or_none(item.get("cache_hits")),
                duplicate_reads_prevented=_int_or_none(item.get("duplicate_reads_prevented")),
                quality_risk_estimate=_string_or_none(item.get("quality_risk_estimate")),
                runtime_ms=_int_or_none(item.get("runtime_ms")),
            )
        )
    return summaries


def _max_int(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return max(present)


def _min_int(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return min(present)


def _base_payload(command: str, repo: str | Path | None) -> AnalyticsEventPayload:
    return AnalyticsEventPayload(
        command=command,
        repository=build_repository_identifiers(repo),
    )


def _build_run_started_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    payload.tokens.max_tokens = _int_or_none(data.get("max_tokens"))
    payload.files.top_files = _int_or_none(data.get("top_files"))
    return asdict(payload)


def _build_scan_completed_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    payload.files.scanned_files = _int_or_none(data.get("scanned_files"))
    return asdict(payload)


def _build_scoring_completed_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    payload.files.scanned_files = _int_or_none(data.get("scanned_files"))
    payload.files.ranked_files = _int_or_none(data.get("ranked_files"))
    payload.files.top_files = _int_or_none(data.get("top_files"))
    return asdict(payload)


def _build_pack_completed_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    payload.tokens.max_tokens = _int_or_none(data.get("max_tokens"))
    payload.tokens.estimated_input_tokens = _int_or_none(data.get("estimated_input_tokens"))
    payload.tokens.estimated_saved_tokens = _int_or_none(data.get("estimated_saved_tokens"))
    payload.files.scanned_files = _int_or_none(data.get("scanned_files"))
    payload.files.ranked_files = _int_or_none(data.get("ranked_files"))
    payload.files.included_files = _int_or_none(data.get("files_included"))
    payload.files.skipped_files = _int_or_none(data.get("files_skipped"))
    payload.files.top_files = _int_or_none(data.get("top_files"))
    payload.cache.cache_hits = _int_or_none(data.get("cache_hits"))
    payload.cache.duplicate_reads_prevented = _int_or_none(data.get("duplicate_reads_prevented"))
    payload.quality_risk_estimate = _string_or_none(data.get("quality_risk_estimate"))
    return asdict(payload)


def _build_benchmark_completed_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    strategies = _strategy_summaries(data.get("strategies"))
    payload.tokens.max_tokens = _int_or_none(data.get("max_tokens"))
    payload.tokens.baseline_full_context_tokens = _int_or_none(data.get("baseline_full_context_tokens"))
    payload.tokens.estimated_input_tokens = _min_int([item.estimated_input_tokens for item in strategies])
    payload.tokens.estimated_saved_tokens = _max_int([item.estimated_saved_tokens for item in strategies])
    payload.files.scanned_files = _int_or_none(data.get("scanned_files"))
    payload.files.ranked_files = _int_or_none(data.get("ranked_files"))
    payload.files.top_files = _int_or_none(data.get("top_files"))
    payload.files.strategy_count = len(strategies)
    payload.cache.cache_hits = _max_int([item.cache_hits for item in strategies])
    payload.cache.duplicate_reads_prevented = _max_int([item.duplicate_reads_prevented for item in strategies])
    payload.benchmark.scan_runtime_ms = _int_or_none(data.get("scan_runtime_ms"))
    payload.benchmark.strategies = strategies
    return asdict(payload)


def _build_policy_failed_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    checks = _policy_checks(data.get("checks"))
    violations = _string_list(data.get("violations"))

    payload.tokens.max_tokens = _int_or_none(data.get("max_tokens"))
    payload.tokens.estimated_input_tokens = _int_or_none(data.get("estimated_input_tokens"))
    payload.tokens.estimated_saved_tokens = _int_or_none(data.get("estimated_saved_tokens"))
    payload.files.included_files = _int_or_none(data.get("files_included"))
    payload.files.skipped_files = _int_or_none(data.get("files_skipped"))
    payload.cache.cache_hits = _int_or_none(data.get("cache_hits"))
    payload.cache.duplicate_reads_prevented = _int_or_none(data.get("duplicate_reads_prevented"))
    payload.policy.evaluated = True
    payload.policy.passed = False
    payload.policy.violation_count = len(violations)
    payload.policy.violations = violations
    payload.policy.checks = checks
    payload.policy.failing_checks = _failing_checks(checks)
    payload.quality_risk_estimate = _string_or_none(data.get("quality_risk_estimate"))
    return asdict(payload)


def _build_cache_hit_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    payload.cache.cache_hits = _int_or_none(data.get("total_hits"))
    payload.cache.tokens_saved = _int_or_none(data.get("tokens_saved"))
    payload.cache.backend = _string_or_none(data.get("backend"))
    payload.cache.fragment_hits = _int_or_none(data.get("fragment_hits"))
    payload.cache.fragment_misses = _int_or_none(data.get("fragment_misses"))
    return asdict(payload)


def _build_delta_applied_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    payload.delta.files_added = _int_or_none(data.get("files_added"))
    payload.delta.files_removed = _int_or_none(data.get("files_removed"))
    payload.delta.files_changed = _int_or_none(data.get("files_changed"))
    payload.delta.delta_tokens = _int_or_none(data.get("delta_tokens"))
    payload.delta.tokens_saved = _int_or_none(data.get("tokens_saved"))
    payload.delta.has_previous_run = bool(data.get("has_previous_run", False))
    payload.delta.slices_changed = _int_or_none(data.get("slices_changed"))
    payload.delta.symbols_changed = _int_or_none(data.get("symbols_changed"))
    return asdict(payload)


def _build_policy_violation_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    # Identical structure to policy_failed; canonical name going forward.
    return _build_policy_failed_payload(command, repo, data)


def _build_plan_completed_payload(command: str, repo: str | Path | None, data: Mapping[str, Any]) -> dict[str, Any]:
    payload = _base_payload(command, repo)
    payload.files.scanned_files = _int_or_none(data.get("scanned_files"))
    payload.files.ranked_files = _int_or_none(data.get("ranked_files"))
    payload.files.top_files = _int_or_none(data.get("top_files"))
    payload.tokens.estimated_input_tokens = _int_or_none(data.get("total_estimated_tokens"))
    return asdict(payload)


_PAYLOAD_BUILDERS = {
    "run_started": _build_run_started_payload,
    "scan_completed": _build_scan_completed_payload,
    "scoring_completed": _build_scoring_completed_payload,
    "pack_completed": _build_pack_completed_payload,
    "plan_completed": _build_plan_completed_payload,
    "cache_hit": _build_cache_hit_payload,
    "delta_applied": _build_delta_applied_payload,
    "benchmark_completed": _build_benchmark_completed_payload,
    "policy_failed": _build_policy_failed_payload,
    "policy_violation": _build_policy_violation_payload,
}


def schema_version_for_event(name: str) -> str:
    """Return the version tag for a named analytics event."""

    return EVENT_SCHEMA_VERSIONS.get(name, ANALYTICS_SCHEMA_V1)


def build_analytics_payload(
    name: str,
    *,
    command: str,
    repo: str | Path | None,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a versioned analytics payload for a supported event name."""

    builder = _PAYLOAD_BUILDERS.get(name)
    if builder is None:
        return asdict(_base_payload(command, repo))
    return builder(command, repo, data)


__all__ = [
    "ANALYTICS_EVENT_NAMES",
    "ANALYTICS_SCHEMA_V1",
    "AnalyticsEventPayload",
    "BenchmarkStrategySummary",
    "BenchmarkSummary",
    "CacheStats",
    "DeltaMetrics",
    "EVENT_SCHEMA_VERSIONS",
    "EventName",
    "FileCounts",
    "PolicyOutcome",
    "RepositoryIdentifiers",
    "TokenEstimates",
    "build_analytics_payload",
    "build_repository_identifiers",
    "schema_version_for_event",
]
