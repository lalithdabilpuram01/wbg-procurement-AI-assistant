"""Explicit, non-destructive initialisation of the conversation store schema.

Prerequisite (once, as a PostgreSQL superuser): conversation/sql/bootstrap_roles.sql.
Applies conversation/sql/schema.sql as wbg_conversation_admin (DSN from WBG_CONVERSATION_ADMIN_DSN). Only
CREATE ... IF NOT EXISTS statements and grants are run, so re-running it never drops or rewrites conversation data.
Usage (from procurement-intelligence/):
    /opt/anaconda3/bin/python3 -m conversation.init_store
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

from conversation.postgres_store import DEFAULT_ADMIN_DSN, DEFAULT_SCHEMA, checked_schema

SCHEMA_SQL = Path(__file__).with_name("sql") / "schema.sql"


def schema_statements(schema: str) -> str:
    name = checked_schema(schema)
    return SCHEMA_SQL.read_text().replace("{schema_literal}", name).replace("{schema}", name)


def apply_schema(admin_dsn: str, schema: str = DEFAULT_SCHEMA) -> None:
    with psycopg.connect(admin_dsn) as conn:
        conn.execute(schema_statements(schema))


def main() -> None:
    schema = os.environ.get("WBG_CONVERSATION_SCHEMA", DEFAULT_SCHEMA)
    apply_schema(os.environ.get("WBG_CONVERSATION_ADMIN_DSN", DEFAULT_ADMIN_DSN), schema)
    print(f"conversation schema '{schema}' is present (non-destructive apply)")


if __name__ == "__main__":
    main()
