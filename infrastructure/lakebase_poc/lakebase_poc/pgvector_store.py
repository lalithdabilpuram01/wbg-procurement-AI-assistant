"""Standard pgvector operations on the isolated test schema. Identifiers are fixed constants composed with
psycopg.sql; all values are bound parameters. Every statement is idempotent, so a retry never duplicates data.

Only the standard `vector` extension is used. Lakebase Search / `lakebase_vector` is never enabled.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql

from lakebase_poc.synthetic import DIMENSIONS, Item

SCHEMA = "wbg_rag_poc"
TABLE = "synthetic_embeddings"
INDEX = "synthetic_embeddings_embedding_hnsw_cosine"
QUALIFIED_TABLE = sql.Identifier(SCHEMA, TABLE)


@dataclass(frozen=True)
class ServerInfo:
    """PostgreSQL version and the vector extensions the server offers and has installed."""
    version: str
    version_num: int
    vector_available: str | None        # default version offered by the server
    vector_installed: str | None
    lakebase_vector_installed: str | None


def _one(cur: psycopg.Cursor) -> tuple[Any, ...]:
    """The cursor's next row; raises LookupError when the query returned none."""
    row = cur.fetchone()
    if row is None:
        raise LookupError("query returned no row")
    return row


def server_info(conn: psycopg.Connection) -> ServerInfo:
    """Server version and the available and installed versions of `vector` and `lakebase_vector`."""
    with conn.cursor() as cur:
        cur.execute("SELECT version(), current_setting('server_version_num')::int")
        version, version_num = _one(cur)
        cur.execute("SELECT name, default_version, installed_version FROM pg_available_extensions "
                    "WHERE name = ANY(%s)", (["vector", "lakebase_vector"],))
        extensions = {name: (default, installed) for name, default, installed in cur.fetchall()}
    return ServerInfo(version=version, version_num=version_num,
                      vector_available=extensions.get("vector", (None, None))[0],
                      vector_installed=extensions.get("vector", (None, None))[1],
                      lakebase_vector_installed=extensions.get("lakebase_vector", (None, None))[1])


def enable_vector(conn: psycopg.Connection) -> None:
    """Standard pgvector in the current (isolated development) database only."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)


def create_table(conn: psycopg.Connection) -> None:
    """Create the test schema and the synthetic embeddings table if they do not exist."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA)))
        cur.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} ("
                            " item_id integer PRIMARY KEY,"
                            " label text NOT NULL,"
                            " embedding vector({}) NOT NULL,"
                            " created_at timestamptz NOT NULL DEFAULT now())").format(QUALIFIED_TABLE, sql.Literal(DIMENSIONS)))
    conn.commit()


def insert_items(conn: psycopg.Connection, items: list[Item]) -> int:
    """Insert missing rows only (ON CONFLICT DO NOTHING); returns the number of rows actually inserted."""
    statement = sql.SQL("INSERT INTO {} (item_id, label, embedding) VALUES (%s, %s, %s) "
                        "ON CONFLICT (item_id) DO NOTHING RETURNING item_id").format(QUALIFIED_TABLE)
    inserted = 0
    with conn.cursor() as cur:
        for item in items:
            cur.execute(statement, (item.item_id, item.label, item.embedding))
            inserted += cur.rowcount
    conn.commit()
    return inserted


def row_count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(QUALIFIED_TABLE))
        return int(_one(cur)[0])


def fetch_embedding(conn: psycopg.Connection, item_id: int) -> np.ndarray:
    """The stored embedding of one item as a float32 array."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT embedding FROM {} WHERE item_id = %s").format(QUALIFIED_TABLE), (item_id,))
        value = _one(cur)[0]                          # pgvector 0.5 returns a Vector object
        return np.asarray(value.to_numpy() if hasattr(value, "to_numpy") else value, dtype=np.float32)


def nearest(conn: psycopg.Connection, query: np.ndarray, k: int, force_index: bool = False,
            ef_search: int | None = None) -> list[tuple[int, float]]:
    """Top-k by cosine distance (`<=>`). force_index disables sequential scans and ef_search sets hnsw.ef_search,
    both for this transaction only."""
    statement = sql.SQL("SELECT item_id, embedding <=> %s AS distance FROM {} ORDER BY embedding <=> %s LIMIT %s"
                        ).format(QUALIFIED_TABLE)
    with conn.transaction(), conn.cursor() as cur:
        if force_index:
            cur.execute("SET LOCAL enable_seqscan = off")
        if ef_search is not None:
            cur.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(int(ef_search)),))
        cur.execute(statement, (query, query, k))
        return [(item_id, float(distance)) for item_id, distance in cur.fetchall()]


def create_hnsw_index(conn: psycopg.Connection) -> None:
    """HNSW with cosine operator class and pgvector defaults (m = 16, ef_construction = 64)."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING hnsw (embedding vector_cosine_ops)").format(
            sql.Identifier(INDEX), QUALIFIED_TABLE))
    conn.commit()


def analyze(conn: psycopg.Connection) -> None:
    """Refresh the planner statistics of the synthetic table."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("ANALYZE {}").format(QUALIFIED_TABLE))
    conn.commit()


def index_catalog(conn: psycopg.Connection) -> dict[str, Any] | None:
    """The index as recorded in the PostgreSQL catalog (pg_index, pg_class, pg_am, pg_opclass)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i.relname, am.amname, ix.indisvalid, ix.indisready, opc.opcname, pg_get_indexdef(i.oid)
            FROM pg_index ix
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_am am ON am.oid = i.relam
            JOIN pg_opclass opc ON opc.oid = ix.indclass[0]
            WHERE n.nspname = %s AND t.relname = %s AND i.relname = %s""", (SCHEMA, TABLE, INDEX))
        row = cur.fetchone()
    if row is None:
        return None
    name, method, valid, ready, opclass, definition = row
    return {"name": name, "access_method": method, "valid": valid, "ready": ready, "operator_class": opclass,
            "definition": definition}


def plan_nodes(conn: psycopg.Connection, query: np.ndarray, k: int, force_index: bool) -> list[str]:
    """Node types and index names of the EXPLAIN plan (plan only; the query is not executed)."""
    statement = sql.SQL("EXPLAIN (FORMAT JSON) SELECT item_id FROM {} ORDER BY embedding <=> %s LIMIT %s"
                        ).format(QUALIFIED_TABLE)
    with conn.transaction(), conn.cursor() as cur:
        if force_index:
            cur.execute("SET LOCAL enable_seqscan = off")
        cur.execute(statement, (query, k))
        plan = _one(cur)[0]
    plan = json.loads(plan) if isinstance(plan, str) else plan
    nodes: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        nodes.append(node["Node Type"] + (f" using {node['Index Name']}" if "Index Name" in node else ""))
        for child in node.get("Plans", []):
            walk(child)
    walk(plan[0]["Plan"])
    return nodes


def sizes(conn: psycopg.Connection) -> dict[str, int]:
    """Database, table, heap, all-index and HNSW index sizes in bytes."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT pg_database_size(current_database()), pg_total_relation_size({t}), "
                            "pg_relation_size({t}), pg_indexes_size({t}), pg_relation_size({i})").format(
            t=sql.Literal(f"{SCHEMA}.{TABLE}"), i=sql.Literal(f"{SCHEMA}.{INDEX}")))
        database, total, heap, indexes, hnsw = _one(cur)
    return {"database_bytes": database, "table_total_bytes": total, "table_heap_bytes": heap,
            "all_indexes_bytes": indexes, "hnsw_index_bytes": hnsw}
