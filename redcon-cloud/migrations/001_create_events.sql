-- ContextBudget Control Plane: events table
-- Schema version: 1

CREATE TABLE IF NOT EXISTS events (
    id                          BIGSERIAL PRIMARY KEY,

    -- Envelope fields (mirrors TelemetryEvent)
    name                        TEXT        NOT NULL,
    schema_version              TEXT        NOT NULL DEFAULT 'v1',
    event_timestamp             TIMESTAMPTZ NOT NULL,
    run_id                      TEXT        NOT NULL,

    -- Top-level payload fields
    command                     TEXT,

    -- Repository identifiers (SHA-256 digests, never raw paths)
    repository_id               TEXT,
    workspace_id                TEXT,

    -- Token estimates
    max_tokens                  BIGINT,
    estimated_input_tokens      BIGINT,
    estimated_saved_tokens      BIGINT,
    baseline_full_context_tokens BIGINT,

    -- File counts
    scanned_files               INT,
    ranked_files                INT,
    included_files              INT,
    skipped_files               INT,
    top_files                   INT,

    -- Cache stats
    cache_hits                  INT,
    tokens_saved_by_cache       BIGINT,
    cache_backend               TEXT,

    -- Policy outcome
    policy_evaluated            BOOLEAN,
    policy_passed               BOOLEAN,
    violation_count             INT,

    -- Full payload for ad-hoc queries
    payload                     JSONB       NOT NULL DEFAULT '{}',

    received_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Lookup indexes
CREATE INDEX IF NOT EXISTS events_name_idx           ON events (name);
CREATE INDEX IF NOT EXISTS events_repository_id_idx  ON events (repository_id);
CREATE INDEX IF NOT EXISTS events_run_id_idx         ON events (run_id);
CREATE INDEX IF NOT EXISTS events_event_timestamp_idx ON events (event_timestamp DESC);
CREATE INDEX IF NOT EXISTS events_command_idx        ON events (command);

-- Composite indexes for aggregation queries
CREATE INDEX IF NOT EXISTS events_name_repository_id_idx ON events (name, repository_id);
CREATE INDEX IF NOT EXISTS events_name_command_idx       ON events (name, command);
