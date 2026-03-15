from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

# Mirrors ANALYTICS_EVENT_NAMES from contextbudget/telemetry/schemas.py
VALID_EVENT_NAMES: frozenset[str] = frozenset({
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
})

SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"v1"})


class IncomingEvent(BaseModel):
    name: str
    schema_version: str
    timestamp: datetime
    run_id: str
    payload: dict[str, Any] = {}

    @field_validator("name")
    @classmethod
    def name_must_be_valid(cls, v: str) -> str:
        if v not in VALID_EVENT_NAMES:
            raise ValueError(
                f"Unknown event name '{v}'. Valid names: {sorted(VALID_EVENT_NAMES)}"
            )
        return v

    @field_validator("schema_version")
    @classmethod
    def schema_version_must_be_supported(cls, v: str) -> str:
        if v not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported schema_version '{v}'. Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        return v

    @field_validator("run_id")
    @classmethod
    def run_id_must_be_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("run_id must not be empty")
        return v


class IngestResponse(BaseModel):
    accepted: int
    event_ids: list[int]


class TokensPerRepoRow(BaseModel):
    repository_id: str
    total_tokens: int | None
    run_count: int


class TokensPerTaskRow(BaseModel):
    command: str
    total_tokens: int | None
    run_count: int


class CacheHitRateRow(BaseModel):
    runs_with_cache_hits: int
    total_runs: int
    cache_hit_rate_pct: float | None
    total_cache_hits: int | None
    total_tokens_saved: int | None


class DashboardOverview(BaseModel):
    total_tokens_used: int
    total_tokens_saved: int
    savings_rate: float | None
    cache_hit_rate_pct: float | None
    total_runs: int
    runs_with_cache_hits: int


class RepositoryStats(BaseModel):
    repository_id: str
    total_tokens_used: int
    total_tokens_saved: int
    run_count: int
    savings_rate: float | None


class DashboardRepositories(BaseModel):
    repositories: list[RepositoryStats]


class CommandSavings(BaseModel):
    command: str | None
    tokens_used: int
    tokens_saved: int
    run_count: int
    savings_rate: float | None


class DashboardSavings(BaseModel):
    total_tokens_used: int
    total_tokens_saved: int
    savings_rate: float | None
    by_command: list[CommandSavings]


class DashboardHeatmap(BaseModel):
    total_runs: int
    avg_scanned_files: float | None
    avg_included_files: float | None
    avg_top_files: float | None
    note: str


# ---------------------------------------------------------------------------
# Control plane — orgs / projects / repos
# ---------------------------------------------------------------------------

class OrgCreate(BaseModel):
    slug: str
    display_name: str


class OrgResponse(BaseModel):
    id: int
    slug: str
    display_name: str
    created_at: datetime


class ProjectCreate(BaseModel):
    slug: str
    display_name: str


class ProjectResponse(BaseModel):
    id: int
    org_id: int
    slug: str
    display_name: str
    created_at: datetime


class RepoCreate(BaseModel):
    slug: str
    display_name: str
    # SHA-256 digest of the local repo path; links telemetry events to this record
    repository_id: str | None = None


class RepoResponse(BaseModel):
    id: int
    project_id: int
    slug: str
    display_name: str
    repository_id: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

class ApiKeyCreate(BaseModel):
    label: str | None = None
    expires_at: datetime | None = None


class ApiKeyIssued(BaseModel):
    """Returned once at creation. ``raw_key`` is never stored and cannot be recovered."""
    id: int
    org_id: int
    key_prefix: str
    label: str | None
    raw_key: str
    created_at: datetime
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    """Safe list view — no raw key."""
    id: int
    org_id: int
    key_prefix: str
    label: str | None
    revoked: bool
    created_at: datetime
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditEntry(BaseModel):
    id: int
    org_id: int | None
    repository_id: str | None
    run_id: str | None
    task_hash: str | None
    endpoint: str
    policy_version: str | None
    tokens_used: int | None
    tokens_saved: int | None
    violation_count: int
    policy_passed: bool | None
    status_code: int | None
    created_at: datetime


class AuditLogResponse(BaseModel):
    entries: list[AuditEntry]
    total: int


class AuditEntryCreate(BaseModel):
    """Body for ``POST /orgs/{org_id}/audit-log`` — used by the gateway to push entries."""
    endpoint: str
    repository_id: str | None = None
    run_id: str | None = None
    task_hash: str | None = None
    policy_version: str | None = None
    tokens_used: int | None = None
    tokens_saved: int | None = None
    violation_count: int = 0
    policy_passed: bool | None = None
    status_code: int | None = None


# ---------------------------------------------------------------------------
# Policy versions
# ---------------------------------------------------------------------------

class PolicyVersionCreate(BaseModel):
    version: str
    # Fields mirror contextbudget.core.policy.PolicySpec
    spec: dict[str, Any]
    project_id: int | None = None
    repo_id: int | None = None


class PolicyVersionResponse(BaseModel):
    id: int
    org_id: int
    project_id: int | None
    repo_id: int | None
    version: str
    spec: dict[str, Any]
    is_active: bool
    created_at: datetime
    activated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Cost analytics
# ---------------------------------------------------------------------------

class CostSummaryResponse(BaseModel):
    baseline_tokens: int
    optimized_tokens: int
    tokens_saved: int
    savings_rate: float | None
    run_count: int


class CostByRepoRow(BaseModel):
    repository_id: str
    baseline_tokens: int
    optimized_tokens: int
    tokens_saved: int
    savings_rate: float | None
    run_count: int


class CostByRepoResponse(BaseModel):
    repositories: list[CostByRepoRow]


class CostByDateRow(BaseModel):
    date: str
    baseline_tokens: int
    optimized_tokens: int
    tokens_saved: int
    savings_rate: float | None
    run_count: int


class CostByDateResponse(BaseModel):
    days: list[CostByDateRow]


# ---------------------------------------------------------------------------
# Agent runs
# ---------------------------------------------------------------------------

class AgentRunResponse(BaseModel):
    id: int
    repo_id: int
    run_id: str
    task_hash: str | None
    status: str
    tokens_used: int | None
    tokens_saved: int | None
    cache_hits: int | None
    policy_version: str | None
    started_at: datetime
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Cost attribution — by run and by stage
# ---------------------------------------------------------------------------

class CostByRunRow(BaseModel):
    run_id: str
    repository_id: str | None
    command: str | None
    run_at: str | None
    baseline_tokens: int
    optimized_tokens: int
    tokens_saved: int
    savings_rate: float | None
    cache_hits: int
    tokens_saved_by_cache: int


class CostByRunResponse(BaseModel):
    runs: list[CostByRunRow]


class StageDetail(BaseModel):
    tokens_saved: int
    savings_rate: float | None
    description: str


class CostByStageResponse(BaseModel):
    run_count: int
    total_baseline_tokens: int
    total_optimized_tokens: int
    total_tokens_saved: int
    overall_savings_rate: float | None
    stages: dict[str, StageDetail]


# ---------------------------------------------------------------------------
# ROI dashboard
# ---------------------------------------------------------------------------

class ROIRepoRow(BaseModel):
    repository_id: str
    tokens_used: int
    tokens_saved: int
    baseline_tokens: int
    run_count: int
    savings_rate: float | None
    dollars_saved: float


class DashboardROI(BaseModel):
    total_tokens_used: int
    total_tokens_saved: int
    total_baseline_tokens: int
    savings_rate: float | None
    estimated_dollars_saved: float
    cache_hit_rate_pct: float | None
    total_runs: int
    runs_with_cache_hits: int
    price_per_1m_tokens: float
    top_repos: list[ROIRepoRow]
    note: str


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

class WebhookCreate(BaseModel):
    url: str
    secret: str | None = None       # HMAC signing secret; stored hashed
    events: list[str] = []          # e.g. ["policy_violation", "budget_overrun", "drift"]


class WebhookResponse(BaseModel):
    id: int
    org_id: int
    url: str
    events: list[str]
    active: bool
    created_at: datetime
