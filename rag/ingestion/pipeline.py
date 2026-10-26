"""Stage-1 ingestion pipeline over the local corpus (pilot: the 10 PDFs in rag/corpus_v3/CORPUS_MANIFEST_FIRST10.csv).

  python -m rag.ingestion.pipeline            # process (reuses per-document cache when nothing changed)
  python -m rag.ingestion.pipeline --force    # ignore the cache

Deterministic: ids are derived from document ids, page/table/row positions and hashes; records carry no timestamps;
output files are sorted and written atomically. Restartable / idempotent: each document's records are cached under
by_document/<document_id>.json with a key of (PDF SHA-256, extraction version, code hash, contracts-snapshot SHA-256).
Outputs (rag/corpus_v3/processed/pilot_v1/): projects, documents, document_versions, pages, sections, chunks,
plan_versions, plan_activities, evidence_links (*.jsonl) and run_manifest.json. Original PDFs and manifests are only read.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import EXTRACTION_VERSION, config
from . import chunking, contracts_snapshot, evidence, plan_tables, sections, text_extract
from .pdf_validate import project_ids_in_text, validate
from .sanitize import PERSONNEL, sanitize

ENTITIES = ("projects", "documents", "document_versions", "pages", "sections", "chunks", "plan_versions", "plan_activities", "evidence_links")


def code_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.read_bytes())
    h.update((config.REPO / "rag" / "module1_discovery" / "plan_text_rows.py").read_bytes())
    return h.hexdigest()[:16]


def _section_type(doc_type: str, sec: dict) -> str:
    t = sec["heading_text"].upper()
    if sec["heading_kind"] == "plan_table":
        return "plan_table_" + t.replace(" ", "_")
    if sec["heading_kind"] == "front_matter":
        return "front_matter"
    if "DATA SHEET" in t:
        return "data_sheet"
    if sec["heading_kind"] == "annex" or sec["section_path"].upper().startswith("ANNEX"):
        return "annex"
    if doc_type == "PP":
        return "plan_textual_part" if t != "GENERAL INFORMATION" else "plan_general_information"
    if doc_type == "ISR":
        return "isr_template_section"
    return "narrative"


def process_document(row: dict, scope: dict, contracts: list[dict], codes: set[str], idx: dict) -> dict:
    did, pid = row["document_id"], row["project_id"]
    path = config.REPO / row["local_path"]
    val = validate(path, row["sha256"])
    pages = text_extract.extract(path)
    inferred = text_extract.infer_labels(pages)
    labels = {p.number: p.printed_label for p in pages}
    full = "\n".join(ln.text for p in pages for ln in p.lines)
    nn = lambda v: v if v not in ("", None) else None  # noqa: E731
    doc = {"document_id": did, "project_id": pid, "country_code": row["country_code"], "doc_type": row["doc_type"], "role": row["role"],
           "api": {"title": nn(row["title"]), "wds_document_date": nn(row["document_date"]), "wds_disclosure_date": nn(row["disclosure_date"]),
                   "language": nn(row["language"]), "wds_version_type": nn(row["version_type"]), "report_number": nn(row["report_number"]),
                   "guid": nn(row["guid"]), "source_url": nn(row["source_url"]), "final_url": nn(row["final_url_head"]),
                   "txt_url": nn(scope.get("txt_url")), "isr_sequence": nn(scope.get("isr_sequence"))},
           "pdf": {"project_ids_in_text": project_ids_in_text(full), "own_project_id_in_text": pid in project_ids_in_text(full),
                   "page_count": val.get("page_count"), "outline_entries": val.get("pdf_outline_entries"), "producer": val.get("pdf_producer"),
                   "creation_date": val.get("pdf_creation_date")},
           "inferred": {}, "extraction_version": EXTRACTION_VERSION}
    version = {"document_version_id": f"{did}:{val['sha256'][:12]}", "document_id": did, "sha256": val["sha256"], "size_bytes": val["size_bytes"],
               "page_count": val.get("page_count"), "local_path": row["local_path"], "validation_errors": val["errors"],
               "download": {"source_url": nn(row["source_url"]), "downloaded_at": nn(row["downloaded_at"]), "manifest_sha256": nn(row["sha256"])},
               "pdf": {"pages_with_text": sum(p.chars >= 100 for p in pages), "low_text_pages": [p.number for p in pages if p.chars < 100],
                       "likely_scanned_pages": [p.number for p in pages if p.chars < 100 and p.image_cover > 0.5],
                       "toc_pages": [p.number for p in pages if p.is_toc], "printed_labels_found": sum(bool(p.printed_label) for p in pages),
                       "boilerplate_lines_removed": sum(len(p.removed_lines) for p in pages)},
               "supersedes_document_id": None, "extraction_version": EXTRACTION_VERSION}
    page_recs = [{"document_id": did, "page": p.number, "printed_page_label": p.printed_label, "printed_page_label_inferred": inferred.get(p.number),
                  "chars": p.chars, "image_cover": p.image_cover, "likely_scanned": p.chars < 100 and p.image_cover > 0.5, "is_toc": p.is_toc,
                  "tables_detected": len(p.tables), "boilerplate_lines_removed": len(p.removed_lines),
                  "orientation": "landscape" if p.width > p.height else "portrait"} for p in pages]
    heads = sections.detect(pages, row["doc_type"])
    secs = sections.build_sections(did, pages, heads)
    lines = [ln for p in pages for ln in p.lines]
    starts = [((lines[s["_line_range"][0] - 1].page, lines[s["_line_range"][0] - 1].y0) if s["heading_level"] else (1, -1.0)) for s in secs]
    chunks: list[dict] = []
    for k, s in enumerate(secs):
        s["section_type"] = _section_type(row["doc_type"], s)
        a, b = s["_line_range"]
        s["contains_personal_data_indicators"] = bool(PERSONNEL.search(" ".join(ln.text for ln in lines[a:b]) + " " + s["heading_text"]))
        s["printed_page_label_start"] = labels.get(s["physical_page_start"]) or inferred.get(s["physical_page_start"])
        s["printed_page_label_end"] = labels.get(s["physical_page_end"]) or inferred.get(s["physical_page_end"])
        s["heading_text"] = sanitize(s["heading_text"], personnel_context=False)[0]   # headings: e-mail/phone only
        s["section_path"] = sanitize(s["section_path"], personnel_context=False)[0]
        if s["section_type"].startswith("plan_table_"):
            continue  # STEP tables become plan activities + row chunks, not prose
        units = chunking.units_for_section(pages, s, starts[k + 1] if k + 1 < len(secs) else None, starts[k])
        chunks.extend(chunking.make_chunks(doc, s, units, labels, len(chunks), inferred))
    out = {"documents": [doc], "document_versions": [version], "pages": page_recs, "sections": secs, "chunks": chunks,
           "plan_versions": [], "plan_activities": [], "evidence_links": []}
    if row["doc_type"] == "PP":
        facts = plan_tables.header_facts(path)
        prefixes = {c["borrower_contract_reference"][:2] for c in contracts if c["project_id"] == pid and c["borrower_contract_reference"]} | {row["country_code"]}
        prefixes = {p for p in prefixes if p.isalpha() and len(p) == 2}
        acts = plan_tables.activities(did, "PENDING", path, prefixes, codes)
        agency = plan_tables.reference_agency_code(acts)
        revision = max(facts["revised_plan_dates"]) if facts["revised_plan_dates"] else None
        stream = f"{pid}:{agency}" if agency else f"{pid}:document:{did}"
        pv_id = f"{stream}:{revision or 'undated:' + did}"
        out["plan_versions"].append({
            "plan_version_id": pv_id, "plan_stream_id": stream, "document_id": did, "project_id": pid,
            "pdf": facts | {"revision_date": revision}, "api": {"wds_document_date": doc["api"]["wds_document_date"],
                                                                 "wds_disclosure_date": doc["api"]["wds_disclosure_date"]},
            "inferred": {"reference_agency_code": agency, "plan_stream_basis": "agency segment of the STEP activity references in this PDF"},
            "previous_version_id": None,
            "version_rule": "versions are ordered only within one plan_stream_id; different executing agencies are never treated as revisions",
            "activities": len(acts), "extraction_version": EXTRACTION_VERSION})
        for act in acts:
            act["description"], dcounts = sanitize(act.pop("description_text", "") or "")
            act["description"] = act["description"] or None
            act["description_sanitisation"] = {k: v for k, v in dcounts.items() if v}
            act["plan_version_id"] = pv_id
            act["plan_stream_id"] = stream
            act["project_id"] = pid
            links = evidence.link(pid, act, idx)
            out["evidence_links"].extend(links)
            out["plan_activities"].append(act)
        sec_by_page = {}
        for s in secs:
            if s["section_type"].startswith("plan_table_"):
                for pg in range(s["physical_page_start"], s["physical_page_end"] + 1):
                    sec_by_page.setdefault(pg, s)
        for act in acts:
            s = sec_by_page.get(act["pdf_page"]) or secs[-1]
            txt = (f"Procurement plan {did} (plan stream {stream}, revision {revision}) - {act['section'] or 'section unknown'}: "
                   f"activity {act['activity_reference_printed']} - {act['description'] or 'no description'}; review {act['review_type']}; method {act['method_printed']}; "
                   f"status {act['process_status']}; estimated US$ {act['estimated_amount_usd']}; actual US$ {act['actual_amount_usd']}; "
                   f"signed contract planned {act['signed_contract_planned']}, actual {act['signed_contract_actual']}.")
            clean, counts = sanitize(txt)
            out["chunks"].append({
                "chunk_id": f"{did}:r{act['pdf_page']:03d}{(act['table_index'] or 0):02d}{(act['row_index'] or 0):03d}", "document_id": did,
                "project_id": pid, "country_code": row["country_code"], "doc_type": "PP", "section_id": s["section_id"],
                "section_path": s["section_path"], "chunk_type": "table_row", "table_index": act["table_index"], "activity_id": act["activity_id"],
                "physical_page_start": act["pdf_page"], "physical_page_end": act["pdf_page"],
                "printed_page_label_start": labels.get(act["pdf_page"]), "printed_page_label_end": labels.get(act["pdf_page"]),
                "text": clean, "text_sha256": hashlib.sha256(clean.encode()).hexdigest(), "token_count": chunking.count_tokens(clean),
                "tokenizer": chunking.tokenizer_name(), "sanitisation": {k: v for k, v in counts.items() if v}})
    for c in out["chunks"]:
        c["extraction_version"] = EXTRACTION_VERSION
    return out


def _strip(rec):
    if isinstance(rec, dict):
        return {k: _strip(v) for k, v in rec.items() if not k.startswith("_")}
    if isinstance(rec, list):
        return [_strip(v) for v in rec]
    return rec


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def run(force: bool = False, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or config.OUTPUT
    (out_dir / "by_document").mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with config.CORPUS_MANIFEST.open(newline="") as fh:
        corpus = list(csv.DictReader(fh))
    with config.SCOPE_MANIFEST.open(newline="") as fh:
        scope = {r["document_id"]: r for r in csv.DictReader(fh)}
    with config.PROJECT_MANIFEST.open(newline="") as fh:
        projects = {r["project_id"]: r for r in csv.DictReader(fh)}
    contracts, snap_meta = contracts_snapshot.load()
    codes = evidence.method_codes(contracts)
    idx = evidence.index_contracts(contracts, codes)
    chash = code_hash()
    agg: dict[str, list] = {e: [] for e in ENTITIES}
    status = {}
    for row in sorted(corpus, key=lambda r: r["document_id"]):
        key = {"sha256": row["sha256"], "extraction_version": EXTRACTION_VERSION, "code_hash": chash,
               "contracts_sha256": snap_meta["sha256"] if row["doc_type"] == "PP" else None}
        cache = out_dir / "by_document" / f"{row['document_id']}.json"
        recs = None
        if cache.exists() and not force:
            cached = json.loads(cache.read_text())
            if cached.get("cache_key") == key:
                recs, status[row["document_id"]] = cached["records"], "cached"
        if recs is None:
            try:
                recs = _strip(process_document(row, scope.get(row["document_id"], {}), contracts, codes, idx))
                _atomic_write(cache, json.dumps({"cache_key": key, "records": recs}, sort_keys=True, default=str))
                status[row["document_id"]] = "processed"
            except Exception as exc:  # noqa: BLE001  (one failing document must not stop the run)
                status[row["document_id"]] = f"failed: {type(exc).__name__}: {str(exc)[:200]}"
                continue
        for e in ENTITIES[1:]:
            agg[e].extend(recs.get(e, []))
    for pid in sorted({r["project_id"] for r in corpus}):
        p = projects.get(pid, {})
        agg["projects"].append({"project_id": pid, "source": "rag_scope_v3_35_projects/PROJECT_SELECTION_35.csv (structured, not from PDFs)",
                                "api": {k: (p.get(k) or None) for k in ("project_title", "country_code", "country_name", "region", "income_group_fy2026",
                                                                       "sector", "approval_fy", "status")}})
    keys = {"projects": ("project_id",), "documents": ("document_id",), "document_versions": ("document_id",), "pages": ("document_id", "page"),
            "sections": ("document_id", "order"), "chunks": ("document_id", "chunk_id"), "plan_versions": ("plan_version_id",),
            "plan_activities": ("activity_id",), "evidence_links": ("activity_id", "wb_contract_number")}
    outputs = {}
    for e in ENTITIES:
        rows = sorted(agg[e], key=lambda r: tuple(str(r.get(k)) for k in keys[e]))
        body = "".join(json.dumps(r, sort_keys=True, default=str, ensure_ascii=False) + "\n" for r in rows)
        _atomic_write(out_dir / f"{e}.jsonl", body)
        outputs[e] = {"rows": len(rows), "sha256": hashlib.sha256(body.encode()).hexdigest()}
    manifest = {"extraction_version": EXTRACTION_VERSION, "code_hash": chash, "tokenizer": chunking.tokenizer_name(),
                "contracts_snapshot": {k: snap_meta[k] for k in ("sha256", "rows", "source", "source_as_of_dates")},
                "inputs": [{"document_id": r["document_id"], "sha256": r["sha256"], "local_path": r["local_path"]} for r in corpus],
                "document_status": status, "outputs": outputs, "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _atomic_write(out_dir / "run_manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    m = run(force=a.force)
    print(json.dumps({k: m[k] for k in ("document_status", "outputs", "tokenizer", "code_hash")}, indent=1))
