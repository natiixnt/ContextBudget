-- Create the replication user on the primary database.
-- This script runs automatically via docker-entrypoint-initdb.d.
--
-- POSTGRES_REPLICATOR_PWD must be set in .env; it is substituted
-- by the entrypoint using `envsubst` before execution.
-- If the variable is not set, the user will have no password (unsafe).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replicator') THEN
    CREATE USER replicator REPLICATION LOGIN PASSWORD current_setting('app.replicator_pwd', true);
  END IF;
END
$$;

-- Allow the replicator to connect from the replica container
-- (pg_hba.conf addition — handled at container level via POSTGRESQL_HBA_ENTRY
--  in the bitnami image, or manually in pg_hba.conf for vanilla postgres)
