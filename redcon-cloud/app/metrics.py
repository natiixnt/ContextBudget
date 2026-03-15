from __future__ import annotations

"""Prometheus metrics for the Redcon Cloud service.

All counters and gauges are registered once at import time.  The
``/metrics`` endpoint (added in main.py) returns the Prometheus text
exposition format consumed by any standard scraper.

Usage
-----
    from app.metrics import (
        EVENTS_INGESTED,
        EVENTS_REJECTED,
        REQUESTS_TOTAL,
        TOKENS_INGESTED,
        TOKENS_SAVED,
        ACTIVE_API_KEYS,
    )
    EVENTS_INGESTED.labels(org_id="42").inc(len(events))
"""

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

__all__ = [
    "REGISTRY",
    "CONTENT_TYPE_LATEST",
    "generate_latest",
    "EVENTS_INGESTED",
    "EVENTS_REJECTED",
    "REQUESTS_TOTAL",
    "REQUEST_LATENCY",
    "TOKENS_INGESTED",
    "TOKENS_SAVED",
    "ACTIVE_ORG_COUNT",
    "RATE_LIMITED_REQUESTS",
    "QUOTA_EXCEEDED_REQUESTS",
    "OIDC_AUTH_SUCCESS",
    "OIDC_AUTH_FAILURE",
]

EVENTS_INGESTED: Counter = Counter(
    "rc_events_ingested_total",
    "Total number of telemetry events accepted",
    ["org_id"],
)

EVENTS_REJECTED: Counter = Counter(
    "rc_events_rejected_total",
    "Total number of events rejected (validation error or rate limit)",
    ["reason"],  # 'validation' | 'rate_limit' | 'quota_exceeded'
)

REQUESTS_TOTAL: Counter = Counter(
    "rc_http_requests_total",
    "Total HTTP requests handled",
    ["method", "endpoint", "status_code"],
)

REQUEST_LATENCY: Histogram = Histogram(
    "rc_http_request_duration_seconds",
    "HTTP request processing time in seconds",
    ["endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

TOKENS_INGESTED: Counter = Counter(
    "rc_tokens_ingested_total",
    "Total estimated_input_tokens received across all events",
    ["org_id"],
)

TOKENS_SAVED: Counter = Counter(
    "rc_tokens_saved_total",
    "Total estimated_saved_tokens received across all events",
    ["org_id"],
)

ACTIVE_ORG_COUNT: Gauge = Gauge(
    "rc_active_orgs",
    "Number of organisations currently registered",
)

RATE_LIMITED_REQUESTS: Counter = Counter(
    "rc_rate_limited_requests_total",
    "Requests rejected due to per-API-key rate limiting",
    ["org_id"],
)

QUOTA_EXCEEDED_REQUESTS: Counter = Counter(
    "rc_quota_exceeded_requests_total",
    "Event ingestion requests rejected because the org token quota was exceeded",
    ["org_id"],
)

OIDC_AUTH_SUCCESS: Counter = Counter(
    "rc_oidc_auth_success_total",
    "Successful OIDC token verifications",
)

OIDC_AUTH_FAILURE: Counter = Counter(
    "rc_oidc_auth_failure_total",
    "Failed OIDC token verifications",
    ["reason"],
)
