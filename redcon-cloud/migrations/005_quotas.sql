-- Migration 005: per-org usage quotas
-- Adds an org_quotas table for token/event allowances.

CREATE TABLE IF NOT EXISTS org_quotas (
    org_id                   BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    token_allowance_monthly  BIGINT,          -- NULL = unlimited
    event_allowance_monthly  BIGINT,          -- NULL = unlimited
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE org_quotas IS
    'Per-org monthly token and event allowances. NULL = unlimited.';
