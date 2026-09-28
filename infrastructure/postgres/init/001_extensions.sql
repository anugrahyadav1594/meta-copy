-- 001_extensions.sql
-- Extensions and shared helpers for the MetaScale canonical PostgreSQL.
-- Loaded first by the docker-entrypoint on an empty database.

-- No third-party extensions are required by the canonical schema (plain
-- PostgreSQL 16+). Later members may opt into pgcrypto/pg_trgm on top.

-- Maintain updated_at automatically for rows updated outside the ORM
-- (the SQLAlchemy models also set updated_at via onupdate).
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
