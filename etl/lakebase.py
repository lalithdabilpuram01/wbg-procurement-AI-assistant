"""A Lakebase connection pool for a Databricks job: the job's own identity mints the database credential (no CLI, no
profile, no secret). Only development branches are accepted; the `production` branch is refused."""
from __future__ import annotations

import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DEV_ENDPOINT = "projects/wbg-procurement-dev/branches/dev-orchestrator/endpoints/primary"
PROTECTED_BRANCHES = {"production", "main"}


def branch_of(endpoint: str) -> str:
    """The branch name inside a Lakebase endpoint path; raises ValueError for any other format."""
    match = re.fullmatch(r"projects/[^/]+/branches/([^/]+)/endpoints/[^/]+", endpoint)
    if not match:
        raise ValueError("the endpoint must be projects/<p>/branches/<b>/endpoints/<e>")
    return match.group(1)


def pool(endpoint: str, schema: str, purpose: str) -> ConnectionPool:
    """An open pool pinned to `schema`; every new connection gets a fresh credential."""
    if branch_of(endpoint) in PROTECTED_BRANCHES:
        raise ValueError(f"writes on the '{branch_of(endpoint)}' branch are not allowed; use a development branch")
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    host = w.postgres.get_endpoint(name=endpoint).status.hosts.host
    user = w.current_user.me().user_name

    class JobCredentialConnection(psycopg.Connection):
        """A psycopg connection that mints a fresh database credential each time it connects."""
        @classmethod
        def connect(cls, conninfo: str = "", **kwargs):
            kwargs["password"] = w.postgres.generate_database_credential(endpoint=endpoint).token
            return super().connect(conninfo, **kwargs)

    def pin(conn: psycopg.Connection) -> None:
        conn.execute("SELECT set_config('search_path', %s, false)", (sql.Identifier(schema).as_string(conn),))

    result = ConnectionPool(conninfo="", open=False, name=f"wbg-{purpose}", min_size=1, max_size=2, timeout=60,
                            connection_class=JobCredentialConnection, configure=pin,
                            kwargs={"host": host, "port": 5432, "dbname": "databricks_postgres", "user": user, "sslmode": "require",
                                    "application_name": f"wbg-etl-{purpose}", "autocommit": True, "prepare_threshold": 0,
                                    "row_factory": dict_row})
    result.open(wait=True, timeout=90)
    return result
