from __future__ import annotations

"""Stripe billing meter integration for Redcon Cloud.

Architecture
------------
Redcon uses Stripe Billing Meters (usage-based billing) to report
token consumption per org.  Each org maps to a Stripe Customer; usage is
reported via the Stripe Meters API after each batch of events is ingested.

Configuration (environment variables)
--------------------------------------
RC_CLOUD_STRIPE_SECRET_KEY      Stripe secret API key (sk_live_... / sk_test_...)
RC_CLOUD_STRIPE_METER_ID        Stripe Meter ID for token consumption events
                                  (create with: stripe meters create --event-name cb_tokens_used)
RC_CLOUD_BILLING_ENABLED        Set to "true" to enable billing (default: false)

Database schema
---------------
The ``006_billing.sql`` migration adds:
  - ``orgs.stripe_customer_id``  — Stripe Customer ID for the org
  - ``billing_events`` table     — audit log of reported meter events

Usage
-----
    from app.billing import report_token_usage

    # Called after successful event ingestion:
    await report_token_usage(pool, org_id=42, tokens=12_500)
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

BILLING_ENABLED: bool = os.getenv("RC_CLOUD_BILLING_ENABLED", "false").lower() == "true"
_STRIPE_SECRET_KEY: str = os.getenv("RC_CLOUD_STRIPE_SECRET_KEY", "")
_METER_ID: str = os.getenv("RC_CLOUD_STRIPE_METER_ID", "")


def _get_stripe():
    """Lazy-import stripe so the package is optional at runtime."""
    try:
        import stripe  # type: ignore
        stripe.api_key = _STRIPE_SECRET_KEY
        return stripe
    except ImportError:
        logger.warning("stripe package not installed; billing is disabled")
        return None


async def get_or_create_stripe_customer(pool: Any, org_id: int, org_slug: str) -> str | None:
    """Return the Stripe Customer ID for *org_id*, creating one if needed.

    The customer ID is cached in ``orgs.stripe_customer_id``.
    """
    if not BILLING_ENABLED or not _STRIPE_SECRET_KEY:
        return None

    stripe = _get_stripe()
    if stripe is None:
        return None

    # Look up cached customer ID
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stripe_customer_id FROM orgs WHERE id = $1",
            org_id,
        )
        if row and row["stripe_customer_id"]:
            return row["stripe_customer_id"]

    # Create a new Stripe customer
    try:
        customer = stripe.Customer.create(
            metadata={"rc_org_id": str(org_id), "rc_org_slug": org_slug},
            description=f"Redcon org: {org_slug}",
        )
        customer_id: str = customer["id"]
    except Exception as exc:
        logger.warning("Stripe customer creation failed for org %s: %s", org_id, exc)
        return None

    # Cache in the database
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE orgs SET stripe_customer_id = $1 WHERE id = $2",
            customer_id,
            org_id,
        )

    return customer_id


async def report_token_usage(pool: Any, org_id: int, tokens: int) -> bool:
    """Report *tokens* consumed by *org_id* to the Stripe Meter.

    This is a best-effort, fire-and-forget call.  Returns True on success.
    """
    if not BILLING_ENABLED or not _STRIPE_SECRET_KEY or not _METER_ID:
        return False

    stripe = _get_stripe()
    if stripe is None:
        return False

    # Get org details for customer lookup
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, slug, stripe_customer_id FROM orgs WHERE id = $1",
            org_id,
        )
    if row is None:
        return False

    customer_id = row["stripe_customer_id"]
    if not customer_id:
        customer_id = await get_or_create_stripe_customer(pool, org_id, row["slug"])
    if not customer_id:
        return False

    try:
        import time
        stripe.billing.MeterEvent.create(
            event_name="rc_tokens_used",
            payload={
                "stripe_customer_id": customer_id,
                "value": str(tokens),
            },
            timestamp=int(time.time()),
        )

        # Write to audit table
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO billing_events (org_id, stripe_customer_id, meter_id, tokens_reported)
                VALUES ($1, $2, $3, $4)
                """,
                org_id,
                customer_id,
                _METER_ID,
                tokens,
            )

        return True
    except Exception as exc:
        logger.warning("Stripe meter event failed for org %s: %s", org_id, exc)
        return False


async def get_billing_summary(pool: Any, org_id: int) -> dict:
    """Return a summary of reported billing events for *org_id*."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::int           AS event_count,
                COALESCE(SUM(tokens_reported), 0)::bigint AS total_tokens_reported,
                MIN(created_at)         AS first_reported_at,
                MAX(created_at)         AS last_reported_at
            FROM billing_events
            WHERE org_id = $1
            """,
            org_id,
        )
        customer = await conn.fetchval(
            "SELECT stripe_customer_id FROM orgs WHERE id = $1", org_id
        )
    return {
        "org_id": org_id,
        "stripe_customer_id": customer,
        "billing_events": row["event_count"],
        "total_tokens_reported": row["total_tokens_reported"],
        "first_reported_at": row["first_reported_at"].isoformat() if row["first_reported_at"] else None,
        "last_reported_at": row["last_reported_at"].isoformat() if row["last_reported_at"] else None,
    }
