"""Load the pilot_v3 bge-m3 vectors into Lakebase (schema wbg_rag) and verify them.

    cd orchestrator && env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m rag.vector_store.load <step>

    plan                          offline: corpus, vectors and fingerprints checked; nothing is sent
    load  --confirm-live-write    create the schema/tables and load the release in one transaction
    index --confirm-live-write    build the HNSW index and ANALYZE
    verify                        read-only: release row matches the corpus and model; Lakebase neighbours equal
                                  the exact in-memory neighbours for benchmark questions (unscoped and scoped)
    evaluate                      read-only: all benchmark question sets through build_retriever("pilot_v3"), compared
                                  with the in-memory bge-m3 evaluation (rag/release_v3/results/multilingual.json)

The vectors are the ones already encoded for the multilingual evaluation (rag/release_v3/results/experiments/bge-m3);
`plan` refuses them unless they were encoded for exactly this corpus. Results go to rag/vector_store/results/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from pgvector import HalfVector

from rag.embedding_cache.adapter import indexed_text_fingerprint
from rag.ingestion import config as ingestion_config
from rag.retrieval import core

from . import encoder
from .index import (DENSE_CANDIDATES, DIMENSION, LakebaseVectorIndex, ReleaseRecord, create_hnsw_index, create_tables, load_release,
                    nearest, stored_release, vectors_sha256)
from .lakebase import SCHEMA, open_pool

RELEASE = "pilot_v3"
EXPERIMENT = ingestion_config.REPO / "rag" / "release_v3" / "results" / "experiments"
RESULTS = Path(__file__).resolve().parent / "results"
VERIFY_QUESTIONS = 20
TOP_K = 10


def corpus() -> core.Corpus:
    return core.Corpus("C", include_tables=True, processed_dir=ingestion_config.OUTPUT_V3)


def release_inputs() -> tuple[core.Corpus, np.ndarray, ReleaseRecord]:
    """The corpus, its vectors and the release row; refuses vectors encoded for other units or another model."""
    units = corpus().units
    meta = json.loads((EXPERIMENT / "bge-m3.json").read_text())
    vectors = np.load(EXPERIMENT / "bge-m3.npy")
    fingerprint = indexed_text_fingerprint(units)
    problems = [name for name, ok in (("model", meta["model"] == encoder.MODEL_ID),
                                      ("indexed_text_sha256", meta["indexed_text_sha256"] == fingerprint),
                                      ("shape", vectors.shape == (len(units), DIMENSION))) if not ok]
    if problems:
        raise SystemExit(f"vectors do not match the pilot_v3 corpus: {problems}")
    record = ReleaseRecord(RELEASE, encoder.MODEL_ID, encoder.snapshot_path().name, DIMENSION, meta["max_sequence_tokens"],
                           len(units), fingerprint, vectors_sha256(vectors),
                           str(ingestion_config.OUTPUT_V3.relative_to(ingestion_config.REPO)))
    return units, vectors, record


def unit_rows(units, vectors: np.ndarray, docs: dict[str, dict]):
    """Yield one COPY row per unit (release, ids, document facts, pages, text and half-precision vector)."""
    for unit, vector in zip(units, vectors):
        doc = docs[unit.document_id]
        yield (RELEASE, unit.unit_id, unit.unit_type, unit.document_id, unit.project_id, doc.get("country_code"),
               doc.get("doc_type"), unit.page_start, unit.page_end, unit.section, unit.table_id, unit.activity_id,
               unit.text, HalfVector(vector))


def plan() -> dict[str, Any]:
    """Offline check: validate the corpus and vectors and report what would be loaded."""
    units, vectors, record = release_inputs()
    by_type: dict[str, int] = {}
    for unit in units:
        by_type[unit.unit_type] = by_type.get(unit.unit_type, 0) + 1
    return {"target": f"{SCHEMA}.units (production branch)", "release": record.__dict__, "units_by_type": by_type,
            "vector_megabytes": round(vectors.nbytes / 1e6, 1),
            "text_megabytes": round(sum(len(u.text.encode()) for u in units) / 1e6, 1)}


def load(replace: bool) -> dict[str, Any]:
    """Create the tables and load the release in one transaction (a live write); replace reloads an existing release."""
    units, vectors, record = release_inputs()
    docs = corpus().docs
    pool = open_pool(read_only=False, max_size=1, statement_timeout_ms=30 * 60_000)
    try:
        with pool.connection() as conn:
            create_tables(conn)
            started = time.perf_counter()
            written = load_release(conn, record, unit_rows(units, vectors, docs), replace=replace)
            stored = stored_release(conn, RELEASE)
            count = conn.execute(f"SELECT count(*) FROM {SCHEMA}.units WHERE release = %s", (RELEASE,)).fetchone()[0]
    finally:
        pool.close()
    return {"written": written, "rows_in_store": count, "release_row_matches": stored == record,
            "seconds": round(time.perf_counter() - started, 1)}


def index() -> dict[str, Any]:
    """Build the HNSW index on the loaded vectors and report its size (a live write)."""
    pool = open_pool(read_only=False, max_size=1, statement_timeout_ms=60 * 60_000)
    try:
        with pool.connection() as conn:
            started = time.perf_counter()
            create_hnsw_index(conn)
            size = conn.execute("SELECT pg_relation_size(%s)", (f"{SCHEMA}.units_embedding_hnsw",)).fetchone()[0]
    finally:
        pool.close()
    return {"index": "units_embedding_hnsw (hnsw, halfvec_cosine_ops)", "megabytes": round(size / 1e6, 1),
            "seconds": round(time.perf_counter() - started, 1)}


def _questions() -> list[str]:
    """Up to VERIFY_QUESTIONS questions drawn from the v1, v3, French and cross-language sets."""
    from rag.release_v3.multilingual import question_sets
    sets = question_sets()
    picked = sets["v1"][:6] + sets["v3"][:6] + sets["fr_v3"][:4] + sets["cross"][:4]
    return [q["question"] for q in picked][:VERIFY_QUESTIONS]


def verify() -> dict[str, Any]:
    """Read-only check: the release matches the corpus and model, and Lakebase neighbours match exact in-memory
    neighbours for sample questions, with and without a project scope."""
    units, vectors, record = release_inputs()
    model = encoder.load_model()
    project_of = np.array([u.project_id for u in units])
    pool = open_pool(read_only=True)
    try:
        lakebase = LakebaseVectorIndex(pool, RELEASE)
        lakebase.verify(record.indexed_text_sha256, record.units, record.model_id, model.revision)
        overlaps, scoped_overlaps, latencies = [], [], []
        with pool.connection() as conn:
            for question in _questions():
                query = model.encode_query(question)
                exact = np.argsort(-(vectors @ query))
                started = time.perf_counter()
                found = [unit_id for unit_id, _ in nearest(conn, RELEASE, query)][:TOP_K]   # as the app asks
                latencies.append(1000 * (time.perf_counter() - started))
                overlaps.append(len(set(found) & {units[i].unit_id for i in exact[:TOP_K]}) / TOP_K)
                scope = {units[exact[0]].project_id}
                in_scope = [i for i in exact if project_of[i] in scope][:TOP_K]
                found_scoped = [unit_id for unit_id, _ in nearest(conn, RELEASE, query, scope)][:TOP_K]
                scoped_overlaps.append(len(set(found_scoped) & {units[i].unit_id for i in in_scope}) / TOP_K)
    finally:
        pool.close()
    return {"release_verified": True, "questions": len(overlaps), "candidates_per_query": DENSE_CANDIDATES,
            "recall@10_vs_exact_unscoped": round(float(np.mean(overlaps)), 3),
            "recall@10_vs_exact_project_scoped": round(float(np.mean(scoped_overlaps)), 3),
            "median_query_ms": round(float(np.median(latencies)), 1)}


def evaluate() -> dict[str, Any]:
    """Run every question set through build_retriever('pilot_v3') and compare with the in-memory bge-m3 results."""
    from rag.release_v3.evaluate import run_benchmark, summarize
    from rag.release_v3.multilingual import question_sets
    from rag.retrieval.versions import build_retriever
    in_memory = json.loads((EXPERIMENT.parent / "multilingual.json").read_text())["models"]["bge-m3"]
    retriever = build_retriever(RELEASE)
    try:
        report = {}
        for name, questions in question_sets().items():
            summary = summarize(run_benchmark(retriever, questions, rerank=False))
            report[name] = {metric: {"lakebase": summary[metric], "in_memory": in_memory[name][metric]}
                            for metric in ("recall@5", "recall@10", "MRR")}
    finally:
        retriever.dense_index.close()
    return report


def main(argv: list[str]) -> int:
    """Command-line entry point; 'load' and 'index' refuse to run without --confirm-live-write."""
    parser = argparse.ArgumentParser(description="Load and verify the Lakebase vector store")
    parser.add_argument("step", choices=["plan", "load", "index", "verify", "evaluate"])
    parser.add_argument("--confirm-live-write", action="store_true")
    parser.add_argument("--replace", action="store_true", help="reload an already loaded release")
    args = parser.parse_args(argv)
    if args.step in ("load", "index") and not args.confirm_live_write:
        parser.error(f"'{args.step}' writes to Lakebase; pass --confirm-live-write")
    core.force_hugging_face_offline()
    result = {"plan": plan, "verify": verify, "evaluate": evaluate, "index": index,
              "load": lambda: load(args.replace)}[args.step]()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{args.step}.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
