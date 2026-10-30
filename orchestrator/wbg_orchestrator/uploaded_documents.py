"""Documents uploaded after the corpus release: the ones the ingestion pipelines (`wbg-ingest watch` on a laptop, or the
Databricks job `wbg_document_ingest`) wrote to the Knowledge Graph schema `wbg_kg` with bge-m3 chunk vectors.

    question -> bge-m3 query vector -> pgvector cosine search over wbg_kg.chunk (HNSW) -> passages above a similarity floor

Documents already in the corpus release (pilot_v3) are skipped, so the same document is never cited twice. A hit is a
passage with its document, type, project and pages; its pages were not re-checked against the PDF, so the document
worker passes it on as unverified text (numbers in it are not approved figures).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Callable, ContextManager

import psycopg
from psycopg.rows import dict_row

PILOT_DOCUMENTS = Path(__file__).resolve().parents[2] / "rag" / "corpus_v3" / "processed" / "pilot_v3" / "shared" / "documents.jsonl"
MIN_SIMILARITY = 0.45
DOCUMENTS_SQL = "SELECT DISTINCT document_id FROM {schema}.chunk WHERE embedding IS NOT NULL"
SEARCH_SQL = """
SELECT c.chunk_id, c.document_id, c.text, c.page_start, c.page_end, d.doc_type, d.project_id, d.title,
       1 - (c.embedding <=> %(vector)s::public.halfvec) AS similarity
FROM {schema}.chunk c JOIN {schema}.document d USING (document_id)
WHERE c.embedding IS NOT NULL AND c.document_id = ANY(%(documents)s)
  AND (%(projects)s::text[] IS NULL OR d.project_id = ANY(%(projects)s::text[]))
ORDER BY c.embedding <=> %(vector)s::public.halfvec
LIMIT %(k)s
"""

Connect = Callable[[], ContextManager[psycopg.Connection]]


@lru_cache(maxsize=1)
def corpus_document_ids(path: Path = PILOT_DOCUMENTS) -> frozenset[str]:
    """The document ids of the corpus release (already searched by the main document search)."""
    if not path.is_file():
        return frozenset()
    return frozenset(json.loads(line)["document_id"] for line in path.read_text().splitlines() if line.strip())


def in_corpus(document_id: str, corpus: frozenset[str]) -> bool:
    """A graph document id names its corpus document directly ('34345370') or inside a longer id
    ('procurement_plans-40025990-p148850')."""
    return document_id in corpus or any(part in corpus for part in re.split(r"[-_]", document_id))


class UploadedDocumentSearch:
    def __init__(self, connect: Connect, encode_query: Callable[[str], list[float]], schema: str = "wbg_kg",
                 corpus: frozenset[str] | None = None, min_similarity: float = MIN_SIMILARITY):
        self._connect, self._encode, self._min, self._schema = connect, encode_query, min_similarity, schema
        self._corpus = corpus_document_ids() if corpus is None else corpus

    def uploaded(self) -> list[str]:
        """The graph's embedded documents that are not in the corpus release."""
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(DOCUMENTS_SQL.replace("{schema}", self._schema))
            return sorted(r["document_id"] for r in cur.fetchall() if not in_corpus(r["document_id"], self._corpus))

    def search(self, question: str, k: int = 3, projects: set[str] | None = None) -> list[dict]:
        """The k best passages of uploaded documents (most similar first), each above the similarity floor."""
        documents = self.uploaded()
        if not documents:
            return []
        vector = "[" + ",".join(f"{v:.6f}" for v in self._encode(question)) + "]"
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SEARCH_SQL.replace("{schema}", self._schema),
                        {"vector": vector, "documents": documents, "k": k, "projects": sorted(projects) if projects else None})
            rows = cur.fetchall()
        return [r for r in rows if float(r["similarity"]) >= self._min]
