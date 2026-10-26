"""Write representative sample records and chunks from the pipeline outputs (all text is already sanitised).

  python -m rag.ingestion.samples
"""
from __future__ import annotations

import json

from . import config


def main() -> None:
    out = config.OUTPUT

    def ld(name):
        return [json.loads(x) for x in (out / f"{name}.jsonl").read_text().splitlines()]

    def pick(rows, f):
        return next(r for r in rows if f(r))
    docs, vers, secs, chunks = ld("documents"), ld("document_versions"), ld("sections"), ld("chunks")
    pvs, acts, links, projects, pages = ld("plan_versions"), ld("plan_activities"), ld("evidence_links"), ld("projects"), ld("pages")
    records = {
        "project": projects[0],
        "document_PAD": pick(docs, lambda r: r["document_id"] == "26256705"),
        "document_version_PAD": pick(vers, lambda r: r["document_id"] == "26256705"),
        "page_with_printed_label": pick(pages, lambda r: r["document_id"] == "26256705" and r["page"] == 55),
        "section_PAD_procurement": pick(secs, lambda r: r["document_id"] == "26256705" and "Procurement" in r["heading_text"]),
        "plan_version_ET": pick(pvs, lambda r: r["document_id"] == "40025990"),
        "plan_activity_amount_verified": pick(acts, lambda r: r["package_reference_core"] == "ET-ERA-147679-CS-QCBS"),
        "evidence_link_amount_verified": pick(links, lambda r: r["wb_contract_number"] == "1672571"),
        "evidence_link_exact_date_lot": pick(links, lambda r: r["wb_contract_number"] == "1729238"),
        "evidence_link_candidate": pick(links, lambda r: r["document_id"] == "33761744" and r["evidence_status"] == "candidate"),
        "evidence_link_conflicting": pick(links, lambda r: r["evidence_status"] == "conflicting"),
        "evidence_link_unmatched_signed": pick(links, lambda r: r["evidence_status"] == "unmatched" and r["flags"][0].startswith("signed/completed")),
    }
    samples = {
        "PAD_text_procurement": pick(chunks, lambda c: c["document_id"] == "26256705" and c["chunk_type"] == "text" and "Procurement" in c["section_path"]),
        "ICR_text_fiduciary": pick(chunks, lambda c: c["document_id"] == "34345370" and c["chunk_type"] == "text" and "FIDUCIARY" in c["section_path"].upper()),
        "ICRR_text_fiduciary": pick(chunks, lambda c: c["document_id"] == "34407877" and "Fiduciary" in c["section_path"]),
        "ISR_text_status": pick(chunks, lambda c: c["document_id"] == "40089196" and "Implementation Status" in c["section_path"]),
        "PAD_table_results_framework": pick(chunks, lambda c: c["document_id"] == "26256705" and c["chunk_type"] == "table" and "Annex 1" in c["section_path"]),
        "PP_textual_part": pick(chunks, lambda c: c["document_id"] == "40009757" and c["chunk_type"] == "text"),
        "PP_table_row": pick(chunks, lambda c: c["chunk_type"] == "table_row" and "147679" in c["text"]),
        "staff_table_sanitised": pick(chunks, lambda c: c["chunk_type"] == "table" and c["sanitisation"].get("rule_staff_table")),
    }
    (out / "samples").mkdir(exist_ok=True)
    (out / "samples" / "sample_metadata_records.json").write_text(json.dumps(records, indent=1, sort_keys=True))
    (out / "samples" / "sample_chunks.json").write_text(json.dumps(samples, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
