-- Migration 004: webhook registrations
-- Stores per-org webhook endpoints for push notifications.
-- event_type filters: "policy_violation", "budget_overrun", "drift", "cache_miss_spike"
-- Empty events array means "subscribe to all events".

CREATE TABLE IF NOT EXISTS webhooks (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    url         TEXT   NOT NULL,
    secret_hash TEXT,               -- SHA-256 of the signing secret; NULL = unsigned
    events      JSONB  NOT NULL DEFAULT '[]'::jsonb,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS webhooks_org_id_idx    ON webhooks (org_id);
CREATE INDEX IF NOT EXISTS webhooks_org_active_idx ON webhooks (org_id, active);
