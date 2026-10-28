"""The feedback and learning store in Lakebase: schema `wbg_learning` on a development branch (by default the
checkpoints' branch, dev-orchestrator; WBG_LEARNING_ENDPOINT overrides it). Writes on `production` are refused.

It has its own small pool (the checkpoint schema must hold only checkpoint objects), the same OAuth credentials,
TLS and timeouts as the checkpoint pool, and is checked at app start: the app refuses to start feedback without it.

    cd orchestrator
    uv run python -m wbg_orchestrator.learning_db init     # create schema, tables and policies (non-destructive)
    uv run python -m wbg_orchestrator.learning_db check    # list the tables and whether row-level security is forced
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg
from psycopg_pool import ConnectionPool

from conversation.learning import LEARNING_SCHEMA, apply_schema

from .checkpointing import CheckpointDbSettings, checkpoint_pool, endpoint_host, lakebase_token_provider, validate_settings

DEV_ENDPOINT = "projects/wbg-procurement-dev/branches/dev-orchestrator/endpoints/primary"
LEARNING_TABLES = ("feedback", "learned_items", "learning_uses", "preferences", "reviews")


class LearningStoreMissing(RuntimeError):
    """The learning schema or one of its tables is missing, or row-level security is not forced."""


def learning_endpoint(default: str = DEV_ENDPOINT) -> str:
    return os.environ.get("WBG_LEARNING_ENDPOINT", default)


def learning_pool(endpoint: str) -> ConnectionPool:
    """An open pool to the learning schema of `endpoint` (a development branch only)."""
    return dev_schema_pool(endpoint, LEARNING_SCHEMA, "learning")


def dev_schema_pool(endpoint: str, schema: str, purpose: str) -> ConnectionPool:
    """An open pool pinned to `schema` on a Lakebase development branch (production is refused)."""
    from infrastructure.lakebase_poc.lakebase_poc.settings import load_settings as lakebase_login
    login = lakebase_login()
    settings = CheckpointDbSettings(host=endpoint_host(endpoint, login.databricks_profile), database="databricks_postgres",
                                    user=login.user, endpoint=endpoint, schema=schema, databricks_profile=login.databricks_profile)
    validate_settings(settings)                                            # refuses production and non-TLS settings
    pool = checkpoint_pool({**settings.connect_kwargs(), "application_name": f"wbg-orchestrator-{purpose}"}, schema,
                           lakebase_token_provider(settings), name=f"wbg-{purpose}")
    pool.open(wait=True, timeout=60)
    return pool


def table_status(conn: psycopg.Connection) -> dict[str, bool]:
    """Each learning table present in the schema -> whether its row-level security is enabled and forced."""
    rows = conn.execute("SELECT c.relname AS name, c.relrowsecurity AND c.relforcerowsecurity AS forced FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r'",
                        (LEARNING_SCHEMA,)).fetchall()
    return {r["name"]: r["forced"] for r in rows if r["name"] in LEARNING_TABLES}


def verify(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        status = table_status(conn)
    if set(status) != set(LEARNING_TABLES) or not all(status.values()):
        raise LearningStoreMissing("the learning store is not initialised (run: python -m wbg_orchestrator.learning_db init)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Feedback and learning store in Lakebase")
    parser.add_argument("command", choices=["init", "check"])
    args = parser.parse_args(argv)
    endpoint = learning_endpoint()
    pool = learning_pool(endpoint)
    try:
        with pool.connection() as conn:
            if args.command == "init":
                apply_schema(conn)
            status = table_status(conn)
    finally:
        pool.close()
    print(json.dumps({"branch": endpoint.split("/branches/")[1].split("/")[0], "schema": LEARNING_SCHEMA,
                      "tables_rls_forced": status}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
