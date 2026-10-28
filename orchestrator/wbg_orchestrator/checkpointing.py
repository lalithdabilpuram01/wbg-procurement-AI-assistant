"""Lakebase (PostgreSQL) checkpointing with the official LangGraph `PostgresSaver`, confined to one isolated schema.

Isolation
  The saver issues UNQUALIFIED SQL (`CREATE TABLE IF NOT EXISTS checkpoints ...`, `CREATE INDEX CONCURRENTLY ...`,
  `SELECT ... FROM checkpoints`). Every pooled connection therefore pins `search_path` to exactly the approved schema.
  PostgreSQL creates unqualified objects in the first schema of the path and resolves names only through the path
  (plus pg_catalog). Before `setup()` runs, `verify_isolation` proves on a pooled connection that:
    * the effective path is exactly [schema];
    * the schema exists and the role may use and create in it;
    * the schema holds only checkpointer objects.
  `isolated_setup` snapshots every other schema before and after `setup()` and fails if anything outside the schema
  changed. Schema creation is a separate, explicit step (`create_schema`); nothing is ever dropped.

Connections
  A dedicated pool (`checkpoint_pool`), separate from any other application database connection:
    * autocommit, dict rows and no prepared statements, as the saver requires;
    * TLS required;
    * bounded size and wait;
    * per-session statement, lock and idle-transaction timeouts;
    * `max_lifetime` below the OAuth credential lifetime.
  Each new physical connection takes a credential from a `TokenProvider`, which refreshes before expiry and reuses
  the existing CLI-based Lakebase credential generator. Tokens never appear in a DSN, log, span, exception or file.

Deserialization
  `JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=None)` is LangGraph's strict mode: only the safe
  built-in types (including LangGraph's Interrupt and Command) can be revived. Graph state itself is plain JSON.

Branch policy
  Checkpoint writes on the `production` branch are refused; a development branch must be named explicitly.
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

CHECKPOINT_TABLES = frozenset({"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"})
CHECKPOINT_OBJECTS = CHECKPOINT_TABLES | {f"{t}_pkey" for t in CHECKPOINT_TABLES} | {
    "checkpoints_thread_id_idx", "checkpoint_blobs_thread_id_idx", "checkpoint_writes_thread_id_idx"}
SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
RESERVED_SCHEMAS = frozenset({"public", "information_schema"})
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")
PROTECTED_BRANCHES = frozenset({"production", "main"})
APPLICATION_NAME = "wbg-orchestrator-checkpoints"
ENV_PREFIX = "WBG_CKPT_"


class CheckpointConfigError(RuntimeError):
    """Checkpoint settings are missing, unsafe or point at a protected branch (messages never contain values)."""


class IsolationError(RuntimeError):
    """The checkpointer could reach or change objects outside its approved schema; setup is refused."""


# -------------------------------------------------------------------------------------------------- settings
@dataclass(frozen=True)
class CheckpointDbSettings:
    host: str = field(repr=False)
    database: str
    user: str = field(repr=False)
    endpoint: str                                # projects/<project>/branches/<branch>/endpoints/<endpoint>
    schema: str = "wbg_orchestrator_ckpt"
    databricks_profile: str = "wbg-databricks-free"
    port: int = 5432
    sslmode: str = "require"
    pool_max_size: int = 2
    pool_wait_seconds: float = 30.0
    connect_timeout_seconds: int = 30            # allows for a scale-to-zero cold start
    statement_timeout_ms: int = 15_000
    max_lifetime_seconds: float = 40 * 60        # OAuth credentials live 60 minutes

    @property
    def branch(self) -> str:
        return branch_of(self.endpoint)

    def connect_kwargs(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "dbname": self.database, "user": self.user, "sslmode": self.sslmode,
                "connect_timeout": self.connect_timeout_seconds, "application_name": APPLICATION_NAME}

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "CheckpointDbSettings":
        """WBG_CKPT_HOST / _DATABASE / _USER / _ENDPOINT (required), _SCHEMA / _PROFILE / _SSLMODE (optional)."""
        env = environ if environ is not None else os.environ
        missing = [k for k in ("HOST", "DATABASE", "USER", "ENDPOINT") if not env.get(ENV_PREFIX + k)]
        if missing:
            raise CheckpointConfigError("missing checkpoint settings: " + ", ".join(ENV_PREFIX + k for k in missing))
        settings = cls(host=env[ENV_PREFIX + "HOST"], database=env[ENV_PREFIX + "DATABASE"], user=env[ENV_PREFIX + "USER"],
                       endpoint=env[ENV_PREFIX + "ENDPOINT"], schema=env.get(ENV_PREFIX + "SCHEMA", cls.schema),
                       databricks_profile=env.get(ENV_PREFIX + "PROFILE", cls.databricks_profile),
                       sslmode=env.get(ENV_PREFIX + "SSLMODE", "require"))
        validate_settings(settings)
        return settings


def branch_of(endpoint: str) -> str:
    match = re.fullmatch(r"projects/[^/]+/branches/([^/]+)/endpoints/[^/]+", endpoint)
    if not match:
        raise CheckpointConfigError("the endpoint must be projects/<p>/branches/<b>/endpoints/<e>")
    return match.group(1)


def validate_schema_name(schema: str) -> str:
    if not SCHEMA_NAME.match(schema) or schema in RESERVED_SCHEMAS or schema.startswith("pg_"):
        raise CheckpointConfigError("the checkpoint schema must be a dedicated lower-case schema (not public or pg_*)")
    return schema


def validate_settings(settings: CheckpointDbSettings) -> None:
    validate_schema_name(settings.schema)
    if settings.branch in PROTECTED_BRANCHES:
        raise CheckpointConfigError(f"checkpoint writes on the '{settings.branch}' branch are not allowed; use a dev branch")
    if settings.sslmode not in ("require", "verify-ca", "verify-full"):
        raise CheckpointConfigError("checkpoint connections must use TLS (sslmode require, verify-ca or verify-full)")


# ---------------------------------------------------------------------------------------------- credentials
class TokenProvider:
    """Short-lived database credential, fetched lazily and refreshed `refresh_margin` before it expires.

    Thread-safe (the pool opens connections from worker threads). The token is only ever returned to psycopg.
    """

    def __init__(self, fetch: Callable[[], str], lifetime_seconds: float = 60 * 60, refresh_margin_seconds: float = 15 * 60,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch = fetch
        self._ttl = lifetime_seconds - refresh_margin_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires = 0.0
        self.fetch_count = 0

    def get(self) -> str:
        with self._lock:
            if self._token is None or self._clock() >= self._expires:
                self._token = self._fetch()
                self._expires = self._clock() + self._ttl
                self.fetch_count += 1
            return self._token

    def __repr__(self) -> str:
        return f"TokenProvider(fetches={self.fetch_count})"


def lakebase_token_provider(settings: CheckpointDbSettings, clock: Callable[[], float] = time.monotonic) -> TokenProvider:
    """Reuses the Lakebase POC's credential generator (Databricks CLI, the profile's OAuth login; 60-minute token)."""
    from infrastructure.lakebase_poc.lakebase_poc.credentials import generate_token
    return TokenProvider(lambda: generate_token(settings.endpoint, settings.databricks_profile), clock=clock)


def oauth_connection_class(tokens: TokenProvider) -> type[psycopg.Connection]:
    """A psycopg Connection class that sets a current credential as the password of every NEW connection."""

    class OAuthConnection(psycopg.Connection):
        @classmethod
        def connect(cls, conninfo: str = "", **kwargs: Any):
            kwargs["password"] = tokens.get()
            return super().connect(conninfo, **kwargs)

    return OAuthConnection


# ------------------------------------------------------------------------------------------------------ pool
def _session_setup(schema: str, statement_timeout_ms: int) -> Callable[[psycopg.Connection], None]:
    def configure(conn: psycopg.Connection) -> None:
        path = sql.Identifier(schema).as_string(conn)
        conn.execute("SELECT set_config('search_path', %s, false), set_config('statement_timeout', %s, false), "
                     "set_config('lock_timeout', '10s', false), set_config('idle_in_transaction_session_timeout', '60s', false)",
                     (path, f"{statement_timeout_ms}ms"))
    return configure


def checkpoint_pool(connect_kwargs: Mapping[str, Any], schema: str, tokens: TokenProvider, *, max_size: int = 2,
                    wait_seconds: float = 30.0, statement_timeout_ms: int = 15_000,
                    max_lifetime_seconds: float = 40 * 60, name: str = "wbg-checkpoints") -> ConnectionPool:
    """A closed pool (call `.open(wait=True)`); every connection is pinned to `schema` before first use."""
    validate_schema_name(schema)
    return ConnectionPool(
        conninfo="", open=False, name=name, min_size=1, max_size=max_size, timeout=wait_seconds,
        max_lifetime=max_lifetime_seconds, max_idle=300, connection_class=oauth_connection_class(tokens),
        kwargs={**connect_kwargs, "autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        configure=_session_setup(schema, statement_timeout_ms), check=ConnectionPool.check_connection)


def lakebase_checkpoint_pool(settings: CheckpointDbSettings, tokens: TokenProvider) -> ConnectionPool:
    validate_settings(settings)
    return checkpoint_pool(settings.connect_kwargs(), settings.schema, tokens, max_size=settings.pool_max_size,
                           wait_seconds=settings.pool_wait_seconds, statement_timeout_ms=settings.statement_timeout_ms,
                           max_lifetime_seconds=settings.max_lifetime_seconds)


def endpoint_host(endpoint: str, profile: str) -> str:
    """The host of a Lakebase endpoint (read-only `databricks postgres get-endpoint`)."""
    import json
    import subprocess

    from infrastructure.lakebase_poc.lakebase_poc.credentials import databricks_cli
    described = subprocess.run([databricks_cli(), "postgres", "get-endpoint", endpoint, "-p", profile, "-o", "json"],
                               capture_output=True, text=True, check=True, timeout=90)
    return json.loads(described.stdout)["status"]["hosts"]["host"]


def safe_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=None, allowed_msgpack_modules=None)


def checkpointer(pool: ConnectionPool) -> PostgresSaver:
    return PostgresSaver(pool, serde=safe_serde())


# ------------------------------------------------------------------------------------------------- isolation
@dataclass(frozen=True)
class IsolationReport:
    schema: str
    search_path: str
    effective_schemas: tuple[str, ...]
    can_use: bool
    can_create: bool
    existing_objects: tuple[str, ...]
    server_version: str


@dataclass(frozen=True)
class SetupReport:
    schema: str
    created: tuple[str, ...]
    present: tuple[str, ...]
    other_schemas_unchanged: bool


def database_inventory(conn: psycopg.Connection) -> dict[str, tuple[tuple[str, str], ...]]:
    """Every non-system schema with its relations (name, kind); used to prove that nothing else changed."""
    rows = conn.execute(
        "SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS kind FROM pg_namespace n "
        "LEFT JOIN pg_class c ON c.relnamespace = n.oid WHERE n.nspname <> ALL(%s) AND n.nspname NOT LIKE 'pg\\_temp\\_%%' "
        "AND n.nspname NOT LIKE 'pg\\_toast\\_temp\\_%%' ORDER BY 1, 2", (list(SYSTEM_SCHEMAS),)).fetchall()
    inventory: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        row = _as_dict(row, ("schema", "name", "kind"))
        objects = inventory.setdefault(row["schema"], [])
        if row["name"] is not None:
            objects.append((row["name"], row["kind"]))
    return {schema: tuple(objects) for schema, objects in inventory.items()}


def verify_isolation(conn: psycopg.Connection, schema: str) -> IsolationReport:
    """Raise IsolationError unless unqualified SQL on `conn` can only see and create objects in `schema`."""
    validate_schema_name(schema)
    row = _as_dict(conn.execute(
        "SELECT current_setting('search_path') AS search_path, current_schemas(false) AS effective, "
        "current_setting('server_version') AS version, "
        "EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s) AS schema_exists", (schema,)).fetchone(),
        ("search_path", "effective", "version", "schema_exists"))
    if not row["schema_exists"]:
        raise IsolationError("the checkpoint schema does not exist (create it explicitly first)")
    privileges = _as_dict(conn.execute("SELECT has_schema_privilege(%s, 'USAGE') AS can_use, "
                                       "has_schema_privilege(%s, 'CREATE') AS can_create", (schema, schema)).fetchone(),
                          ("can_use", "can_create"))
    objects = tuple(name for name, _ in database_inventory(conn).get(schema, ()))
    effective = tuple(row["effective"])
    if effective != (schema,):
        raise IsolationError(f"search_path resolves to {len(effective)} schema(s), not only the checkpoint schema")
    if not (privileges["can_use"] and privileges["can_create"]):
        raise IsolationError("the role lacks USAGE or CREATE on the checkpoint schema")
    unexpected = sorted(set(objects) - CHECKPOINT_OBJECTS)
    if unexpected:
        raise IsolationError(f"the checkpoint schema contains {len(unexpected)} non-checkpoint object(s)")
    return IsolationReport(schema, row["search_path"], effective, True, True, objects, row["version"])


def create_schema(conn: psycopg.Connection, schema: str) -> bool:
    """Create the dedicated schema if missing (explicit, separate step). Returns True when it was created."""
    validate_schema_name(schema)
    exists = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone()
    if exists:
        return False
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    return True


def isolated_setup(pool: ConnectionPool, saver: PostgresSaver, schema: str) -> SetupReport:
    """Verify isolation, run the official `setup()`, and prove that only checkpointer objects in `schema` appeared."""
    with pool.connection() as conn:
        verify_isolation(conn, schema)
        before = database_inventory(conn)
    saver.setup()
    with pool.connection() as conn:
        after = database_inventory(conn)
    others_unchanged = {s: o for s, o in before.items() if s != schema} == {s: o for s, o in after.items() if s != schema}
    present = tuple(sorted(name for name, _ in after.get(schema, ())))
    created = tuple(sorted(set(present) - {name for name, _ in before.get(schema, ())}))
    if not others_unchanged or set(present) - CHECKPOINT_OBJECTS:
        raise IsolationError("setup changed objects outside the checkpoint schema or created unexpected objects")
    return SetupReport(schema, created, present, others_unchanged)


def _as_dict(row: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return row if isinstance(row, dict) else dict(zip(names, row))
