#!/usr/bin/env python3
"""Generate a 1000-task benchmark corpus and write it to tasks-1000.toml.

Run from the repo root:
    python redcon-benchmarks/corpus/generate_tasks.py
"""

from __future__ import annotations

import itertools
import random
from pathlib import Path

random.seed(42)  # reproducible ordering

# ---------------------------------------------------------------------------
# Task templates by category.  Each template is a str.format()-compatible
# string; slots are filled from the domain vocabulary below.
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, list[str]] = {
    "auth": [
        "Add {auth_method} authentication to the {layer} layer",
        "Implement {auth_method} token validation middleware",
        "Add role-based access control to {api_entity} endpoints",
        "Replace session cookies with {auth_method} tokens",
        "Add API key authentication to public {api_entity} routes",
        "Implement OAuth2 {oauth_flow} flow for user login",
        "Add {auth_method} token refresh logic to auth service",
        "Enforce permission checks before {api_entity} mutations",
        "Add multi-factor authentication support to login flow",
        "Audit authentication middleware for timing-safe comparison",
        "Add {auth_method} token revocation endpoint",
        "Implement scoped API keys for {api_entity} access",
    ],
    "caching": [
        "Add {cache_backend} caching to {api_entity} lookup endpoints",
        "Implement cache invalidation for {api_entity} mutations",
        "Add {ttl_expr} TTL to {cache_backend} cache entries",
        "Cache {api_entity} query results to reduce database load",
        "Add HTTP cache headers to {api_entity} GET responses",
        "Implement write-through {cache_backend} cache for {api_entity}",
        "Add cache warming on application startup for {api_entity}",
        "Replace in-memory dict with {cache_backend} for session storage",
        "Add distributed {cache_backend} cache for horizontal scaling",
        "Implement LRU eviction for in-memory {api_entity} cache",
        "Add cache hit/miss metrics to {api_entity} service",
        "Cache {api_entity} count queries with {ttl_expr} expiry",
    ],
    "database": [
        "Add database connection pooling to {db_layer}",
        "Refactor {db_layer} to use repository pattern",
        "Add pagination to {api_entity} list queries",
        "Optimize {api_entity} query by adding database index",
        "Replace raw SQL with ORM queries in {db_layer}",
        "Add database transaction support to {api_entity} creation",
        "Implement soft delete for {api_entity} records",
        "Add created_at and updated_at timestamps to {api_entity} model",
        "Refactor {db_layer} to support read replicas",
        "Add database migration for {api_entity} schema change",
        "Implement bulk {api_entity} insert with conflict handling",
        "Add full-text search index to {api_entity} table",
    ],
    "api": [
        "Add {http_method} endpoint for {api_entity} {crud_action}",
        "Implement cursor-based pagination for {api_entity} list",
        "Add request validation to {api_entity} {http_method} endpoint",
        "Return {response_format} response from {api_entity} endpoint",
        "Add API versioning to {api_entity} routes",
        "Implement bulk {crud_action} endpoint for {api_entity}",
        "Add filtering and sorting to {api_entity} list endpoint",
        "Implement webhooks for {api_entity} {crud_action} events",
        "Add GraphQL resolver for {api_entity}",
        "Add idempotency key support to {api_entity} creation endpoint",
        "Implement {api_entity} export endpoint returning CSV",
        "Add ETags to {api_entity} GET responses for conditional requests",
    ],
    "error_handling": [
        "Add structured error responses to {api_entity} endpoints",
        "Implement retry logic with exponential backoff in {layer} layer",
        "Add circuit breaker to {api_entity} external service calls",
        "Handle {error_type} errors gracefully in {api_entity} handler",
        "Add global exception handler for unhandled {error_type} errors",
        "Implement dead letter queue for failed {api_entity} events",
        "Add timeout handling to {api_entity} {layer} calls",
        "Return 409 Conflict when {api_entity} already exists",
        "Propagate request ID through {api_entity} error responses",
        "Add fallback response when {api_entity} service is unavailable",
    ],
    "testing": [
        "Add unit tests for {api_entity} {layer} layer",
        "Write integration tests for {api_entity} {http_method} endpoint",
        "Add test fixtures for {api_entity} database records",
        "Mock {cache_backend} in {api_entity} service tests",
        "Add property-based tests for {api_entity} validation",
        "Add contract tests between {api_entity} service and client",
        "Increase test coverage for {api_entity} error paths",
        "Add load test for {api_entity} list endpoint",
        "Write e2e tests for {api_entity} {crud_action} flow",
        "Add snapshot tests for {api_entity} {response_format} responses",
        "Parameterize {api_entity} tests across {http_method} methods",
        "Add mutation testing to {api_entity} validator tests",
    ],
    "security": [
        "Add rate limiting to {api_entity} {http_method} endpoints",
        "Sanitize {api_entity} input to prevent SQL injection",
        "Add CORS policy to {api_entity} routes",
        "Implement request signing for {api_entity} webhooks",
        "Add audit log for {api_entity} {crud_action} operations",
        "Enforce HTTPS-only access to {api_entity} endpoints",
        "Add CSP headers to {api_entity} responses",
        "Implement SSRF protection in {api_entity} URL fetching",
        "Add IP allowlist to {api_entity} admin endpoints",
        "Scan {api_entity} dependencies for known vulnerabilities",
        "Add secrets rotation support to {api_entity} configuration",
        "Mask PII fields in {api_entity} logs and error messages",
    ],
    "performance": [
        "Profile and optimize {api_entity} {layer} hot path",
        "Add async processing for {api_entity} heavy operations",
        "Implement connection pooling in {api_entity} {db_layer}",
        "Add background job for {api_entity} report generation",
        "Optimize {api_entity} serialization with {response_format}",
        "Reduce {api_entity} query count with eager loading",
        "Add streaming response to {api_entity} export endpoint",
        "Parallelize {api_entity} external service calls",
        "Compress {api_entity} {response_format} responses with gzip",
        "Add request coalescing for concurrent {api_entity} lookups",
        "Precompute {api_entity} aggregates via materialized view",
        "Reduce {api_entity} startup time by lazy-loading config",
    ],
    "logging": [
        "Add structured JSON logging to {api_entity} {layer} layer",
        "Add request/response logging middleware for {api_entity} routes",
        "Include trace ID in all {api_entity} log messages",
        "Add {api_entity} performance metrics to Prometheus exporter",
        "Log slow {api_entity} queries above {ttl_expr} threshold",
        "Add health check endpoint with {api_entity} dependency status",
        "Emit OpenTelemetry spans for {api_entity} {layer} calls",
        "Add structured {error_type} error events to {api_entity} logs",
        "Correlate {api_entity} logs with upstream request ID",
        "Add dashboard for {api_entity} error rates and latency",
    ],
    "refactoring": [
        "Extract {api_entity} validation into dedicated validator class",
        "Split {api_entity} {layer} module into smaller focused units",
        "Replace {api_entity} global state with dependency injection",
        "Rename {api_entity} service methods to follow naming convention",
        "Convert {api_entity} callback pattern to async/await",
        "Move {api_entity} configuration to environment variables",
        "Consolidate duplicate {api_entity} {layer} code into shared utility",
        "Introduce {api_entity} factory method for object construction",
        "Replace magic numbers in {api_entity} with named constants",
        "Add type annotations to {api_entity} {layer} functions",
        "Apply strategy pattern to {api_entity} {layer} implementation",
        "Introduce value objects for {api_entity} domain concepts",
    ],
    "deployment": [
        "Add Dockerfile for {api_entity} service",
        "Add {api_entity} service to docker-compose stack",
        "Create Kubernetes deployment manifest for {api_entity}",
        "Add readiness and liveness probes to {api_entity} deployment",
        "Configure auto-scaling for {api_entity} based on CPU usage",
        "Add Helm chart for {api_entity} microservice",
        "Add CI pipeline step for {api_entity} integration tests",
        "Add {api_entity} blue-green deployment configuration",
        "Configure {api_entity} secrets via Kubernetes secrets manager",
        "Add rollback strategy to {api_entity} deployment pipeline",
    ],
    "docs": [
        "Add OpenAPI schema for {api_entity} {http_method} endpoint",
        "Write {api_entity} architecture decision record",
        "Add inline docstrings to {api_entity} {layer} public functions",
        "Document {api_entity} rate limits in API reference",
        "Add changelog entry for {api_entity} breaking change",
        "Write {api_entity} integration guide for external consumers",
        "Document {api_entity} error codes and remediation steps",
        "Add example {response_format} payloads to {api_entity} docs",
        "Write runbook for {api_entity} on-call incidents",
        "Add {api_entity} sequence diagram to architecture docs",
    ],
    "search": [
        "Add full-text search to {api_entity} list endpoint",
        "Implement {api_entity} search index with Elasticsearch",
        "Add fuzzy matching to {api_entity} name search",
        "Implement faceted search for {api_entity} filtering",
        "Add typeahead suggestions to {api_entity} search endpoint",
        "Cache {api_entity} search results in {cache_backend}",
        "Add search relevance scoring to {api_entity} results",
        "Implement {api_entity} search analytics for query tracking",
    ],
    "events": [
        "Publish {api_entity} {crud_action} event to message queue",
        "Consume {api_entity} events from Kafka topic",
        "Add idempotent event handler for {api_entity} creation",
        "Implement outbox pattern for {api_entity} event delivery",
        "Add dead letter queue for failed {api_entity} events",
        "Implement {api_entity} event sourcing with append-only log",
        "Add event replay capability for {api_entity} projections",
        "Correlate {api_entity} events with distributed trace ID",
    ],
    "multitenancy": [
        "Add tenant isolation to {api_entity} data layer",
        "Implement per-tenant {cache_backend} namespace for {api_entity}",
        "Add tenant ID to all {api_entity} audit log entries",
        "Enforce row-level security for {api_entity} tenant data",
        "Add tenant-scoped rate limiting to {api_entity} endpoints",
        "Implement {api_entity} data export per tenant",
    ],
}

# ---------------------------------------------------------------------------
# Domain vocabulary
# ---------------------------------------------------------------------------

VOCAB: dict[str, list[str]] = {
    "auth_method": [
        "JWT", "OAuth2", "API key", "mTLS", "PASETO", "session-based",
        "magic link", "SAML", "OIDC",
    ],
    "oauth_flow": ["authorization code", "client credentials", "device code", "implicit"],
    "cache_backend": ["Redis", "Memcached", "in-memory LRU", "CDN", "Varnish"],
    "ttl_expr": ["60-second", "5-minute", "1-hour", "24-hour", "sliding-window"],
    "api_entity": [
        "user", "task", "project", "order", "product", "comment", "notification",
        "document", "invoice", "payment", "subscription", "team", "workspace",
        "file", "audit", "session", "token", "report", "webhook", "integration",
        "pipeline", "deployment", "build", "artifact", "release", "environment",
        "secret", "policy", "role", "permission", "alert", "metric", "log",
        "event", "message", "channel", "thread", "attachment", "tag", "label",
        "category", "review", "approval", "workflow",
    ],
    "layer": ["service", "repository", "handler", "middleware", "controller", "gateway"],
    "db_layer": ["repository", "data access", "ORM", "query builder"],
    "http_method": ["GET", "POST", "PUT", "PATCH", "DELETE"],
    "crud_action": ["create", "update", "delete", "archive", "restore", "publish"],
    "response_format": ["JSON", "Protobuf", "MessagePack", "JSON:API", "HAL"],
    "error_type": [
        "timeout", "connection", "validation", "authorization", "not-found",
        "rate-limit", "conflict",
    ],
}


def _fill(template: str) -> str:
    """Fill all {slot} placeholders with random vocabulary values."""
    result = template
    for slot, values in VOCAB.items():
        placeholder = "{" + slot + "}"
        if placeholder in result:
            result = result.replace(placeholder, random.choice(values))
    return result


def generate_tasks(target: int = 1000) -> list[dict]:
    """Generate *target* unique task entries."""
    tasks: list[dict] = []
    seen: set[str] = set()

    # First pass: one full round-robin across all categories
    all_templates = [
        (category, tmpl)
        for category, templates in TEMPLATES.items()
        for tmpl in templates
    ]
    random.shuffle(all_templates)

    attempts = 0
    while len(tasks) < target and attempts < target * 20:
        attempts += 1
        category, tmpl = random.choice(all_templates)
        text = _fill(tmpl)
        if text in seen:
            continue
        seen.add(text)
        tasks.append({"id": len(tasks) + 1, "category": category, "task": text})

    return tasks[:target]


def write_toml(tasks: list[dict], path: Path) -> None:
    lines = ["# Redcon benchmark corpus — 1000 coding tasks", "# generated by generate_tasks.py", ""]
    for entry in tasks:
        lines.append("[[tasks]]")
        lines.append(f'id = {entry["id"]}')
        lines.append(f'category = "{entry["category"]}"')
        # Escape double quotes inside task string
        task_escaped = entry["task"].replace('"', '\\"')
        lines.append(f'task = "{task_escaped}"')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(tasks)} tasks to {path}")


def main() -> None:
    out = Path(__file__).parent / "tasks-1000.toml"
    tasks = generate_tasks(1000)
    write_toml(tasks, out)

    # Print category distribution
    from collections import Counter
    counts = Counter(t["category"] for t in tasks)
    print("\nCategory distribution:")
    for cat, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:20s} {count:4d} tasks")


if __name__ == "__main__":
    main()
