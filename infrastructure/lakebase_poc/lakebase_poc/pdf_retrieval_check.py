"""Retrieval checks for the PDF POC. Offline: exact reference ranking with NumPy. Live (after approval only): the same
queries against Lakebase, exact and HNSW, with keyword/metadata filters and citations.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import psycopg
from psycopg import sql

from lakebase_poc.pdf_evidence import EvidenceBundle
from lakebase_poc.pdf_schema import SCHEMA, checked

QUERIES_FILE = Path(__file__).resolve().parents[1] / "results" / "pdf_poc_queries.npz"
TOP_K = 5


@dataclass(frozen=True)
class Query:
    query_id: str
    text: str
    gold_pages: list[int]
    vector: np.ndarray


def load_queries(path: Path = QUERIES_FILE) -> list[Query]:
    data = np.load(path)
    return [Query(str(i), str(q), json.loads(str(g)), v.astype(np.float32))
            for i, q, g, v in zip(data["ids"], data["questions"], data["gold_pages"], data["vectors"])]


def exact_reference(bundle: EvidenceBundle, query: Query, k: int = TOP_K) -> list[tuple[str, float]]:
    """Exact cosine ranking over this document's units (vectors are unit length)."""
    matrix = np.stack(bundle.vectors)
    similarity = matrix @ query.vector
    order = np.argsort(-similarity, kind="stable")[:k]
    units = bundle.records["retrieval_units"]
    return [(units[i]["unit_id"], float(1.0 - similarity[i])) for i in order]


def page_hit(bundle: EvidenceBundle, unit_ids: list[str], gold_pages: list[int]) -> bool:
    pages = {u["unit_id"]: set(range(u["page_start"], u["page_end"] + 1)) for u in bundle.records["retrieval_units"]}
    return any(pages[unit_id] & set(gold_pages) for unit_id in unit_ids)


# ------------------------------------------------------------------------------------------------- live
def _t(name: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(checked(SCHEMA)), sql.Identifier(checked(name)))


SCOPE_FILTERS = {"project_id": "d.project_id", "document_id": "d.document_id", "document_version_id": "d.document_version_id",
                 "strategy_version": "u.strategy_version", "unit_type": "u.unit_type"}


def search(conn: psycopg.Connection, query: np.ndarray, k: int = TOP_K, use_index: bool = False,
           ef_search: int | None = None, keyword: str | None = None, **scope: str) -> list[tuple[str, float]]:
    """Top-k by cosine distance with optional scope filters (project, document, version, strategy, unit type) and a
    keyword filter on the sanitised excerpt. Filter columns are a fixed allow-list; all values are bound parameters."""
    unknown = set(scope) - set(SCOPE_FILTERS)
    if unknown:
        raise ValueError(f"unsupported filters: {sorted(unknown)}")
    where, params = [], [query]
    for name, value in scope.items():
        where.append(sql.SQL(SCOPE_FILTERS[name] + " = %s"))
        params.append(value)
    if keyword is not None:
        where.append(sql.SQL("u.excerpt ILIKE %s"))
        params.append(f"%{keyword}%")
    statement = sql.SQL("SELECT e.unit_id, e.embedding <=> %s AS distance FROM {} e JOIN {} u USING (unit_id) "
                        "JOIN {} d ON d.document_version_id = u.document_version_id {} "
                        "ORDER BY e.embedding <=> %s LIMIT %s").format(
        _t("unit_embeddings"), _t("retrieval_units"), _t("documents"),
        sql.SQL("WHERE ") + sql.SQL(" AND ").join(where) if where else sql.SQL(""))
    if not conn.autocommit and conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.commit()                       # SET LOCAL below must be scoped to this search only
    with conn.transaction(), conn.cursor() as cur:
        if use_index:
            cur.execute("SET LOCAL enable_seqscan = off")
        if ef_search is not None:
            cur.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(int(ef_search)),))
        cur.execute(statement, [*params, query, k])
        return [(unit_id, float(distance)) for unit_id, distance in cur.fetchall()]


def citations(conn: psycopg.Connection, unit_ids: list[str]) -> list[dict]:
    """Citation records joined with the unit's source spans and the document's checksum."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT c.unit_id, c.project_id, c.document_id, c.document_version_id, c.page_start, c.page_end, "
                            "c.printed_page_label, c.section_path, c.source_url, c.volume_path, u.source_spans, d.sha256 "
                            "FROM {} c JOIN {} u USING (unit_id) JOIN {} d ON d.document_version_id = c.document_version_id "
                            "WHERE c.unit_id = ANY(%s)").format(_t("citations"), _t("retrieval_units"), _t("documents")),
                    (unit_ids,))
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def projects_present(conn: psycopg.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT DISTINCT project_id FROM {} UNION SELECT DISTINCT project_id FROM {}").format(
            _t("documents"), _t("citations")))
        return sorted(row[0] for row in cur.fetchall())
