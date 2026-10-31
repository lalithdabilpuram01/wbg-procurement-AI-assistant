"""The account store in Lakebase (schema `wbg_auth`, table `app_user`) and the `wbg-user` command.

The store lives on a development branch: by default the same branch as the feedback store (dev-orchestrator);
WBG_ACCOUNTS_ENDPOINT moves it to another branch. Writes on `production` are refused (learning_db.dev_schema_pool).
The API refuses to start when the table is missing, so nobody is ever signed in against an empty store by mistake.

    uv run wbg-user init-db                                  create the schema and table (safe to run twice)
    uv run wbg-user migrate                                  copy the users of ~/.wbg/api_users.json into Lakebase
    uv run wbg-user add --user rita --password '...' [--reviewer]   create or replace a user (prints a recovery code)
    uv run wbg-user add --user etl-bot                       a script user: prints a bearer token once
    uv run wbg-user recovery-code --user rita                a new recovery code for a user who lost theirs
    uv run wbg-user list                                     usernames, roles, and which sign-in each one has
    uv run wbg-user --user rita --password '...'             the older form of `add`, still accepted
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from psycopg import sql
from psycopg_pool import ConnectionPool

from .accounts import AccountError, JsonFileAccountStore, PostgresAccountStore, UserDirectory
from .learning_db import dev_schema_pool, learning_endpoint

ACCOUNTS_SCHEMA = "wbg_auth"
LEGACY_USERS_FILE = Path.home() / ".wbg" / "api_users.json"
# One statement each: Lakebase connections prepare every statement, and a prepared statement holds one command.
SCHEMA_SQL = (
    "CREATE SCHEMA IF NOT EXISTS {schema}",
    """CREATE TABLE IF NOT EXISTS {schema}.app_user (
    tenant_id        text NOT NULL,
    user_id          text NOT NULL,
    roles            text[] NOT NULL DEFAULT '{{}}',
    password_salt    text,
    password_scrypt  text,
    recovery_salt    text,
    recovery_scrypt  text,
    token_sha256     text UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
)""",
    "COMMENT ON TABLE {schema}.app_user IS 'App users: salted scrypt hashes of passwords and recovery codes, "
    "SHA-256 of script tokens. No secret in clear.'",
)


class AccountStoreMissing(RuntimeError):
    """The account table does not exist yet."""


def accounts_endpoint() -> str:
    return os.environ.get("WBG_ACCOUNTS_ENDPOINT") or learning_endpoint()


def accounts_pool(endpoint: str | None = None) -> ConnectionPool:
    return dev_schema_pool(endpoint or accounts_endpoint(), ACCOUNTS_SCHEMA, "accounts")


def apply_schema(conn, schema: str = ACCOUNTS_SCHEMA) -> None:
    for statement in SCHEMA_SQL:                                  # '{{}}' becomes the empty array '{}'
        conn.execute(sql.SQL(statement).format(schema=sql.Identifier(schema)))


def verify(pool: ConnectionPool, schema: str = ACCOUNTS_SCHEMA) -> None:
    with pool.connection() as conn:
        found = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.app_user",)).fetchone()
    if not (found and list(found.values() if isinstance(found, dict) else found)[0]):
        raise AccountStoreMissing("the account store is not initialised (run: uv run wbg-user init-db, then wbg-user migrate)")


def directory(pool: ConnectionPool, schema: str = ACCOUNTS_SCHEMA) -> UserDirectory:
    return UserDirectory(PostgresAccountStore(pool.connection, schema))


def migrate(source: JsonFileAccountStore, target: UserDirectory) -> dict[str, list[str]]:
    """Copy every account of the JSON file (hashes as they are; nobody needs a new password). An account that already
    exists in the target is left unchanged."""
    copied, kept = [], []
    for account in source.all():
        (copied if target.store.insert(account) else kept).append(account.user_id)
    return {"copied": copied, "already_there": kept}


# ------------------------------------------------------------------------------------------------------ command
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wbg-user", description="App users in Lakebase (schema wbg_auth).")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="create the schema and table (safe to run twice)")
    copy = commands.add_parser("migrate", help="copy the users of the old JSON user file into Lakebase")
    copy.add_argument("--file", type=Path, default=LEGACY_USERS_FILE)
    add = commands.add_parser("add", help="create or replace a user")
    add.add_argument("--user", required=True)
    add.add_argument("--password", help="sign in with this password (without it, a script token is printed once)")
    add.add_argument("--reviewer", action="store_true", help="may use the Review queue")
    code = commands.add_parser("recovery-code", help="a new recovery code for a user who lost theirs")
    code.add_argument("--user", required=True)
    commands.add_parser("list", help="usernames, roles and sign-in methods")
    return parser


def main(argv: list[str]) -> int:
    if argv and argv[0].startswith("--"):                 # the older form: wbg-user --user x --password y [--reviewer]
        argv = ["add", *argv]
    args = _parser().parse_args(argv)
    pool = accounts_pool()
    try:
        if args.command == "init-db":
            with pool.connection() as conn:
                apply_schema(conn)
            print(json.dumps({"schema": ACCOUNTS_SCHEMA, "table": "app_user", "branch": _branch()}, indent=1))
            return 0
        verify(pool)
        users = directory(pool)
        if args.command == "migrate":
            print(json.dumps(migrate(JsonFileAccountStore(args.file), users), indent=1))
        elif args.command == "add":
            token, recovery = users.set_user(args.user, args.password, args.reviewer)
            print(f"User {args.user.strip().lower()} saved{' (reviewer)' if args.reviewer else ''}.")
            if token:
                print(f"Bearer token (shown once): {token}")
            if recovery:
                print(f"Recovery code (shown once; needed for 'Forgot password'): {recovery}")
        elif args.command == "recovery-code":
            print(f"New recovery code for {args.user} (shown once; the old one no longer works): "
                  f"{users.issue_recovery_code(args.user)}")
        elif args.command == "list":
            for account in users.store.all():
                ways = [w for w, present in (("password", account.password), ("token", account.token_sha256)) if present]
                print(f"{account.user_id:<24} {','.join(account.roles) or '-':<10} {'+'.join(ways)}")
    except (AccountError, AccountStoreMissing) as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        pool.close()
    return 0


def _branch() -> str:
    endpoint = accounts_endpoint()
    return endpoint.split("/branches/")[1].split("/")[0] if "/branches/" in endpoint else endpoint


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
