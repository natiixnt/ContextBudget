from __future__ import annotations

import os

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://contextbudget:contextbudget@db:5432/contextbudget",
)
# Optional read replica URL for analytics queries.
# When set, dashboard and cost-analytics endpoints route reads to this pool.
# Falls back to DATABASE_URL when unset.
READ_DATABASE_URL: str = os.getenv("READ_DATABASE_URL", "")
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8080"))

# ── Security ──────────────────────────────────────────────────────────────────
# Required to call POST /orgs (org bootstrap endpoint).
# Set CB_CLOUD_ADMIN_TOKEN to a strong random secret in production.
# When empty, POST /orgs returns 403.
ADMIN_TOKEN: str = os.getenv("CB_CLOUD_ADMIN_TOKEN", "")

# ── OIDC ──────────────────────────────────────────────────────────────────────
OIDC_ENABLED: bool = os.getenv("CB_CLOUD_OIDC_ENABLED", "false").lower() == "true"
OIDC_ISSUER: str = os.getenv("CB_CLOUD_OIDC_ISSUER", "")
OIDC_AUDIENCE: str = os.getenv("CB_CLOUD_OIDC_AUDIENCE", "")
OIDC_JWKS_URI: str = os.getenv("CB_CLOUD_OIDC_JWKS_URI", "")

# ── Billing ───────────────────────────────────────────────────────────────────
BILLING_ENABLED: bool = os.getenv("CB_CLOUD_BILLING_ENABLED", "false").lower() == "true"
STRIPE_SECRET_KEY: str = os.getenv("CB_CLOUD_STRIPE_SECRET_KEY", "")
STRIPE_METER_ID: str = os.getenv("CB_CLOUD_STRIPE_METER_ID", "")

# ── Platform webhook adapters ─────────────────────────────────────────────────
SLACK_WEBHOOK_URL: str = os.getenv("CB_CLOUD_SLACK_WEBHOOK_URL", "")
PAGERDUTY_ROUTING_KEY: str = os.getenv("CB_CLOUD_PAGERDUTY_ROUTING_KEY", "")

# ── Rate limiting ─────────────────────────────────────────────────────────────
EVENTS_RATE_LIMIT: int = int(os.getenv("CB_CLOUD_EVENTS_RATE_LIMIT", "500"))
EVENTS_RATE_WINDOW: int = int(os.getenv("CB_CLOUD_EVENTS_RATE_WINDOW", "60"))
