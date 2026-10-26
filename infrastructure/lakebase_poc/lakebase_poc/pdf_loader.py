"""Idempotent, bounded, verified write of ONE document version's evidence into the isolated PDF POC schema.

Runs only with LAKEBASE_LIVE_TESTS=1 and LAKEBASE_PDF_POC_WRITE_APPROVED=1. Safety rules:
* writes go only to schema `wbg_rag_pdf_poc`; the bundle must hold exactly the approved document version;
* before writing, the target schema is inspected: any relation other than the approved tables and their indexes stops
  the run (nothing is overwritten, deleted or reconciled);
* the `vector` extension must already be installed (it is verified, never created); `lakebase_vector` must be absent;
* all inserts and the content verification run in ONE transaction. Existing primary keys are not silently accepted:
  every stored row is compared with the prepared record (content and provenance, including the embedding hash), and
  any mismatch or unrelated row aborts and rolls back the whole transaction.
"""
from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import os
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.types.json import Jsonb

from lakebase_poc.pdf_evidence import DOCUMENT_ID, EvidenceBundle
from lakebase_poc.pdf_schema import HNSW_INDEX, PRIMARY_KEYS, SCHEMA, TABLES, checked, statements

WRITE_OPT_IN = "LAKEBASE_PDF_POC_WRITE_APPROVED"
MAX_RECORDS = 1_000                               # dry run: 311 records; anything far larger is refused
JSON_COLUMNS = {"sanitisation", "source_spans", "preprocessing", "fields", "header", "cells"}
DATE_COLUMNS = {"document_date", "disclosure_date"}


class WriteNotApproved(RuntimeError):
    """Real-data writes need the explicit approval opt-in for this invocation."""


class TargetNotClean(RuntimeError):
    """The target schema holds unexpected objects; nothing was written."""


class ContentMismatch(RuntimeError):
    """Stored rows differ from the prepared records; the transaction was rolled back."""


def write_approved(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return env.get("LAKEBASE_LIVE_TESTS") == "1" and env.get(WRITE_OPT_IN) == "1"


def guard_bundle(bundle: EvidenceBundle) -> None:
    versions = {rec["document_version_id"] for rec in bundle.records["documents"]}
    total = sum(len(rows) for rows in bundle.records.values())
    if len(versions) != 1 or not next(iter(versions)).startswith(f"{DOCUMENT_ID}:"):
        raise ValueError("bundle must hold exactly the approved document version")
    if total > MAX_RECORDS:
        raise ValueError(f"bundle has {total} records, above the approved bound {MAX_RECORDS}")
    if set(bundle.records) != set(TABLES):
        raise ValueError("bundle tables do not match the POC schema")


def insert_statement(table: str, columns: list[str]) -> sql.Composed:
    return sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
        sql.Identifier(checked(SCHEMA)), sql.Identifier(checked(table)),
        sql.SQL(", ").join(sql.Identifier(checked(c)) for c in columns), sql.SQL(", ").join(sql.Placeholder() * len(columns)))


def _adapt(column: str, value: Any) -> Any:
    if column in JSON_COLUMNS:
        return Jsonb(value)
    if column in DATE_COLUMNS and isinstance(value, str):
        return dt.date.fromisoformat(value)
    return value


def end_read_transaction(conn: psycopg.Connection) -> None:
    """Close any implicit read transaction so the next `conn.transaction()` is a real top-level BEGIN/COMMIT
    (not a savepoint inside an uncommitted transaction)."""
    if not conn.autocommit and conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.commit()


# ------------------------------------------------------------------------------------------- pre-write checks
def expected_relations() -> set[str]:
    """The approved tables, their primary-key / unique indexes and the HNSW index."""
    return set(TABLES) | {f"{t}_pkey" for t in TABLES} | {"documents_document_id_sha256_key", HNSW_INDEX}


def inspect_target(conn: psycopg.Connection) -> dict[str, Any]:
    """Read-only: does the schema exist, and which relations does it hold (excluding TOAST)."""
    with conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)", (SCHEMA,))
        exists = cur.fetchone()[0]
        cur.execute("SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relkind <> 't' ORDER BY 1", (SCHEMA,))
        relations = {name: kind for name, kind in cur.fetchall()}
    return {"schema_exists": exists, "relations": relations,
            "unexpected_relations": sorted(set(relations) - expected_relations())}


def verify_extensions(conn: psycopg.Connection) -> dict[str, str | None]:
    with conn.cursor() as cur:
        cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname = ANY(%s)", (["vector", "lakebase_vector"],))
        installed = dict(cur.fetchall())
    if "vector" not in installed:
        raise TargetNotClean("the vector extension is not installed; installing it is outside this approval")
    if "lakebase_vector" in installed:
        raise TargetNotClean("lakebase_vector is installed; this POC uses standard pgvector only")
    return {"vector": installed["vector"], "lakebase_vector": None}


# ----------------------------------------------------------------------------------------- content verification
def normalise(value: Any) -> Any:
    """Comparable form of a value as prepared in Python or as returned by PostgreSQL."""
    if hasattr(value, "to_numpy"):                                  # pgvector Vector
        value = value.to_numpy()
    if isinstance(value, np.ndarray):
        return "sha256:" + hashlib.sha256(np.asarray(value, dtype=np.float32).tobytes()).hexdigest()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10] if isinstance(value, dt.date) and not isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, (list, tuple)):
        return [normalise(v) for v in value]
    if isinstance(value, dict):
        return {k: normalise(v) for k, v in value.items()}
    return value


def prepared_rows(bundle: EvidenceBundle, table: str) -> list[dict[str, Any]]:
    rows = [dict(row) for row in bundle.records[table]]
    if table == "unit_embeddings":
        for row, vector in zip(rows, bundle.vectors):
            row["embedding"] = vector
    return rows


def verify_stored(conn: psycopg.Connection, bundle: EvidenceBundle) -> list[str]:
    """Differences between the stored schema content and the prepared records (empty list = identical)."""
    problems: list[str] = []
    for table in TABLES:
        expected = prepared_rows(bundle, table)
        if not expected:
            continue
        columns = list(expected[0])
        key = PRIMARY_KEYS[table]
        statement = sql.SQL("SELECT {} FROM {}.{}").format(sql.SQL(", ").join(sql.Identifier(checked(c)) for c in columns),
                                                            sql.Identifier(SCHEMA), sql.Identifier(checked(table)))
        with conn.cursor() as cur:
            cur.execute(statement)
            stored = {tuple(normalise(row[columns.index(k)]) for k in key): dict(zip(columns, row)) for row in cur.fetchall()}
        wanted = {tuple(normalise(row[k]) for k in key): row for row in expected}
        if set(stored) - set(wanted):
            problems.append(f"{table}: {len(set(stored) - set(wanted))} unrelated row(s)")
        if set(wanted) - set(stored):
            problems.append(f"{table}: {len(set(wanted) - set(stored))} prepared row(s) missing")
        for pk in set(stored) & set(wanted):
            differing = [c for c in columns if normalise(stored[pk][c]) != normalise(wanted[pk][c])]
            if differing:
                problems.append(f"{table} {pk}: differs in {differing}")
    return problems


# ----------------------------------------------------------------------------------------------------- write
def write_bundle(conn: psycopg.Connection, bundle: EvidenceBundle) -> dict[str, int]:
    """Insert missing rows and verify every stored row, atomically. Returns rows inserted per table."""
    if not write_approved():
        raise WriteNotApproved(f"set LAKEBASE_LIVE_TESTS=1 and {WRITE_OPT_IN}=1 for an approved run")
    guard_bundle(bundle)
    target = inspect_target(conn)
    if target["unexpected_relations"]:
        raise TargetNotClean(f"unexpected objects in {SCHEMA}: {target['unexpected_relations']}")
    verify_extensions(conn)
    register_vector(conn)
    end_read_transaction(conn)
    inserted: dict[str, int] = {}
    with conn.transaction():
        with conn.cursor() as cur:
            for statement in statements():
                cur.execute(statement)
            for table in TABLES:
                rows = prepared_rows(bundle, table)
                inserted[table] = 0
                if not rows:
                    continue
                columns = list(rows[0])
                statement = insert_statement(table, columns)
                for row in rows:
                    cur.execute(statement, [_adapt(c, row[c]) for c in columns])
                    inserted[table] += cur.rowcount
        problems = verify_stored(conn, bundle)
        if problems:
            raise ContentMismatch("; ".join(problems[:10]))          # leaving the block rolls everything back
    return inserted


def stored_matches(conn: psycopg.Connection, bundle: EvidenceBundle) -> list[str]:
    """Read-only re-verification outside any write (used after a rerun)."""
    register_vector(conn)
    problems = verify_stored(conn, bundle)
    end_read_transaction(conn)
    return problems
