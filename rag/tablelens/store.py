"""Lakebase storage for TableLens: physical tables, the schema registry with description embeddings, the release.

    pdf_tables.<physical name>      one table per structured PDF table:
                                    _row, _page, _group, _quality, then one typed column per PDF column
    wbg_rag.pdf_table_registry      table id -> physical name, document, project, country, type, section, caption,
                                    pages, columns (sql name, printed header, type, samples), card text, bge-m3 vector
    wbg_rag.releases                the release record (model, revision, card fingerprint): checked before querying

Loading replaces the release's physical tables and registry rows in one transaction (this schema only).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from rag.vector_store.index import VECTOR_TYPE, ReleaseRecord, stored_release

from .assemble import LogicalTable, parse_number
from .cards import DocumentInfo

SCHEMA, REGISTRY = "pdf_tables", "pdf_table_registry"
SQL_TYPES = {"text": "text", "number": "double precision", "date": "date"}
DDL = (
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS wbg_rag.{REGISTRY} (
        release text NOT NULL REFERENCES wbg_rag.releases ON DELETE CASCADE, table_id text NOT NULL,
        physical_name text NOT NULL, document_id text NOT NULL, project_id text NOT NULL, country_code text,
        doc_type text, project_title text, section text, caption text, pages int[] NOT NULL, n_rows int NOT NULL,
        part_ids text[] NOT NULL, columns jsonb NOT NULL, card_text text NOT NULL, embedding {VECTOR_TYPE} NOT NULL,
        PRIMARY KEY (release, table_id))""",
    f"CREATE INDEX IF NOT EXISTS pdf_table_registry_hnsw ON wbg_rag.{REGISTRY} USING hnsw (embedding halfvec_cosine_ops)",
)


@dataclass(frozen=True)
class TableEntry:
    table: LogicalTable
    doc: DocumentInfo
    card: str


def physical_name(table_id: str) -> str:
    return "t_" + re.sub(r"[^a-z0-9]+", "_", table_id.lower()).strip("_")[:58]


def cards_fingerprint(entries: Sequence[TableEntry]) -> str:
    return hashlib.sha256("\n".join(f"{e.table.table_id}\t{e.card}" for e in entries).encode()).hexdigest()


def _value(text: str, kind: str):
    if not text:
        return None
    if kind == "number":
        return parse_number(text)
    if kind == "date":
        try:
            return dt.date.fromisoformat(text[:10])
        except ValueError:
            return None
    return text


def load(conn: psycopg.Connection, record: ReleaseRecord, entries: Sequence[TableEntry], vectors: np.ndarray) -> int:
    """Replace the release (physical tables, registry rows, release row) in one transaction; returns rows written."""
    written = 0
    with conn.transaction():
        for statement in DDL:
            conn.execute(statement)
        old = conn.execute(f"SELECT physical_name FROM wbg_rag.{REGISTRY} WHERE release = %s", (record.release,)).fetchall()
        for (name,) in old:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(name)))
        conn.execute("DELETE FROM wbg_rag.releases WHERE release = %s", (record.release,))   # cascades to the registry
        values = record.__dict__
        conn.execute(f"INSERT INTO wbg_rag.releases ({', '.join(values)}) VALUES ({', '.join(['%s'] * len(values))})",
                     tuple(values.values()))
        for entry, vector in zip(entries, vectors):
            written += _load_table(conn, record.release, entry, vector)
    return written


def _load_table(conn: psycopg.Connection, release: str, entry: TableEntry, vector: np.ndarray) -> int:
    table, doc, name = entry.table, entry.doc, physical_name(entry.table.table_id)
    columns = [sql.SQL("{} {}").format(sql.Identifier(c.sql_name), sql.SQL(SQL_TYPES[c.type])) for c in table.columns]
    conn.execute(sql.SQL("CREATE TABLE {}.{} (_row int NOT NULL, _page int NOT NULL, _group text, _quality text NOT NULL, {})")
                 .format(sql.Identifier(SCHEMA), sql.Identifier(name), sql.SQL(", ").join(columns)))
    names = ["_row", "_page", "_group", "_quality", *(c.sql_name for c in table.columns)]
    insert = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(SCHEMA), sql.Identifier(name), sql.SQL(", ").join(map(sql.Identifier, names)),
        sql.SQL(", ").join(sql.Placeholder() * len(names)))
    rows = [(i + 1, r.page, r.group, r.quality, *(_value(v, c.type) for v, c in zip(r.cells, table.columns)))
            for i, r in enumerate(table.rows)]
    with conn.cursor() as cur:
        cur.executemany(insert, rows)
    conn.execute(
        f"""INSERT INTO wbg_rag.{REGISTRY} (release, table_id, physical_name, document_id, project_id, country_code, doc_type,
            project_title, section, caption, pages, n_rows, part_ids, columns, card_text, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::{VECTOR_TYPE})""",
        (release, table.table_id, name, doc.document_id, doc.project_id, doc.country_code, doc.doc_type, doc.project_title,
         table.section, table.caption, table.pages, len(table.rows), table.part_ids,
         Jsonb([{"sql_name": c.sql_name, "header": c.header, "type": c.type, "samples": c.samples} for c in table.columns]),
         entry.card, vector))
    return len(rows)


def release_record(conn: psycopg.Connection, release: str) -> ReleaseRecord | None:
    return stored_release(conn, release)
