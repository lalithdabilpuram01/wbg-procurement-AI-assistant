"""The Knowledge Graph store (Lakebase schema `wbg_kg`): load one document per transaction, reconcile across files,
and traverse.

* `load` replaces everything a document owns (its rows, owned nodes and asserted edges) and merges shared entity
  nodes, in ONE transaction: a file is either fully in the graph or not at all, and re-ingesting a corrected file
  never leaves stale sections or chunks behind.
* `reconcile` joins records that arrived in different files (a contract feed and a procurement plan, a notice and an
  activity) by project + normalised reference, and contracts to supervision/completion reports by temporal
  proximity. It is set-based SQL over indexed columns, so its cost grows with the data, not with the number of files.
* `lessons_for_contract` answers the assessment's example: from a contract row to the lessons sections of completion
  reports, of the same project first and then of projects in the same sector, ordered by distance in time.
"""
from __future__ import annotations

import datetime as dt
import json
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Callable, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from conversation.postgres_store import checked_schema
from lessons import store as lesson_store
from lessons.cards import LessonCard

from .graph import Graph
from .model import ParsedDocument
from .resolve import TEMPORAL_WINDOW, Mention

KG_SCHEMA = "wbg_kg"
SCHEMA_SQL = Path(__file__).with_name("sql") / "kg_schema.sql"
Connect = Callable[[], AbstractContextManager[psycopg.Connection]]
OWNED_TABLES = ("chunk", "section", "doc_table", "plan_activity", "contract", "notice", "entity_mention")
RESOLVER_EDGES = {"MENTIONS", "TEMPORALLY_NEAR"}              # found by entity resolution, not read from the file


class KnowledgeGraphStore:
    def __init__(self, connect: Connect, schema: str = KG_SCHEMA, vector_schema: str | None = "public"):
        self._connect, self._name = connect, checked_schema(schema)
        self._schema = sql.Identifier(self._name)
        self._vector_schema = vector_schema                              # where pgvector lives (None: store real[])

    @classmethod
    def for_dsn(cls, dsn: str, schema: str = KG_SCHEMA, vector_schema: str | None = None) -> "KnowledgeGraphStore":
        return cls(lambda: psycopg.connect(dsn), schema, vector_schema)

    # --------------------------------------------------------------------------------------------- schema
    def apply_schema(self) -> None:
        vector = f"{self._vector_schema}.halfvec(1024)" if self._vector_schema else "real[]"
        ddl = SCHEMA_SQL.read_text().replace("{schema}", self._name).replace("{vector}", vector)
        with self._connect() as conn, conn.transaction():
            conn.execute(ddl, prepare=False)
            conn.execute(lesson_store.apply_sql(self._name), prepare=False)
            if self._vector_schema:
                conn.execute(f"CREATE INDEX IF NOT EXISTS chunk_embedding_hnsw ON {self._name}.chunk USING hnsw "
                             f"(embedding {self._vector_schema}.halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)", prepare=False)

    def tables(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                "WHERE n.nspname = %s AND c.relkind IN ('r', 'p') ORDER BY 1", (self._name,)).fetchall()
        return [r[0] if isinstance(r, tuple) else r["relname"] for r in rows]

    # ----------------------------------------------------------------------------------------------- load
    def known(self, sha256: str) -> tuple[str, str] | None:
        """(status, pipeline version) already recorded for this exact file content (None when it is new)."""
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._q("SELECT status, pipeline FROM {s}.source_file WHERE sha256 = %s"), (sha256,))
            row = cur.fetchone()
        return (row["status"], row["pipeline"]) if row else None

    def load(self, doc: ParsedDocument, graph: Graph, mentions: Sequence[Mention], embeddings: Sequence[Sequence[float]] | None,
             source: dict[str, Any], lessons: Sequence[LessonCard] = ()) -> dict[str, int]:
        now = dt.datetime.now(dt.timezone.utc)
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            self._remove_document(cur, doc.document_id)
            if not doc.is_feed:
                self._document(cur, doc, now)
            self._records(cur, doc)
            self._mentions(cur, doc, mentions)
            self._chunks(cur, doc, embeddings)
            self._graph(cur, doc, graph, now)
            lesson_store.upsert(cur, self._name, lessons)                 # proposed cards; decisions are kept
            self._source(cur, source | {"document_id": doc.document_id, "status": "processed"}, now)
        return {"sections": len(doc.sections), "chunks": len(doc.chunks), "tables": len(doc.tables), "records": len(doc.records),
                "nodes": len(graph.nodes), "edges": len(graph.edges), "mentions": len(mentions), "lessons": len(lessons)}

    def record_source(self, source: dict[str, Any]) -> None:
        """A quarantined or duplicate file: recorded for lineage, nothing else is written."""
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            self._source(cur, source, dt.datetime.now(dt.timezone.utc))

    def record_run(self, run_id: str, started: dt.datetime, summary: dict[str, Any], pipeline: str) -> None:
        with self._connect() as conn, conn.transaction():
            conn.execute(self._q("INSERT INTO {s}.ingest_run (run_id, started_at, finished_at, pipeline, summary) VALUES (%s, %s, %s, %s, %s) "
                                 "ON CONFLICT (run_id) DO UPDATE SET finished_at = EXCLUDED.finished_at, summary = EXCLUDED.summary"),
                         (run_id, started, dt.datetime.now(dt.timezone.utc), pipeline, Jsonb(summary)))

    # ------------------------------------------------------------------------------------------ reconcile
    def reconcile(self) -> dict[str, int]:
        """Edges between records that arrived in different files (idempotent; existing edges are kept)."""
        now = dt.datetime.now(dt.timezone.utc)
        statements = {
            "PLANNED_AS": """
                INSERT INTO {s}.edge (edge_type, src, dst, match_class, confidence, evidence, document_id, origin, updated_at)
                SELECT 'PLANNED_AS', 'activity:' || a.activity_id, 'contract:' || c.contract_key, 'EN', 0.95,
                       jsonb_build_object('rule', 'same project + normalised reference', 'reference', a.reference), NULL, 'reconcile', %(now)s
                FROM {s}.plan_activity a JOIN {s}.contract c ON c.project_id = a.project_id AND c.reference_norm = a.reference_norm
                WHERE a.reference_norm IS NOT NULL
                  AND EXISTS (SELECT 1 FROM {s}.node WHERE node_id = 'activity:' || a.activity_id)
                  AND EXISTS (SELECT 1 FROM {s}.node WHERE node_id = 'contract:' || c.contract_key)
                ON CONFLICT DO NOTHING""",
            "NOTICE_OF": """
                INSERT INTO {s}.edge (edge_type, src, dst, match_class, confidence, evidence, document_id, origin, updated_at)
                SELECT 'NOTICE_OF', 'notice:' || n.notice_key, 'activity:' || a.activity_id, 'EN', 0.9,
                       jsonb_build_object('rule', 'same project + normalised reference', 'reference', n.reference), NULL, 'reconcile', %(now)s
                FROM {s}.notice n JOIN {s}.plan_activity a ON a.project_id = n.project_id AND a.reference_norm = n.reference_norm
                WHERE n.reference_norm IS NOT NULL
                  AND EXISTS (SELECT 1 FROM {s}.node WHERE node_id = 'notice:' || n.notice_key)
                  AND EXISTS (SELECT 1 FROM {s}.node WHERE node_id = 'activity:' || a.activity_id)
                ON CONFLICT DO NOTHING""",
            "TEMPORALLY_NEAR": """
                INSERT INTO {s}.edge (edge_type, src, dst, match_class, confidence, evidence, document_id, origin, updated_at)
                SELECT 'TEMPORALLY_NEAR', 'contract:' || c.contract_key, 'document:' || d.document_id, 'P',
                       round((1 - abs(d.document_date - c.signing_date)::numeric / %(window)s * 0.5), 2),
                       jsonb_build_object('rule', 'same project, signed within the report window',
                                          'days_report_minus_signing', d.document_date - c.signing_date), NULL, 'reconcile', %(now)s
                FROM {s}.contract c JOIN {s}.document d ON d.project_id = c.project_id
                WHERE d.doc_type IN ('ISR', 'ICR', 'ICRR') AND abs(d.document_date - c.signing_date) <= %(window)s
                  AND EXISTS (SELECT 1 FROM {s}.node WHERE node_id = 'contract:' || c.contract_key)
                ON CONFLICT DO NOTHING""",
        }
        added = {}
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            for name, statement in statements.items():
                cur.execute(self._q(statement), {"now": now, "window": TEMPORAL_WINDOW})
                added[name] = cur.rowcount
        return added

    # ------------------------------------------------------------------------------------------ traversal
    def lessons_for_contract(self, contract_key: str, limit: int = 10) -> list[dict[str, Any]]:
        """Lessons sections of completion reports linked to a contract: through its own project, then through projects
        that share a sector, nearest in time first."""
        query = """
            WITH c AS (SELECT project_id, signing_date FROM {s}.contract WHERE contract_key = %(key)s
                       UNION ALL
                       SELECT replace(e.dst, 'project:', ''), (n.props->>'signing_date')::date
                       FROM {s}.edge e JOIN {s}.node n ON n.node_id = e.src
                       WHERE e.edge_type = 'AWARDED_UNDER' AND e.src = 'contract:' || %(key)s
                       LIMIT 1),
                 peers AS (SELECT replace(p2.src, 'project:', '') AS project_id, 'same_sector' AS via
                           FROM c JOIN {s}.edge p1 ON p1.src = 'project:' || c.project_id AND p1.edge_type = 'IN_SECTOR'
                           JOIN {s}.edge p2 ON p2.dst = p1.dst AND p2.edge_type = 'IN_SECTOR' AND p2.src <> p1.src
                           UNION ALL SELECT project_id, 'same_project' FROM c)
            SELECT peers.via, d.project_id, d.document_id, d.doc_type, d.document_date, s.section_id, s.path, s.page_start,
                   abs(d.document_date - (SELECT signing_date FROM c LIMIT 1)) AS days_apart
            FROM peers JOIN {s}.document d ON d.project_id = peers.project_id AND d.doc_type IN ('ICR', 'ICRR')
            JOIN {s}.section s ON s.document_id = d.document_id AND s.topic = 'lessons'
            ORDER BY peers.via = 'same_project' DESC, days_apart NULLS LAST, s.ord
            LIMIT %(limit)s"""
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._q(query), {"key": contract_key, "limit": limit})
            return [{k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in r.items()} for r in cur.fetchall()]

    def neighbourhood(self, node_id: str, depth: int = 2, limit: int = 200) -> list[dict[str, Any]]:
        """Every node reachable within `depth` edges (either direction), with the path that reached it."""
        query = """
            WITH RECURSIVE walk(node_id, depth, path) AS (
                SELECT %(start)s::text, 0, ARRAY[%(start)s::text]
                UNION
                SELECT CASE WHEN e.src = w.node_id THEN e.dst ELSE e.src END, w.depth + 1,
                       w.path || (e.edge_type || ':' || CASE WHEN e.src = w.node_id THEN e.dst ELSE e.src END)
                FROM walk w JOIN {s}.edge e ON e.src = w.node_id OR e.dst = w.node_id
                WHERE w.depth < %(depth)s AND NOT (CASE WHEN e.src = w.node_id THEN e.dst ELSE e.src END) = ANY(w.path))
            SELECT DISTINCT ON (w.node_id) w.node_id, n.node_type, n.label, w.depth, w.path
            FROM walk w JOIN {s}.node n USING (node_id) ORDER BY w.node_id, w.depth LIMIT %(limit)s"""
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._q(query), {"start": node_id, "depth": depth, "limit": limit})
            return cur.fetchall()

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._q("SELECT node_type, count(*) AS n FROM {s}.node GROUP BY 1 ORDER BY 1"))
            nodes = {r["node_type"]: r["n"] for r in cur.fetchall()}
            cur.execute(self._q("SELECT edge_type, match_class, count(*) AS n FROM {s}.edge GROUP BY 1, 2 ORDER BY 1, 2"))
            edges = {f"{r['edge_type']}[{r['match_class']}]": r["n"] for r in cur.fetchall()}
            cur.execute(self._q("SELECT status, count(*) AS n FROM {s}.source_file GROUP BY 1"))
            files = {r["status"]: r["n"] for r in cur.fetchall()}
        return {"files": files, "nodes": nodes, "edges": edges}

    # ---------------------------------------------------------------------------------------- internals
    def _remove_document(self, cur: psycopg.Cursor, document_id: str) -> None:
        for table in OWNED_TABLES:
            cur.execute(self._q(f"DELETE FROM {{s}}.{table} WHERE document_id = %s"), (document_id,))
        cur.execute(self._q("DELETE FROM {s}.doc_table_row r WHERE NOT EXISTS (SELECT 1 FROM {s}.doc_table t WHERE t.table_id = r.table_id)"))
        cur.execute(self._q("DELETE FROM {s}.edge WHERE document_id = %s"), (document_id,))
        cur.execute(self._q("DELETE FROM {s}.edge e WHERE e.document_id IS NULL AND EXISTS (SELECT 1 FROM {s}.node n "
                            "WHERE n.document_id = %s AND n.node_id IN (e.src, e.dst))"), (document_id,))
        cur.execute(self._q("DELETE FROM {s}.node WHERE document_id = %s"), (document_id,))
        cur.execute(self._q("DELETE FROM {s}.document WHERE document_id = %s"), (document_id,))

    def _document(self, cur: psycopg.Cursor, doc: ParsedDocument, now: dt.datetime) -> None:
        cur.execute(self._q("INSERT INTO {s}.document VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
                    (doc.document_id, doc.doc_type, doc.modality, doc.title, doc.project_id, doc.country_code, doc.document_date,
                     doc.language, doc.page_count, doc.source_sha256, now))
        cur.executemany(self._q("INSERT INTO {s}.section VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
                        [(s.section_id, doc.document_id, s.parent_id, s.level, s.order, s.heading, s.path, s.kind, s.topic,
                          s.page_start, s.page_end) for s in doc.sections])
        cur.executemany(self._q("INSERT INTO {s}.doc_table VALUES (%s, %s, %s, %s, %s, %s, %s)"),
                        [(t.table_id, doc.document_id, t.section_id, t.page, t.caption, Jsonb(t.header), len(t.rows)) for t in doc.tables])
        cur.executemany(self._q("INSERT INTO {s}.doc_table_row VALUES (%s, %s, %s)"),
                        [(t.table_id, n, Jsonb(row)) for t in doc.tables for n, row in enumerate(t.rows, start=1)])

    def _records(self, cur: psycopg.Cursor, doc: ParsedDocument) -> None:
        for r in doc.records:
            f = r.fields
            if r.record_type == "plan_activity":
                cur.execute(self._q("INSERT INTO {s}.plan_activity VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                                    "ON CONFLICT (activity_id) DO NOTHING"),
                            (r.record_id, doc.document_id, r.project_id, f.get("reference"), f.get("reference_norm"), f.get("description"),
                             f.get("method"), f.get("category"), f.get("review_type"), f.get("status"), f.get("estimated_amount_usd"),
                             f.get("actual_amount_usd"), _text(f.get("planned_signing_date")), _text(f.get("actual_signing_date")),
                             r.source_row))
            elif r.record_type == "contract":
                cur.execute(self._q("INSERT INTO {s}.contract VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                                    "ON CONFLICT (contract_key) DO UPDATE SET document_id = EXCLUDED.document_id, "
                                    "amount_usd = EXCLUDED.amount_usd, signing_date = EXCLUDED.signing_date, extra = EXCLUDED.extra"),
                            (r.record_id, doc.document_id, r.project_id, f.get("wb_contract_number"), f.get("reference"),
                             f.get("reference_norm"), f.get("amount_usd"), f.get("signing_date"), f.get("method"), f.get("category"),
                             f.get("review_type"), f.get("supplier_name"), f.get("supplier_country"), f.get("description"),
                             Jsonb(f.get("extra") or {}), r.source_row))
            elif r.record_type == "notice":
                cur.execute(self._q("INSERT INTO {s}.notice VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                                    "ON CONFLICT (notice_key) DO UPDATE SET document_id = EXCLUDED.document_id, extra = EXCLUDED.extra"),
                            (r.record_id, doc.document_id, r.project_id, f.get("notice_id"), f.get("notice_type"), f.get("reference"),
                             f.get("reference_norm"), f.get("publication_date"), f.get("deadline_date"), f.get("method"),
                             f.get("category"), f.get("description"), Jsonb(f.get("extra") or {}), r.source_row))

    def _mentions(self, cur: psycopg.Cursor, doc: ParsedDocument, mentions: Sequence[Mention]) -> None:
        cur.executemany(self._q("INSERT INTO {s}.entity_mention VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"),
                        [(m.chunk_id, doc.document_id, m.entity_type, m.value, m.node_id, m.match_class, m.confidence, m.start, m.end)
                         for m in mentions])

    def _chunks(self, cur: psycopg.Cursor, doc: ParsedDocument, embeddings: Sequence[Sequence[float]] | None) -> None:
        cast = f"::{self._vector_schema}.halfvec" if self._vector_schema else ""
        vectors: list[Sequence[float] | None] = list(embeddings) if embeddings is not None else [None] * len(doc.chunks)
        cur.executemany(self._q(f"INSERT INTO {{s}}.chunk VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s{cast})"),
                        [(c.chunk_id, doc.document_id, c.section_id, c.order, c.text, c.token_count, c.page_start, c.page_end,
                          _vector(v, bool(self._vector_schema))) for c, v in zip(doc.chunks, vectors)])

    def _graph(self, cur: psycopg.Cursor, doc: ParsedDocument, graph: Graph, now: dt.datetime) -> None:
        cur.executemany(self._q("INSERT INTO {s}.node VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (node_id) DO UPDATE SET "
                                "props = {s}.node.props || EXCLUDED.props, label = EXCLUDED.label, updated_at = EXCLUDED.updated_at"),
                        [(n.node_id, n.node_type, n.label[:500], Jsonb(n.props), doc.document_id if n.owned else None, now)
                         for n in graph.nodes.values()])
        cur.executemany(self._q("INSERT INTO {s}.edge VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (edge_type, src, dst) DO UPDATE "
                                "SET confidence = GREATEST({s}.edge.confidence, EXCLUDED.confidence), updated_at = EXCLUDED.updated_at"),
                        [(e.edge_type, e.src, e.dst, e.match_class, e.confidence, Jsonb(e.evidence), doc.document_id,
                          "resolver" if e.edge_type in RESOLVER_EDGES else "parser", now) for e in graph.edges.values()])

    def _source(self, cur: psycopg.Cursor, source: dict[str, Any], now: dt.datetime) -> None:
        cur.execute(self._q("INSERT INTO {s}.source_file VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT (sha256) DO UPDATE SET status = EXCLUDED.status, reason = EXCLUDED.reason, "
                            "document_id = EXCLUDED.document_id, warnings = EXCLUDED.warnings, rejected_rows = EXCLUDED.rejected_rows, "
                            "run_id = EXCLUDED.run_id, pipeline = EXCLUDED.pipeline, processed_at = EXCLUDED.processed_at"),
                    (source["sha256"], source["file_name"], source["folder"], source.get("format"), source.get("modality"),
                     source.get("size_bytes"), source.get("document_id"), source["status"], source.get("reason"),
                     Jsonb(source.get("warnings", [])), Jsonb(source.get("rejected_rows", [])[:200]), source.get("run_id"),
                     source["pipeline"], now))

    def _q(self, template: str) -> sql.Composed:
        return sql.SQL(template).format(s=self._schema)


def _vector(values: Sequence[float] | None, pgvector: bool) -> Any:
    if values is None:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]" if pgvector else [float(v) for v in values]


def _text(value: Any) -> str | None:
    return None if value in (None, "") else (value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else str(value))
