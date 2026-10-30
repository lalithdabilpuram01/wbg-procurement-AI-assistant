"""The document flow of the ETL job: drop a PDF, Word or spreadsheet file into the inbox Volume and watch it move through
Bronze -> Silver -> Gold -> the app. The stages are the ones `python -m ingest run` uses (ingest/), one job task each:

  register  Bronze  every new file (by SHA-256) with its exact bytes      documents.bronze_document_files
  parse     Silver  format, type, project; sections, chunks, tables, rows documents.silver_documents (+ _sections,
                    (personal data masked); a file that cannot be used     _chunks, _tables, _records)
                    is quarantined with a reason
  enrich    Gold    entity links, proposed lesson cards, graph nodes/edges documents.gold_lesson_cards, gold_kg_nodes,
                                                                           gold_kg_edges, gold_mentions
  embed     Gold    one bge-m3 vector per chunk (the document search's     documents.gold_chunk_embeddings
                    model and revision)
  publish   App     the document's subgraph, chunk vectors and lesson      wbg_kg on the development branch: chunks in
                    cards to Lakebase (one transaction per document)       pgvector (searchable in chat), cards in the
                                                                           app's Review queue

Every task writes one row per file to documents.ingest_outcomes. Tasks hand work on by the job run id; the parsed
document travels as JSON in silver_documents. Re-dropping identical bytes is a no-op (`duplicate`).
"""
from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from ingest import PIPELINE_VERSION, detect
from ingest.layout import FOLDERS, Inbox
from ingest.model import ParsedDocument, Quarantined, document_from_dict

SCHEMA = "documents"
EMBEDDING_MODEL = "BAAI/bge-m3"                                          # the app's document search model ...
EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"          # ... at the exact snapshot it searches with
EMBEDDING_DIMENSION = 1024
OUTCOMES = ("job_run_id string, sha256 string, file_name string, stage string, status string, detail string, "
            "counts string, at timestamp")


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def table(catalog: str, name: str) -> str:
    return f"{catalog}.{SCHEMA}.{name}"


def write(spark: SparkSession, df: DataFrame, target: str, key: str | None = None) -> None:
    """Append rows, first removing the rows of the same documents (key column); the table is created on first use."""
    if not spark.catalog.tableExists(target):
        df.writeTo(target).using("delta").create()
        return
    keys = [r[0] for r in df.select(key).distinct().collect()] if key else []
    if keys:
        spark.sql(f"DELETE FROM {target} WHERE {key} IN ({', '.join(repr(k) for k in keys)})")
    df.writeTo(target).append()


def outcome(spark, catalog: str, run_id: str, rows: list[tuple[str, str, str, str, str, dict]]) -> None:
    """(sha256, file name, stage, status, detail, counts) per file, appended to ingest_outcomes and printed."""
    for r in rows:
        print(f"{r[2]:9s} {r[3]:12s} {r[1]}  {r[4] or ''} {json.dumps(r[5]) if r[5] else ''}")
    if rows:
        df = spark.createDataFrame([(run_id, *r[:5], json.dumps(r[5]), now()) for r in rows], OUTCOMES)
        write(spark, df, table(catalog, "ingest_outcomes"))


# -------------------------------------------------------------------------------------------- 1 register
def pending(inbox: Path) -> list[tuple[str, Path]]:
    """(folder, file) for every file waiting in the inbox type folders."""
    return [(folder.name, path) for folder, path in Inbox(inbox).pending()]


def register(spark: SparkSession, catalog: str, inbox: Path, run_id: str) -> int:
    """Bronze: fingerprint every file in the inbox; new bytes are stored as they arrived, known bytes are duplicates."""
    target = table(catalog, "bronze_document_files")
    known = {r[0] for r in spark.table(target).select("sha256").collect()} if spark.catalog.tableExists(target) else set()
    rows, outcomes, already = [], [], 0
    for folder, path in pending(inbox):                                  # ingested files stay in the inbox: skip them
        sha = detect.sha256_of(path)
        if sha in known:
            already += 1
            continue
        if sha in {r[3] for r in rows}:
            outcomes.append((sha, path.name, "register", "duplicate", "the same bytes are in the inbox twice", {}))
            continue
        uploaded = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        rows.append((str(path), folder, path.name, sha, path.stat().st_size, detect.sniff(path), uploaded, now(), run_id, path.read_bytes()))
        outcomes.append((sha, path.name, "register", "registered", folder, {"bytes": path.stat().st_size}))
    if rows:
        df = spark.createDataFrame(rows, "file_path string, folder string, file_name string, sha256 string, size_bytes bigint, format string, "
                                         "uploaded_at timestamp, registered_at timestamp, job_run_id string, content binary")
        write(spark, df, target)
    print(f"new files: {len(rows)}; already ingested: {already}")
    outcome(spark, catalog, run_id, outcomes)
    return len(rows)


# ----------------------------------------------------------------------------------------------- 2 parse
def parse_one(pipeline, folder: str, path: Path, sha: str) -> tuple[ParsedDocument | None, str | None, str | None]:
    """(document, None, None), or (None, reason, what to do) when the file is quarantined."""
    try:
        return pipeline.parse_file(FOLDERS[folder], path, sha), None, None
    except Quarantined as q:
        return None, q.reason, q.detail
    except Exception as error:                                           # an unexpected failure is quarantined, never lost
        return None, "pipeline_error", f"{type(error).__name__}: {str(error)[:300]}"


def parse(spark: SparkSession, catalog: str, run_id: str) -> int:
    """Silver: parse every file registered by this run into one document model, or quarantine it with a reason."""
    from ingest.pipeline import MemorySink, Pipeline
    from ingest.reference import load as load_reference

    if not spark.catalog.tableExists(table(catalog, "bronze_document_files")):
        print("nothing registered yet: nothing to parse")
        return 0
    files = spark.table(table(catalog, "bronze_document_files")).filter(F.col("job_run_id") == run_id) \
        .select("file_path", "folder", "file_name", "sha256").collect()
    pipeline = Pipeline(Inbox(), MemorySink(), reference=load_reference())
    docs, sections, chunks, tables, records, outcomes = [], [], [], [], [], []
    for f in files:
        doc, reason, detail = parse_one(pipeline, f.folder, Path(f.file_path), f.sha256)
        if doc is None:
            docs.append((f.sha256, None, f.folder, f.file_name, "quarantined", reason, detail, None, None, None, None, None, None, None,
                         0, 0, 0, 0, [], None, run_id, now()))
            outcomes.append((f.sha256, f.file_name, "parse", "quarantined", f"{reason}: {detail}", {}))
            continue
        counts = {"sections": len(doc.sections), "chunks": len(doc.chunks), "tables": len(doc.tables), "records": len(doc.records)}
        docs.append((f.sha256, doc.document_id, f.folder, f.file_name, "parsed", None, None, doc.doc_type, doc.modality, doc.title,
                     doc.project_id, doc.country_code, doc.document_date, doc.page_count, *counts.values(), list(doc.warnings),
                     json.dumps(asdict(doc), default=str), run_id, now()))
        sections += [(doc.document_id, s.section_id, s.parent_id, s.level, s.heading, s.path, s.kind, s.topic, s.page_start, s.page_end)
                     for s in doc.sections]
        chunks += [(doc.document_id, c.chunk_id, c.section_id, c.text, c.token_count, c.page_start, c.page_end) for c in doc.chunks]
        tables += [(doc.document_id, t.table_id, t.section_id, t.page, t.caption, t.header, len(t.rows), json.dumps(t.rows)) for t in doc.tables]
        records += [(doc.document_id, r.record_type, r.record_id, r.project_id, json.dumps(r.fields, default=str), r.source_row) for r in doc.records]
        outcomes.append((f.sha256, f.file_name, "parse", "parsed", f"{doc.doc_type} {doc.project_id or 'no project'}", counts))
    if docs:
        write(spark, spark.createDataFrame(docs, (
            "sha256 string, document_id string, folder string, file_name string, status string, reason string, detail string, doc_type string, "
            "modality string, title string, project_id string, country_code string, document_date string, page_count int, sections int, "
            "chunks int, tables int, records int, warnings array<string>, parsed_json string, job_run_id string, parsed_at timestamp")),
            table(catalog, "silver_documents"), "sha256")
    parts = {
        "silver_document_sections": (sections, "document_id string, section_id string, parent_id string, level int, heading string, "
                                               "path string, kind string, topic string, page_start int, page_end int"),
        "silver_document_chunks": (chunks, "document_id string, chunk_id string, section_id string, text string, token_count int, "
                                           "page_start int, page_end int"),
        "silver_document_tables": (tables, "document_id string, table_id string, section_id string, page int, caption string, "
                                           "header array<string>, row_count int, rows_json string"),
        "silver_document_records": (records, "document_id string, record_type string, record_id string, project_id string, "
                                             "fields_json string, source_row int"),
    }
    for name, (rows, schema) in parts.items():
        if rows:
            write(spark, spark.createDataFrame(rows, schema), table(catalog, name), "document_id")
    outcome(spark, catalog, run_id, outcomes)
    return sum(1 for d in docs if d[4] == "parsed")


# ---------------------------------------------------------------------------------------------- 3 enrich
def parsed_this_run(spark, catalog: str, run_id: str) -> list:
    """The documents this run parsed (none when nothing was ever parsed: the later tasks then do nothing)."""
    if not spark.catalog.tableExists(table(catalog, "silver_documents")):
        return []
    return spark.table(table(catalog, "silver_documents")).filter((F.col("job_run_id") == run_id) & (F.col("status") == "parsed")) \
        .select("sha256", "file_name", "folder", "parsed_json").collect()


def understand(doc: ParsedDocument, ref):
    """Entity links, proposed lesson cards and the document's subgraph (deterministic, so later tasks can redo it)."""
    from ingest.graph import build
    from ingest.lessons import cards_for
    from ingest.resolve import resolve

    res = resolve(doc, ref)
    lessons = cards_for(doc, ref)
    return res, lessons, build(doc, res, ref, lessons)


def enrich(spark: SparkSession, catalog: str, run_id: str) -> int:
    """Gold: what each parsed document says about projects, contracts and lessons."""
    from ingest.reference import load as load_reference

    ref = load_reference()
    cards, nodes, edges, mentions, outcomes = [], [], [], [], []
    for row in parsed_this_run(spark, catalog, run_id):
        doc = document_from_dict(json.loads(row.parsed_json))
        res, lessons, graph = understand(doc, ref)
        cards += [(doc.document_id, c.card_id, c.project_id, c.country, c.sector, c.doc_type, c.section, c.page_start, c.page_end,
                   c.headline, c.text, list(c.topics), list(c.stages), list(c.categories), c.status, run_id) for c in lessons]
        nodes += [(doc.document_id, n.node_id, n.label, json.dumps(n.props, default=str)) for n in graph.nodes.values()]
        edges += [(doc.document_id, e.edge_type, e.src, e.dst, e.match_class, float(e.confidence), json.dumps(e.evidence, default=str))
                  for e in graph.edges.values()]
        mentions += [(doc.document_id, m.chunk_id, m.entity_type, m.value, m.node_id, m.match_class, float(m.confidence)) for m in res.mentions]
        outcomes.append((row.sha256, row.file_name, "enrich", "enriched", doc.project_id or "",
                         {"lesson_cards": len(lessons), "nodes": len(graph.nodes), "edges": len(graph.edges), "mentions": len(res.mentions)}))
    parts = {
        "gold_lesson_cards": (cards, "document_id string, card_id string, project_id string, country string, sector string, doc_type string, "
                                     "section string, page_start int, page_end int, headline string, text string, topics array<string>, "
                                     "stages array<string>, categories array<string>, status string, job_run_id string"),
        "gold_kg_nodes": (nodes, "document_id string, node_id string, label string, props_json string"),
        "gold_kg_edges": (edges, "document_id string, edge_type string, src string, dst string, match_class string, confidence double, "
                                 "evidence_json string"),
        "gold_mentions": (mentions, "document_id string, chunk_id string, entity_type string, value string, node_id string, "
                                    "match_class string, confidence double"),
    }
    for name, (rows, schema) in parts.items():
        if rows:
            write(spark, spark.createDataFrame(rows, schema), table(catalog, name), "document_id")
    outcome(spark, catalog, run_id, outcomes)
    return len(outcomes)


# ----------------------------------------------------------------------------------------------- 4 embed
def bge_m3(batch_size: int = 8) -> Callable[[list[str]], list[list[float]]]:
    """bge-m3 on the job's CPU, at the revision the app searches with, so the vectors share one space."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBEDDING_MODEL, revision=EMBEDDING_REVISION, device="cpu")
    model.max_seq_length = 1024
    return lambda texts: model.encode(texts, normalize_embeddings=True, batch_size=batch_size,
                                      show_progress_bar=False).astype("float32").tolist()


def chunk_vectors(doc: ParsedDocument, encode: Callable[[list[str]], list[list[float]]]) -> list[list[float]]:
    """One normalised 1024-d vector per chunk, in chunk order; a wrong shape stops the task."""
    vectors = encode([c.text for c in doc.chunks]) if doc.chunks else []
    if len(vectors) != len(doc.chunks) or any(len(v) != EMBEDDING_DIMENSION for v in vectors):
        raise ValueError(f"{doc.document_id}: expected {len(doc.chunks)} vectors of {EMBEDDING_DIMENSION} values")
    return [list(map(float, v)) for v in vectors]


def embed(spark: SparkSession, catalog: str, run_id: str, encode: Callable | None = None) -> int:
    """Gold: the vector of every chunk parsed by this run, kept in Delta and published to pgvector by the next task."""
    rows = parsed_this_run(spark, catalog, run_id)
    if not rows:
        print("nothing new to embed")
        return 0
    encode = encode or bge_m3()
    vectors, outcomes = [], []
    for row in rows:
        doc = document_from_dict(json.loads(row.parsed_json))
        started = time.monotonic()
        values = chunk_vectors(doc, encode)
        vectors += [(doc.document_id, c.chunk_id, c.order, v, EMBEDDING_MODEL, EMBEDDING_REVISION, run_id)
                    for c, v in zip(doc.chunks, values)]
        outcomes.append((row.sha256, row.file_name, "embed", "embedded", f"{EMBEDDING_MODEL} ({EMBEDDING_DIMENSION}-d)",
                         {"chunks": len(values), "seconds": round(time.monotonic() - started, 1)}))
    if vectors:
        write(spark, spark.createDataFrame(vectors, "document_id string, chunk_id string, ord int, embedding array<float>, "
                                                    "model string, revision string, job_run_id string"),
              table(catalog, "gold_chunk_embeddings"), "document_id")
    outcome(spark, catalog, run_id, outcomes)
    return len(outcomes)


def stored_vectors(spark: SparkSession, catalog: str, run_id: str, doc: ParsedDocument) -> list[list[float]] | None:
    """The embed task's vectors for this document, in chunk order (None when it has none, e.g. no chunks)."""
    if not spark.catalog.tableExists(table(catalog, "gold_chunk_embeddings")):
        return None
    found = {r.chunk_id: list(r.embedding) for r in spark.table(table(catalog, "gold_chunk_embeddings"))
             .filter((F.col("job_run_id") == run_id) & (F.col("document_id") == doc.document_id)).collect()}
    if not doc.chunks or any(c.chunk_id not in found for c in doc.chunks):
        return None
    return [found[c.chunk_id] for c in doc.chunks]


# --------------------------------------------------------------------------------------------- 5 publish
def publish(spark: SparkSession, catalog: str, run_id: str, endpoint: str) -> int:
    """App: each document's subgraph, chunk vectors and proposed lesson cards to Lakebase (the store `python -m ingest
    run` writes): the chunks become searchable in chat, the cards wait in the Review queue. Review decisions are kept."""
    from etl.lakebase import pool as lakebase_pool
    from ingest.reference import load as load_reference
    from ingest.store import KG_SCHEMA, KnowledgeGraphStore

    rows = parsed_this_run(spark, catalog, run_id)
    if not rows:
        print("nothing new to publish")
        return 0
    ref, pool = load_reference(), lakebase_pool(endpoint, KG_SCHEMA, "documents")
    outcomes = []
    try:
        store = KnowledgeGraphStore(pool.connection)
        for row in rows:
            doc = document_from_dict(json.loads(row.parsed_json))
            res, lessons, graph = understand(doc, ref)
            vectors = stored_vectors(spark, catalog, run_id, doc)
            counts = store.load(doc, graph, res.mentions, vectors, {"sha256": row.sha256, "file_name": row.file_name, "folder": row.folder,
                                                                 "pipeline": PIPELINE_VERSION, "run_id": f"databricks-job-{run_id}",
                                                                 "modality": doc.modality, "warnings": doc.warnings, "rejected_rows": []},
                                lessons)
            where = "Lakebase wbg_kg: pgvector chunks + Review queue" if vectors else "Lakebase wbg_kg: Review queue (no vectors)"
            outcomes.append((row.sha256, row.file_name, "publish", "published", where, counts))
        reconciled = store.reconcile()
        print(f"cross-document links reconciled: {reconciled}")
    finally:
        pool.close()
    outcome(spark, catalog, run_id, outcomes)
    return len(outcomes)
