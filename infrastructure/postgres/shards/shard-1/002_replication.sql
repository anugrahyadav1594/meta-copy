-- Replication role for the `replication` compose profile.
-- The bitnami/postgresql standbys connect as `repl` and run pg_basebackup
-- on first boot. Nothing in the application depends on this file: without
-- the profile the role is simply never used.
DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'repl') THEN
      CREATE ROLE repl WITH REPLICATION LOGIN PASSWORD 'replpassword';
   END IF;
END
$$;
