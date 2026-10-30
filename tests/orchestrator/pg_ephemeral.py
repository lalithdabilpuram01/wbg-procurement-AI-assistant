"""A throwaway PostgreSQL cluster for checkpoint tests: a new data directory under the system temp folder, unix
socket only (no TCP listener), started with the local PostgreSQL 18 binaries and deleted afterwards. It never touches
the existing local PostgreSQL server or its data (registry, conversations).

The cluster mimics the Lakebase layout: database `databricks_postgres`; existing application schemas owned by another
role (`wbg_rag_poc` with a table and row, plus `public` with a table); and a non-superuser application role
`ckpt_app` that owns only its checkpoint schema. `public` is deliberately left creatable by that role, to prove that
search_path pinning, not luck, keeps the checkpointer out of it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import psycopg

PG_BIN = Path(os.environ.get("WBG_TEST_PG_BIN", "/Library/PostgreSQL/18/bin"))
PORT = 54329
DATABASE, SUPERUSER, APP_ROLE, SCHEMA = "databricks_postgres", "wbg_admin", "ckpt_app", "wbg_orchestrator_ckpt"


def available() -> bool:
    return all((PG_BIN / tool).is_file() for tool in ("initdb", "pg_ctl", "postgres"))


@dataclass(frozen=True)
class Cluster:
    socket_dir: str
    version: str

    def kwargs(self, user: str = APP_ROLE, database: str = DATABASE) -> dict:
        return {"host": self.socket_dir, "port": PORT, "dbname": database, "user": user}

    def admin(self) -> psycopg.Connection:
        return psycopg.connect(**self.kwargs(SUPERUSER), autocommit=True)


def _run(*args: str) -> str:
    return subprocess.run(list(args), check=True, capture_output=True, text=True, timeout=120).stdout


@contextmanager
def ephemeral_cluster() -> Iterator[Cluster]:
    root = tempfile.mkdtemp(prefix="wbgpg-")              # short path: unix socket names are limited to ~100 bytes
    data = os.path.join(root, "data")
    try:
        _run(str(PG_BIN / "initdb"), "-D", data, "-U", SUPERUSER, "--auth=trust", "--encoding=UTF8", "--no-sync", "--no-instructions")
        options = f"-k {root} -p {PORT} -c listen_addresses='' -c fsync=off"
        _run(str(PG_BIN / "pg_ctl"), "-D", data, "-o", options, "-l", os.path.join(root, "server.log"), "-w", "start")
        cluster = Cluster(root, _run(str(PG_BIN / "postgres"), "--version").strip())
        _seed(cluster)
        yield cluster
    finally:
        if os.path.isdir(data):
            subprocess.run([str(PG_BIN / "pg_ctl"), "-D", data, "-m", "fast", "-w", "stop"], capture_output=True, timeout=60)
        shutil.rmtree(root, ignore_errors=True)


def _seed(cluster: Cluster) -> None:
    with psycopg.connect(**cluster.kwargs(SUPERUSER, "postgres"), autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {DATABASE}")
    with cluster.admin() as conn:
        conn.execute(f"CREATE ROLE {APP_ROLE} LOGIN")
        conn.execute("CREATE SCHEMA wbg_rag_poc")
        conn.execute("CREATE TABLE wbg_rag_poc.chunks (id int PRIMARY KEY, text text)")
        conn.execute("INSERT INTO wbg_rag_poc.chunks VALUES (1, 'existing application data')")
        conn.execute("CREATE TABLE public.existing_table (id int)")
        conn.execute(f"GRANT CREATE, USAGE ON SCHEMA public TO {APP_ROLE}")      # a trap the pinning must avoid
        conn.execute(f"CREATE SCHEMA {SCHEMA} AUTHORIZATION {APP_ROLE}")
