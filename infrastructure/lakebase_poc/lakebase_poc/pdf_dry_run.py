"""Dry run of the real-PDF Lakebase ingestion: no connection, no write. Prints and saves exactly what WOULD be written.

    uv run --locked python -m lakebase_poc.pdf_dry_run        (from infrastructure/lakebase_poc/)
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

import numpy as np

from lakebase_poc import pdf_evidence, pdf_retrieval_check
from lakebase_poc.pdf_loader import MAX_RECORDS, WRITE_OPT_IN, guard_bundle
from lakebase_poc.pdf_schema import SCHEMA, TABLES, cleanup_statement, statements
from lakebase_poc.settings import PROJECT_DIR

# Measured in Step 2 on Lakebase (500 x 768-d rows): heap + TOAST and HNSW bytes per vector.
VECTOR_ROW_BYTES = (40_960 + 2_138_112) / 500
HNSW_BYTES_PER_VECTOR = 2_056_192 / 500
ROW_OVERHEAD = 1.5                                # tuple headers, page fill, primary-key and foreign-key indexes


def storage_estimate(bundle: pdf_evidence.EvidenceBundle) -> dict[str, Any]:
    text_bytes = {table: sum(len(json.dumps(row, default=str).encode()) for row in rows) for table, rows in bundle.records.items()}
    vectors = len(bundle.vectors)
    relational = sum(text_bytes.values()) * ROW_OVERHEAD
    vector_bytes = vectors * (VECTOR_ROW_BYTES + HNSW_BYTES_PER_VECTOR)
    return {"record_json_bytes_by_table": text_bytes, "relational_estimate_bytes": round(relational),
            "vectors_estimate_bytes": round(vector_bytes), "total_estimate_bytes": round(relational + vector_bytes),
            "basis": "record JSON size x 1.5 + per-vector heap/TOAST/HNSW bytes measured in Step 2"}


def retrieval_preview(bundle: pdf_evidence.EvidenceBundle) -> list[dict[str, Any]]:
    """Exact local ranking for the benchmark questions on this document (what Lakebase must reproduce)."""
    preview = []
    for query in pdf_retrieval_check.load_queries():
        top = pdf_retrieval_check.exact_reference(bundle, query)
        preview.append({"query_id": query.query_id, "question": query.text, "gold_pages": query.gold_pages,
                        "top5_unit_ids": [u for u, _ in top], "top5_distances": [round(d, 6) for _, d in top],
                        "gold_page_hit_at_5": pdf_retrieval_check.page_hit(bundle, [u for u, _ in top], query.gold_pages)})
    return preview


def dry_run() -> dict[str, Any]:
    bundle = pdf_evidence.build_bundle()
    guard_bundle(bundle)
    vectors = np.stack(bundle.vectors)
    lookup = {f: [(v["value_text"], v["value_numeric"], v["unit"], v["page"])
                  for v in pdf_evidence.lookup_indicator(bundle, "Roads constructed (Kilometers)", f)]
              for f in ("current_value", "current_date", "target_value", "target_period")}
    return {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "mode": "DRY RUN: no Lakebase connection, nothing written",
        "target": {"schema": SCHEMA, "tables": list(TABLES), "separate_from": "wbg_rag_poc (synthetic POC, untouched)",
                   "ddl": [s.as_string(None) for s in statements()]},
        "filters": {"project_id": pdf_evidence.PROJECT_ID, "document_id": pdf_evidence.DOCUMENT_ID,
                    "document_version_id": bundle.records["documents"][0]["document_version_id"],
                    "sha256": pdf_evidence.EXPECTED_SHA256, "volume_path": pdf_evidence.VOLUME_PATH,
                    "strategy": pdf_evidence.STRATEGY},
        "record_counts": {table: len(rows) for table, rows in bundle.records.items()},
        "total_records": sum(len(rows) for rows in bundle.records.values()), "write_bound": MAX_RECORDS,
        "embeddings": {"count": int(vectors.shape[0]), "dimensions": int(vectors.shape[1]),
                       "model": "Alibaba-NLP/gte-base-en-v1.5", "preprocessing": pdf_evidence.PREPROCESSING,
                       "source_cache": bundle.checks["embedding_cache"], "regenerated": False},
        "checks": bundle.checks,
        "exact_numeric_lookup_roads_constructed_km": lookup,
        "retrieval_preview_exact_local": retrieval_preview(bundle),
        "storage": storage_estimate(bundle),
        "write_requires": f"explicit approval, then LAKEBASE_LIVE_TESTS=1 and {WRITE_OPT_IN}=1 for that invocation",
        "rollback": {"statement": cleanup_statement().as_string(None),
                     "scope": "drops only the PDF POC schema; wbg_rag_poc, the vector extension and everything else stay"},
    }


def main() -> None:
    report = dry_run()
    out = PROJECT_DIR / "results" / f"pdf_dry_run_{report['generated_at_utc'].replace(':', '').replace('-', '')[:15]}Z.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    summary = {k: report[k] for k in ("mode", "filters", "record_counts", "total_records", "embeddings", "storage")}
    summary["checks_all_true"] = all(v for v in report["checks"]["identity"].values())
    print(json.dumps(summary, indent=1, default=str))
    print(f"full dry-run report -> {out.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
