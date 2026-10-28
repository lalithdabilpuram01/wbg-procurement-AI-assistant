"""Schema, release loading and nearest-neighbour search for the Lakebase vector store (schema `wbg_rag`).

    releases  1 row per loaded release: model, revision, dimension, unit count and fingerprints
    units     1 row per retrieval unit: metadata, indexed text and its embedding (halfvec(1024), HNSW cosine)

A release is loaded in one transaction and is immutable afterwards (reload = explicit replace). The retriever
verifies the release row against its local corpus and model before any search, so a mismatch fails instead of
silently returning wrong neighbours. Scoped searches use pgvector's iterative HNSW scan, so a project filter still
returns `limit` neighbours.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

import numpy as np
import psycopg

from .lakebase import SCHEMA

DIMENSION = 1024
VECTOR_TYPE = f"halfvec({DIMENSION})"   # half precision: halves storage (Free Edition cap 512 MB)
DENSE_CANDIDATES = 300                    # neighbours fetched per question for fusion with BM25
UNIT_COLUMNS = ("release", "unit_id", "unit_type", "document_id", "project_id", "country_code", "doc_type",
                "page_start", "page_end", "section", "table_id", "activity_id", "text", "embedding")
UNIT_TYPES = ("text", "text", "text", "text", "text", "text", "text", "int4", "int4", "text", "text", "text", "text",
              "halfvec")
TABLE_DDL = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.releases (
        release text PRIMARY KEY, model_id text NOT NULL, model_revision text NOT NULL, dimension int NOT NULL,
        max_sequence_tokens int NOT NULL, units int NOT NULL, indexed_text_sha256 text NOT NULL,
        vectors_sha256 text NOT NULL, corpus_dir text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now())""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.units (
        release text NOT NULL REFERENCES {SCHEMA}.releases ON DELETE CASCADE, unit_id text NOT NULL,
        unit_type text NOT NULL, document_id text NOT NULL, project_id text NOT NULL, country_code text, doc_type text,
        page_start int, page_end int, section text, table_id text, activity_id text, text text NOT NULL,
        embedding {VECTOR_TYPE} NOT NULL, PRIMARY KEY (release, unit_id))""",
    f"CREATE INDEX IF NOT EXISTS units_release_project ON {SCHEMA}.units (release, project_id)",
)
HNSW_DDL = (f"CREATE INDEX IF NOT EXISTS units_embedding_hnsw ON {SCHEMA}.units USING hnsw (embedding halfvec_cosine_ops) "
            "WITH (m = 32, ef_construction = 200)")


class ReleaseMismatchError(RuntimeError):
    """The stored release does not match the local corpus or model; searching it would return wrong neighbours."""


@dataclass(frozen=True)
class ReleaseRecord:
    release: str
    model_id: str
    model_revision: str
    dimension: int
    max_sequence_tokens: int
    units: int
    indexed_text_sha256: str
    vectors_sha256: str
    corpus_dir: str


def vectors_sha256(vectors: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(vectors, dtype=np.float32).tobytes()).hexdigest()


def create_tables(conn: psycopg.Connection) -> None:
    with conn.transaction():
        for statement in TABLE_DDL:
            conn.execute(statement)


def create_hnsw_index(conn: psycopg.Connection) -> None:
    """Built after loading (much faster than maintaining it during COPY)."""
    with conn.transaction():
        conn.execute("SELECT set_config('maintenance_work_mem', '512MB', true)")
        conn.execute(HNSW_DDL)
    conn.execute(f"ANALYZE {SCHEMA}.units")


def stored_release(conn: psycopg.Connection, release: str) -> ReleaseRecord | None:
    columns = ", ".join(ReleaseRecord.__dataclass_fields__)
    row = conn.execute(f"SELECT {columns} FROM {SCHEMA}.releases WHERE release = %s", (release,)).fetchone()
    return ReleaseRecord(*row) if row else None


def load_release(conn: psycopg.Connection, record: ReleaseRecord, rows: Iterable[Sequence], replace: bool = False) -> int:
    """Insert the release row and all its units in ONE transaction (binary COPY). Returns the units written."""
    written = 0
    with conn.transaction():
        if stored_release(conn, record.release):
            if not replace:
                raise ReleaseMismatchError(f"release {record.release!r} is already loaded; pass replace=True to reload it")
            conn.execute(f"DELETE FROM {SCHEMA}.releases WHERE release = %s", (record.release,))
        values = asdict(record)
        conn.execute(f"INSERT INTO {SCHEMA}.releases ({', '.join(values)}) VALUES ({', '.join(['%s'] * len(values))})",
                     tuple(values.values()))
        with conn.cursor().copy(f"COPY {SCHEMA}.units ({', '.join(UNIT_COLUMNS)}) FROM STDIN WITH (FORMAT BINARY)") as copy:
            copy.set_types(UNIT_TYPES)
            for row in rows:
                copy.write_row(row)
                written += 1
        if written != record.units:
            raise ReleaseMismatchError(f"wrote {written} units, expected {record.units}")
    return written


def nearest(conn: psycopg.Connection, release: str, query: np.ndarray, scope_projects: Iterable[str] | None = None,
            limit: int = DENSE_CANDIDATES) -> list[tuple[str, float]]:
    """(unit_id, cosine similarity), best first. A project scope is applied inside the index scan."""
    scope = sorted(scope_projects) if scope_projects else None
    where = "release = %s" + (" AND project_id = ANY(%s)" if scope else "")
    params: list = [query, release, *([scope] if scope else []), query, limit]
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true), set_config('hnsw.iterative_scan', 'relaxed_order', true)",
                     (str(min(max(2 * limit, 100), 1000)),))   # 2x: halfvec recall@10 1.0
        rows = conn.execute(f"SELECT unit_id, 1 - (embedding <=> %s::{VECTOR_TYPE}) FROM {SCHEMA}.units WHERE {where} "
                            f"ORDER BY embedding <=> %s::{VECTOR_TYPE} LIMIT %s", params).fetchall()
    return sorted(((unit_id, float(score)) for unit_id, score in rows), key=lambda r: -r[1])   # relaxed order -> exact


class LakebaseVectorIndex:
    """The dense side of the retriever, served from Lakebase. Built with an open (read-only) pool."""

    def __init__(self, pool, release: str):
        self._pool, self.release = pool, release

    def record(self) -> ReleaseRecord:
        with self._pool.connection() as conn:
            record = stored_release(conn, self.release)
        if record is None:
            raise ReleaseMismatchError(f"release {self.release!r} is not loaded in {SCHEMA}")
        return record

    def verify(self, indexed_text_sha256: str, units: int, model_id: str, model_revision: str) -> ReleaseRecord:
        """Fail fast unless the stored release was built from exactly this corpus and model."""
        record = self.record()
        expected = {"indexed_text_sha256": indexed_text_sha256, "units": units, "model_id": model_id,
                    "model_revision": model_revision}
        differing = sorted(k for k, v in expected.items() if getattr(record, k) != v)
        if differing:
            raise ReleaseMismatchError(f"release {self.release!r} differs from the local corpus/model in {differing}")
        return record

    def nearest(self, query: np.ndarray, scope_projects: Iterable[str] | None = None,
                limit: int = DENSE_CANDIDATES) -> list[tuple[str, float]]:
        with self._pool.connection() as conn:
            return nearest(conn, self.release, query, scope_projects, limit)

    def close(self) -> None:
        self._pool.close()
