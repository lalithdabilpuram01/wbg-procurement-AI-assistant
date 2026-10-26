-- One-time, additive bootstrap of the conversation store roles (run explicitly, as a PostgreSQL superuser):
--   psql -d wbg_structured_registry -v ON_ERROR_STOP=1 -f conversation/sql/bootstrap_roles.sql
--
-- The conversation store lives in its own schema (`conversation`) inside the existing wbg_structured_registry
-- database, but is independent of the `registry` schema: no foreign keys or grants between them, and re-initialising
-- the registry (which recreates only the `registry` schema) does not touch it. Nothing is dropped here.
--   wbg_conversation_admin  creates the conversation schema and tables (initialisation only)
--   wbg_conversation_app    runtime role: SELECT/INSERT and column-limited UPDATE; no DELETE, no DDL
-- Roles have no passwords (no secret in code); local authentication follows the server configuration.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wbg_conversation_admin') THEN
        CREATE ROLE wbg_conversation_admin LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wbg_conversation_app') THEN
        CREATE ROLE wbg_conversation_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE wbg_structured_registry TO wbg_conversation_admin, wbg_conversation_app;
GRANT CREATE ON DATABASE wbg_structured_registry TO wbg_conversation_admin;   -- to create its own schema only
