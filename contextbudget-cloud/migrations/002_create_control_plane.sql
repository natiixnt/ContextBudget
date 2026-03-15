-- ContextBudget Control Plane: multi-tenant org/project/repo model
-- Schema version: 2

-- ---------------------------------------------------------------------------
-- Organizations: top-level tenant
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orgs (
    id           BIGSERIAL    PRIMARY KEY,
    slug         TEXT         NOT NULL UNIQUE,
    display_name TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Projects: named scope within an org
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id           BIGSERIAL    PRIMARY KEY,
    org_id       BIGINT       NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    slug         TEXT         NOT NULL,
    display_name TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, slug)
);

-- ---------------------------------------------------------------------------
-- Repositories: tracked repo within a project
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repositories (
    id            BIGSERIAL   PRIMARY KEY,
    project_id    BIGINT      NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug          TEXT        NOT NULL,
    display_name  TEXT        NOT NULL,
    -- SHA-256 digest of the local repo path; joins to events.repository_id
    repository_id TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, slug)
);

-- ---------------------------------------------------------------------------
-- Agent runs: per-execution outcome record
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_runs (
    id             BIGSERIAL   PRIMARY KEY,
    repo_id        BIGINT      NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    run_id         TEXT        NOT NULL UNIQUE,
    -- SHA-256 of raw task text; no plaintext task stored
    task_hash      TEXT,
    status         TEXT        NOT NULL DEFAULT 'unknown',
    tokens_used    BIGINT,
    tokens_saved   BIGINT,
    cache_hits     INT,
    policy_version TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- API keys: hashed credentials tied to an org
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    id         BIGSERIAL   PRIMARY KEY,
    org_id     BIGINT      NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    -- SHA-256 of the raw key; raw key is never stored
    key_hash   TEXT        NOT NULL UNIQUE,
    -- First 12 chars of raw key for display ("cbk_xxxxxxxx")
    key_prefix TEXT        NOT NULL,
    label      TEXT,
    revoked    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Audit log: append-only record of gateway activity and management ops
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL   PRIMARY KEY,
    org_id          BIGINT      REFERENCES orgs(id),
    -- SHA-256 digest from event payload (never raw path)
    repository_id   TEXT,
    run_id          TEXT,
    task_hash       TEXT,
    endpoint        TEXT        NOT NULL,
    policy_version  TEXT,
    tokens_used     BIGINT,
    tokens_saved    BIGINT,
    violation_count INT         NOT NULL DEFAULT 0,
    policy_passed   BOOLEAN,
    status_code     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Policy versions: versioned PolicySpec per org / project / repo scope
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_versions (
    id           BIGSERIAL   PRIMARY KEY,
    org_id       BIGINT      NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    project_id   BIGINT      REFERENCES projects(id) ON DELETE CASCADE,
    repo_id      BIGINT      REFERENCES repositories(id) ON DELETE CASCADE,
    version      TEXT        NOT NULL,
    -- Full PolicySpec stored as JSON; fields mirror contextbudget.core.policy.PolicySpec
    spec         JSONB       NOT NULL,
    is_active    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS api_keys_org_id_idx
    ON api_keys (org_id);

CREATE INDEX IF NOT EXISTS audit_log_org_id_idx
    ON audit_log (org_id);
CREATE INDEX IF NOT EXISTS audit_log_created_at_idx
    ON audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_repository_id_idx
    ON audit_log (repository_id);

CREATE INDEX IF NOT EXISTS policy_versions_org_id_idx
    ON policy_versions (org_id);
-- Partial index: fast lookup for the active policy at each org+scope
CREATE INDEX IF NOT EXISTS policy_versions_active_idx
    ON policy_versions (org_id, repo_id, project_id)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS agent_runs_repo_id_idx
    ON agent_runs (repo_id);
CREATE INDEX IF NOT EXISTS agent_runs_started_at_idx
    ON agent_runs (started_at DESC);
