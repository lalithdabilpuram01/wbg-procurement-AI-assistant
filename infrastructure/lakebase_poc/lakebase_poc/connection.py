"""TLS connection to Lakebase with an OAuth credential, timeouts and guaranteed close."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from lakebase_poc.credentials import generate_token
from lakebase_poc.settings import LakebaseSettings

APPLICATION_NAME = "wbg-lakebase-pgvector-poc"


def connection_kwargs(settings: LakebaseSettings, password: str) -> dict[str, object]:
    """psycopg connection arguments for these settings and a credential."""
    return {"host": settings.host, "port": settings.port, "dbname": settings.database, "user": settings.user,
            "password": password, "sslmode": settings.sslmode, "connect_timeout": settings.connect_timeout_seconds,
            "application_name": APPLICATION_NAME}


@contextmanager
def connect(settings: LakebaseSettings, token: str | None = None, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Open one connection (fresh credential unless given), apply session timeouts, always close it.
    autocommit=True makes every `conn.transaction()` block a real BEGIN/COMMIT."""
    password = token or generate_token(settings.endpoint, settings.databricks_profile)
    try:
        conn = psycopg.connect(**connection_kwargs(settings, password))
    except psycopg.OperationalError as error:
        raise ConnectionError(f"could not connect to Lakebase: {type(error).__name__}") from None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('statement_timeout', %s, false), set_config('lock_timeout', '10s', false), "
                        "set_config('idle_in_transaction_session_timeout', '60s', false)",
                        (f"{settings.statement_timeout_ms}ms",))
        conn.commit()
        conn.autocommit = autocommit
        yield conn
    finally:
        conn.close()
