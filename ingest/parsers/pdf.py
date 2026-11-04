"""Text PDFs through the validated corpus extractor (rag.ingestion.pipeline_v2.process, profile 3.0.0).

The extractor already provides the document hierarchy (sections with heading levels and paths, page ranges and
printed page labels), sanitised paragraphs with source spans, repaired tables, chunking strategy C, and, for
procurement plans, typed plan activities matched to contracts. This module only maps its output onto the shared
document model; nothing here re-implements extraction.
"""
from __future__ import annotations

from pathlib import Path

from rag.ingestion.pipeline_v2 import PROFILES, _strip, process

from ..hierarchy import topic_of
from ..model import Block, Chunk, Link, ParsedDocument, Record, Section, TableBlock
from ..reference import Reference, normalise_reference

EXTRACTION_VERSION, _, STACKED_CELLS = PROFILES["3.0.0"]
MATCH_CLASS = {"exact": ("E", 0.9), "normalized": ("EN", 0.85), "lot_or_sequence_suffix": ("P", 0.7)}  # (class, base confidence)
STATUS_ADJUSTMENT = {"amount_verified": 0.1, "exact_actual_date_supported": 0.1, "candidate": 0.0, "conflicting": -0.3}


def parse(path: Path, sha256: str, document_id: str, doc_type: str, project_id: str | None, ref: Reference) -> ParsedDocument:
    """Run the corpus extractor on a text PDF and map its output onto the shared document model."""
    project = ref.project(project_id) or {}
    # The extractor expects a manifest row; fields an inbox file does not have are left empty.
    row = {"document_id": document_id, "project_id": project_id or "", "local_path": str(path), "sha256": sha256,
           "country_code": project.get("country_code") or "", "doc_type": doc_type, "role": "inbox", "title": "",
           "document_date": "", "disclosure_date": "", "language": "", "version_type": "", "report_number": "", "guid": "",
           "source_url": "", "final_url_head": "", "downloaded_at": ""}
    out = _strip(process(row, {}, ref.contracts, ref.codes, ref.contract_index, ref.vocab, ref.lower_vocab,
                         EXTRACTION_VERSION, STACKED_CELLS))
    version = out["document_versions"][0]
    parsed = ParsedDocument(document_id=document_id, doc_type=doc_type, modality="pdf_text", source_sha256=sha256,
                            source_name=path.name, project_id=project_id, country_code=project.get("country_code"),
                            page_count=version.get("page_count"))
    parsed.sections = _sections(out["sections"])
    parsed.blocks = [Block(p["paragraph_id"], p["section_id"], p["kind"], p["text"], n, p["page_start"], p["page_end"])
                     for n, p in enumerate(out["paragraphs"])]
    parsed.tables = _tables(out["tables"], out["table_rows"])
    parsed.chunks = [Chunk(c["chunk_id"], c["section_id"], c["text"], c["token_count"], n, c["physical_page_start"],
                           c["physical_page_end"]) for n, c in enumerate(out["chunks_C"])]
    parsed.records = [_activity(a) for a in out["plan_activities"]]
    parsed.links = [_contract_link(link) for link in out["evidence_links"] if link.get("wb_contract_number")]
    if version["pdf"]["likely_scanned_pages"]:
        parsed.warnings.append(f"pages_without_text_layer: {version['pdf']['likely_scanned_pages'][:20]}")
    if version["validation_errors"]:
        parsed.warnings += [f"pdf_validation: {e}" for e in version["validation_errors"]]
    return parsed


def _sections(records: list[dict]) -> list[Section]:
    """The extractor's flat, ordered sections with their tree restored from the heading levels."""
    sections, stack = [], []
    for s in sorted(records, key=lambda r: r["order"]):
        level = int(s["heading_level"] or 0)
        while stack and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1] if stack and level > 0 else None
        section = Section(s["section_id"], parent.section_id if parent else None, level, s["heading_text"], s["section_path"],
                          s["order"], s["section_type"], topic_of(s["heading_text"]) or (parent.topic if parent else None),
                          s["physical_page_start"], s["physical_page_end"])
        sections.append(section)
        if level > 0:
            stack.append(section)
    return sections


def _tables(tables: list[dict], rows: list[dict]) -> list[TableBlock]:
    """Tables with their body rows in order; the header comes from the table's columns, header rows are skipped."""
    by_table: dict[str, list[dict]] = {}
    for r in rows:
        by_table.setdefault(r["table_id"], []).append(r)
    out = []
    for t in tables:
        body = sorted(by_table.get(t["table_id"], []), key=lambda r: r["row_index"])
        header = [str(c) for c in (t.get("columns") or [])]
        out.append(TableBlock(t["table_id"], t.get("section_id"), t.get("page"), t.get("caption"), header,
                              [[str(c or "") for c in r["cells_clean"]] for r in body if not r.get("is_header")]))
    return out


def _activity(a: dict) -> Record:
    """A procurement-plan activity as a plan_activity Record, with the extractor's cleaned fields."""
    fields = {"reference": a["activity_reference_printed"], "reference_norm": normalise_reference(a["activity_reference_printed"]),
              "description": a.get("description_clean"), "method": a.get("method_clean"), "category": a.get("category_family"),
              "review_type": a.get("review_type"), "status": a.get("process_status"),
              "estimated_amount_usd": a.get("estimated_amount_usd"), "actual_amount_usd": a.get("actual_amount_usd"),
              "planned_signing_date": a.get("signed_contract_planned"), "actual_signing_date": a.get("signed_contract_actual"),
              "plan_version_id": a.get("plan_version_id"), "page": a.get("pdf_page")}
    return Record("plan_activity", a["activity_id"], a.get("project_id") or None, fields)


def _contract_link(link: dict) -> Link:
    """The extractor's activity -> contract match: its match class mapped onto E / EN / P, and the confidence raised
    when amounts or dates agree independently and lowered when fields conflict."""
    match_class, base = MATCH_CLASS.get(str(link.get("match_class")), ("P", 0.6))
    confidence = round(min(1.0, max(0.1, base + STATUS_ADJUSTMENT.get(str(link.get("evidence_status")), 0.0))), 2)
    return Link("PLANNED_AS", f"activity:{link['activity_id']}", f"contract:{link['wb_contract_number']}", match_class, confidence,
                {"rule": "rag.ingestion.evidence.link", "agreeing_fields": link.get("independent_fields_agreeing") or [],
                 "disagreeing_fields": link.get("disagreeing_fields") or [], "evidence_status": link.get("evidence_status")})
