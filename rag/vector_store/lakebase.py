"""OAuth connection pool to the Lakebase vector store, pinned to schema `wbg_rag`.

Settings come from the Lakebase POC configuration (host, database, user, endpoint; git-ignored local file or
environment). The password is a short-lived OAuth credential generated through the Databricks CLI, cached in memory
and refreshed before it expires; it is never logged or written. Query pools are read-only; only the loader writes.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from infrastructure.lakebase_poc.lakebase_poc.credentials import generate_token
from infrastructure.lakebase_poc.lakebase_poc.settings import LakebaseSettings, load_settings

SCHEMA = "wbg_rag"
APPLICATION_NAME = "wbg-rag-vector-store"
TOKEN_REUSE_SECONDS = 40 * 60          # OAuth credentials live 60 minutes


class CachedToken:
    """Thread-safe credential cache: fetches lazily, refetches after `reuse_seconds`."""

    def __init__(self, fetch: Callable[[], str], reuse_seconds: float = TOKEN_REUSE_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        """`fetch` gets a fresh credential; `clock` measures the token's age (monotonic time by default)."""
        self._fetch, self._reuse, self._clock = fetch, reuse_seconds, clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._fetched_at = 0.0

    def get(self) -> str:
        """Return the cached token, fetching a new one when none is cached or it is older than the reuse window."""
        with self._lock:
            if self._token is None or self._clock() - self._fetched_at >= self._reuse:
                self._token, self._fetched_at = self._fetch(), self._clock()
            return self._token


def _connection_class(token: CachedToken) -> type[psycopg.Connection]:
    """A psycopg Connection class that sets the OAuth password from the token cache on every new connection."""
    class OAuthConnection(psycopg.Connection):
        """psycopg Connection that takes its password from the token cache each time it connects."""
        @classmethod
        def connect(cls, conninfo: str = "", **kwargs: Any):
            kwargs["password"] = token.get()
            return super().connect(conninfo, **kwargs)
    return OAuthConnection


def _configure(read_only: bool, statement_timeout_ms: int) -> Callable[[psycopg.Connection], None]:
    """Return the per-connection setup: search_path, statement timeout, read-only default and pgvector types."""
    def configure(conn: psycopg.Connection) -> None:
        """Apply the session settings and register the pgvector types on one new pool connection."""
        conn.execute("SELECT set_config('search_path', %s, false), set_config('statement_timeout', %s, false), "
                     "set_config('default_transaction_read_only', %s, false)",
                     (f"{SCHEMA}, public", f"{statement_timeout_ms}ms", "on" if read_only else "off"))
        register_vector(conn)
    return configure


def open_pool(settings: LakebaseSettings | None = None, *, read_only: bool = True, max_size: int = 2,
              statement_timeout_ms: int = 30_000) -> ConnectionPool:
    """An open pool; every connection has search_path `wbg_rag, public` and the pgvector type registered."""
    settings = settings or load_settings()
    token = CachedToken(lambda: generate_token(settings.endpoint, settings.databricks_profile))
    connect_kwargs = {"host": settings.host, "port": settings.port, "dbname": settings.database, "user": settings.user,
                      "sslmode": settings.sslmode, "connect_timeout": settings.connect_timeout_seconds,
                      "application_name": APPLICATION_NAME, "autocommit": True, "prepare_threshold": None}
    pool = ConnectionPool(conninfo="", open=False, name="wbg-rag-vectors", min_size=1, max_size=max_size, timeout=60,
                          max_lifetime=TOKEN_REUSE_SECONDS, max_idle=300, connection_class=_connection_class(token),
                          kwargs=connect_kwargs, configure=_configure(read_only, statement_timeout_ms),
                          check=ConnectionPool.check_connection)
    pool.open(wait=True)
    return pool
