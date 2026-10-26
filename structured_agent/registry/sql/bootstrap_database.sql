-- One-time bootstrap of the dedicated registry database (run explicitly, as a PostgreSQL superuser):
--   psql -d postgres -v ON_ERROR_STOP=1 -f structured_agent/registry/sql/bootstrap_database.sql
--
-- Creates two LOGIN roles without passwords (no password is stored in code; the local server uses its own
-- authentication configuration, and a password, if later required, comes from PGPASSWORD or ~/.pgpass):
--   wbg_registry_admin   owns the database and loads the registry (initialisation only)
--   wbg_registry_reader  read-only role used by the agent at runtime
-- No other database on the server is touched.

CREATE ROLE wbg_registry_admin LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE wbg_registry_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;

CREATE DATABASE wbg_structured_registry OWNER wbg_registry_admin;

REVOKE ALL ON DATABASE wbg_structured_registry FROM PUBLIC;
GRANT CONNECT ON DATABASE wbg_structured_registry TO wbg_registry_admin, wbg_registry_reader;
