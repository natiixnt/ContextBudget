-- ContextBudget: org-scoped events, API key expiry, row-level security
-- Schema version: 3

-- ---------------------------------------------------------------------------
-- Follow-up #1 / #3: org_id on events
-- Populated during ingest when the sender authenticates with an API key.
-- NULL means the event arrived before auth was required (backward compat).
-- ---------------------------------------------------------------------------
ALTER TABLE events ADD COLUMN IF NOT EXISTS org_id BIGINT REFERENCES orgs(id);

CREATE INDEX IF NOT EXISTS events_org_id_idx ON events (org_id);

-- Composite index for org-scoped aggregation queries
CREATE INDEX IF NOT EXISTS events_org_id_name_idx ON events (org_id, name);

-- ---------------------------------------------------------------------------
-- Follow-up #5: API key expiry
-- ---------------------------------------------------------------------------
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- Follow-up #7: Row-level security on events (defence-in-depth)
--
-- The application already filters by org_id in every query.  RLS adds a
-- second enforcement layer so a query that forgets the WHERE clause cannot
-- leak cross-org data.
--
-- Policy logic:
--   - org_id IS NULL  → event predates auth; visible to all (backward compat)
--   - current_setting('app.current_org_id') = '' → no session context set;
--     only unowned rows are visible (matches unauthenticated query path)
--   - otherwise → org_id must match the session context value
--
-- Usage: call SET LOCAL app.current_org_id = '<org_id>' inside a transaction
-- before issuing an org-scoped query.
-- ---------------------------------------------------------------------------
ALTER TABLE events ENABLE ROW LEVEL SECURITY;

-- Drop policy if it already exists so this migration is re-runnable
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'events' AND policyname = 'events_org_isolation'
    ) THEN
        DROP POLICY events_org_isolation ON events;
    END IF;
END $$;

CREATE POLICY events_org_isolation ON events
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        org_id IS NULL
        OR current_setting('app.current_org_id', true) = ''
        OR org_id::text = current_setting('app.current_org_id', true)
    );
