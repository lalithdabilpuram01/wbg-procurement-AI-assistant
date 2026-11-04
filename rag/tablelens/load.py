"""Build and load TableLens for the 10 pilot PDFs.

    cd orchestrator && env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m rag.tablelens.load <step>

    plan                          offline: assemble the tables, write the cards, report what would be loaded
    load  --confirm-live-write    encode the cards (bge-m3), replace the release in Lakebase (one transaction)
    verify                        read-only: release record matches the cards and model; row counts; one sample query
Results go to rag/tablelens/results/.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from psycopg import sql

from rag.ingestion import config
from rag.vector_store import encoder
from rag.vector_store.index import DIMENSION, ReleaseRecord, vectors_sha256
from rag.vector_store.lakebase import open_pool

from . import DOCUMENTS, RELEASE
from .assemble import DATE, UNNAMED, LogicalTable, assemble, load_parts, parse_number
from .cards import DocumentInfo, card_text
from .store import REGISTRY, SCHEMA, TableEntry, cards_fingerprint, load as load_release, release_record

RESULTS = Path(__file__).resolve().parent / "results"
REFERENCE_SECTIONS = re.compile(r"front_matter|data sheet|project data", re.I)   # cover pages, key-value sheets
ACRONYM = re.compile(r"^[A-Z][A-Z0-9&/-]{1,9}$")
DATA_LIKE = re.compile(r"^\d{1,2}[-/ ][A-Za-z]{3}[-/ ]\d{2,4}$|^[A-Za-z]{3}/\d{4}$")    # 31-Aug-2017, Jun/2018


def documents() -> dict[str, DocumentInfo]:
    """Return DocumentInfo for each TableLens pilot document, read from the pilot_v3 shared outputs."""
    shared = config.OUTPUT_V3 / "shared"
    titles = {p["project_id"]: p["api"].get("project_title", "") for p in map(json.loads, (shared / "projects.jsonl").open())}
    out = {}
    for d in map(json.loads, (shared / "documents.jsonl").open()):
        if d["document_id"] in DOCUMENTS:
            out[d["document_id"]] = DocumentInfo(d["document_id"], d["doc_type"], d["project_id"],
                                                 titles.get(d["project_id"], ""), d["country_code"])
    return out


def worth_loading(table: LogicalTable) -> bool:
    """Structured, and not a cover page, data sheet, abbreviation list or a table whose 'headers' are data values
    (these only match questions by the project name in their card and crowd out real tables)."""
    if table.structure != "structured" or REFERENCE_SECTIONS.search(table.section or ""):
        return False
    headers = [c.header.strip() for c in table.columns]
    data_like = sum(bool(UNNAMED.match(h) or parse_number(h) is not None or DATE.match(h) or DATA_LIKE.match(h)) for h in headers)
    if data_like * 2 >= len(headers):
        return False
    acronyms = sum(bool(ACRONYM.match(r.cells[0].strip())) for r in table.rows if r.cells)
    return not (len(table.columns) == 2 and table.columns[1].type == "text" and acronyms >= 0.6 * len(table.rows))


def entries() -> list[TableEntry]:
    """Assemble the pilot documents' tables; return a TableEntry (table, document, card) for each one worth loading."""
    docs = documents()
    tables = assemble(*load_parts(config.OUTPUT_V3 / "shared", DOCUMENTS))
    return [TableEntry(t, docs[t.document_id], card_text(t, docs[t.document_id])) for t in tables if worth_loading(t)]


def plan() -> dict[str, Any]:
    """Offline dry run: count the tables and rows that would be loaded and show one example card."""
    items = entries()
    return {"structured_tables": len(items), "rows": sum(len(e.table.rows) for e in items),
            "flagged_rows": sum(r.quality != "ok" for e in items for r in e.table.rows),
            "by_document": Counter(e.doc.document_id for e in items), "cards_sha256": cards_fingerprint(items),
            "example_card": items[0].card if items else None}


def load() -> dict[str, Any]:
    """Encode the cards with bge-m3 and replace the TableLens release in Lakebase (a live write)."""
    items = entries()
    model = encoder.load_model()
    vectors = model.model.encode([e.card for e in items], batch_size=8, normalize_embeddings=True,
                                 show_progress_bar=False).astype("float32")
    record = ReleaseRecord(RELEASE, model.model_id, model.revision, DIMENSION, encoder.MAX_SEQUENCE_TOKENS, len(items),
                           cards_fingerprint(items), vectors_sha256(vectors), "tablelens: " + ",".join(DOCUMENTS))
    pool = open_pool(read_only=False, max_size=1, statement_timeout_ms=10 * 60_000)
    try:
        with pool.connection() as conn:
            rows = load_release(conn, record, items, vectors)
    finally:
        pool.close()
    return {"tables": len(items), "rows": rows, "release": RELEASE}


def verify() -> dict[str, Any]:
    """Read-only check: the release record exists, its cards fingerprint matches and every table's row count matches."""
    items = entries()
    pool = open_pool(read_only=True)
    try:
        with pool.connection() as conn:
            record = release_record(conn, RELEASE)
            registry = conn.execute(f"SELECT table_id, physical_name, n_rows FROM wbg_rag.{REGISTRY} WHERE release = %s",
                                    (RELEASE,)).fetchall()
            counted = {}
            for table_id, name, _ in registry:
                counted[table_id] = conn.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(SCHEMA), sql.Identifier(name))).fetchone()[0]
    finally:
        pool.close()
    expected = {e.table.table_id: len(e.table.rows) for e in items}
    return {"release_found": record is not None, "cards_match": bool(record and record.indexed_text_sha256 == cards_fingerprint(items)),
            "tables_registered": len(registry), "row_counts_match": counted == expected}


def main(argv: list[str]) -> int:
    """Command-line entry point; 'load' refuses to run without --confirm-live-write."""
    parser = argparse.ArgumentParser(description="TableLens build and load (10 pilot PDFs)")
    parser.add_argument("step", choices=["plan", "load", "verify"])
    parser.add_argument("--confirm-live-write", action="store_true")
    args = parser.parse_args(argv)
    if args.step == "load" and not args.confirm_live_write:
        parser.error("'load' writes to Lakebase; pass --confirm-live-write")
    result = {"plan": plan, "load": load, "verify": verify}[args.step]()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{args.step}.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps(result, indent=1, default=str)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
