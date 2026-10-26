"""Read-only inventory of the Lakebase project (Milestone B): branches, server, role privileges and every schema.

Run twice (`--label before` / `--label after`) to prove that existing schemas did not change. The session is
read-only (`default_transaction_read_only = on`); the credential is a 60-minute OAuth token from the existing Lakebase
POC helper (Databricks CLI) and is never printed or stored. Identities are reported only as SHA-256 prefixes.

    cd orchestrator && env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m live.lakebase_inspect --label before
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from infrastructure.lakebase_poc.lakebase_poc.credentials import databricks_cli, generate_token
from infrastructure.lakebase_poc.lakebase_poc.settings import load_settings

from wbg_orchestrator.checkpointing import CHECKPOINT_TABLES, database_inventory

RESULTS = Path(__file__).resolve().parents[1] / "live_results"


def branches(project: str, profile: str) -> list[dict]:
    completed = subprocess.run([databricks_cli(), "postgres", "list-branches", f"projects/{project}", "-p", profile, "-o", "json"],
                               capture_output=True, text=True, timeout=90, check=True)
    return [{"branch_id": b["branch_id"], "default": b["status"].get("default"), "state": b["status"].get("current_state"),
             "protected": b["status"].get("is_protected"), "logical_size_bytes": b["status"].get("logical_size_bytes")}
            for b in json.loads(completed.stdout)]


def inspect(label: str) -> dict:
    settings = load_settings()
    project = settings.endpoint.split("/")[1]
    report = {"label": label, "project": project, "endpoint_branch": settings.endpoint.split("/")[3],
              "branches": branches(project, settings.databricks_profile)}
    with psycopg.connect(host=settings.host, port=settings.port, dbname=settings.database, user=settings.user,
                         password=generate_token(settings.endpoint, settings.databricks_profile), sslmode="require",
                         connect_timeout=30, application_name="wbg-orchestrator-readonly-inspect", autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = '20s'")
        row = conn.execute("SELECT current_setting('server_version'), current_database(), current_user, "
                           "current_setting('search_path'), current_setting('default_transaction_read_only'), "
                           "has_database_privilege(current_database(), 'CREATE'), "
                           "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user), "
                           "(SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid())").fetchone()
        inventory = database_inventory(conn)
        tables = {}
        for schema, objects in inventory.items():
            for name, kind in objects:
                if kind in ("r", "p"):
                    count = conn.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(name))).fetchone()[0]
                    tables[f"{schema}.{name}"] = count
        sizes = dict(conn.execute("SELECT nspname, coalesce(sum(pg_total_relation_size(c.oid)), 0)::bigint FROM pg_namespace n "
                                  "LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind IN ('r','p','m') "
                                  "WHERE nspname = ANY(%s) GROUP BY 1", (list(inventory),)).fetchall())
    report.update(server_version=row[0], database=row[1], role_sha256=hashlib.sha256(row[2].encode()).hexdigest()[:12],
                  default_search_path=row[3], session_read_only=row[4], can_create_schema_in_database=row[5], superuser=row[6],
                  tls=row[7], schemas={s: [f"{n}:{k}" for n, k in o] for s, o in inventory.items()}, table_rows=tables,
                  schema_bytes={s: int(b) for s, b in sizes.items()},
                  checkpoint_tables_present=sorted(f"{s}.{n}" for s, o in inventory.items() for n, _ in o if n in CHECKPOINT_TABLES))
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--label", required=True, choices=["before", "after"])
    args = parser.parse_args(argv)
    report = inspect(args.label)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"lakebase_inventory_{args.label}.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({k: report[k] for k in ("branches", "server_version", "endpoint_branch", "can_create_schema_in_database",
                                              "session_read_only", "tls", "table_rows", "checkpoint_tables_present")}, indent=2))
    before = RESULTS / "lakebase_inventory_before.json"
    if args.label == "after" and before.is_file():
        previous = json.loads(before.read_text())
        same = {k: previous[k] == report[k] for k in ("schemas", "table_rows", "branches")}
        print("unchanged since 'before':", same)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
