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
