-- Migration 006: Stripe billing support
-- Adds stripe_customer_id to orgs and a billing_events audit table.

ALTER TABLE orgs
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;

CREATE TABLE IF NOT EXISTS billing_events (
    id                   BIGSERIAL PRIMARY KEY,
    org_id               BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    stripe_customer_id   TEXT NOT NULL,
    meter_id             TEXT NOT NULL,
    tokens_reported      BIGINT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS billing_events_org_created
    ON billing_events (org_id, created_at DESC);

COMMENT ON TABLE billing_events IS
    'Audit log of token usage events reported to the Stripe Billing Meter.';
