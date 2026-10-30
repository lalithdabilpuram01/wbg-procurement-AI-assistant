"""Validation of the stage-1 outputs against independent references (writes <output>/validation/).

  python -m rag.ingestion.validate

Benchmarks: the documents' own tables of contents / PDF outlines and templates (sections), PyMuPDF page text
(reference recall, citation pages), the pilot's pdfplumber rows and links (rag/module1_discovery/pilot_first10_v1) and the
independent WDS-text links (rag_scope_v3_35_projects/plan_coverage), and the 'Name' columns of staff tables
(sanitisation). Only counts and identifiers are written; no personal values.
"""
from __future__ import annotations

from app_config.paths import RAG_PILOT_FIRST10, RAG_SCOPE
import collections
import csv
import json
import re
import statistics
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import pdfplumber

from . import config
from .sanitize import EMAIL, LABEL, NAME, PHONE, STOP, _is_name

PILOT = RAG_PILOT_FIRST10
TEXT_LINKS = RAG_SCOPE / "plan_coverage" / "CONTRACT_PLAN_EVIDENCE_LINKS.csv"
STATUS_MAP = {"verified_amount": "amount_verified", "verified_exact_date": "exact_actual_date_supported", "candidate": "candidate",
              "conflicting": "conflicting", "ambiguous": "candidate"}
ISR_TEMPLATES = ["Key Dates", "Project Development Objectives", "Components Table", "Overall Ratings", "Implementation Status and Key Decisions",
                 "Risks", "Results", "1. OBJECTIVE", "2. COMPONENTS", "3. OVERALL RATINGS", "4. KEY ISSUES & STATUS",
                 "5. SYSTEMATIC OPERATIONS RISK-RATING TOOL", "6. RESULTS", "7. DATA ON FINANCIAL PERFORMANCE", "8. KEY DATES", "9. RESTRUCTURING HISTORY"]


def ld(out: Path, name: str) -> list[dict]:
    return [json.loads(x) for x in (out / f"{name}.jsonl").read_text().splitlines()]


def key(t: str) -> str:
    t = re.sub(r"^\s*(?:[IVX]{1,4}|[A-H]|\d{1,2}|ANNEX\s*\d+[A-Z]?|Annex\s*\d+[A-Z]?)[.:)]?\s*", lambda m: m.group(0).upper().replace(" ", ""), t)
    return re.sub(r"[^A-Z0-9]", "", t.upper())[:22]


def alnum(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def toc_expected(path: Path) -> list[str]:
    """Headings listed in the document's own table of contents (or PDF outline)."""
    with fitz.open(path) as doc:
        toc = doc.get_toc()
        if toc:
            return [re.sub(r"\s+\d+$", "", t).strip() for _, t, _ in toc]
        exp = []
        for pg in doc:
            text = pg.get_text()
            if not re.search(r"table of contents|^contents$", text, re.I | re.M):
                continue
            joined = re.sub(r"\n(?=[A-Z][a-z])", " ", text)  # titles wrapped over two lines
            for ln in joined.splitlines():
                ln = re.sub(r"\.{3,}.*$|\s+\d+\s*$", "", ln.strip())
                if re.match(r"^([IVX]{1,4}\.|[A-H]\.|Annex\s*\d+|ANNEX\s*\d+)\s*\S", ln):
                    exp.append(ln)
                elif exp and re.fullmatch(r"[IVX]{1,4}\.|[A-H]\.", exp[-1]):
                    exp[-1] = f"{exp[-1]} {ln}"
            if exp:
                break
        return exp


def main(out: Path | None = None) -> dict:
    out = out or config.OUTPUT
    vdir = out / "validation"
    vdir.mkdir(exist_ok=True)
    docs = {d["document_id"]: d for d in ld(out, "documents")}
    vers = {v["document_id"]: v for v in ld(out, "document_versions")}
    pages = ld(out, "pages")
    secs = ld(out, "sections")
    chunks = ld(out, "chunks")
    acts = ld(out, "plan_activities")
    links = ld(out, "evidence_links")
    pvs = ld(out, "plan_versions")
    m: dict = {}

    # 1 pages
    m["pages"] = {"total": len(pages), "with_text_100_chars": sum(p["chars"] >= 100 for p in pages),
                  "likely_scanned": [(p["document_id"], p["page"]) for p in pages if p["likely_scanned"]],
                  "printed_label_read": sum(bool(p["printed_page_label"]) for p in pages),
                  "printed_label_inferred_only": sum(bool(p["printed_page_label_inferred"]) and not p["printed_page_label"] for p in pages),
                  "by_document": {d: {"pages": sum(p["document_id"] == d for p in pages), "printed_labels": sum(p["document_id"] == d and bool(p["printed_page_label"]) for p in pages)}
                                  for d in sorted(docs)}}

    # 2 metadata completeness (share non-null)
    def comp(rows, fields):
        return {f: round(sum(_get(r, f) not in (None, "", []) for r in rows) / len(rows), 3) for f in fields} if rows else {}
    m["metadata_completeness"] = {
        "documents": comp(list(docs.values()), ["api.title", "api.wds_document_date", "api.wds_disclosure_date", "api.language", "api.wds_version_type",
                                                 "api.report_number", "api.guid", "api.source_url", "api.final_url", "api.txt_url", "pdf.page_count",
                                                 "pdf.project_ids_in_text", "pdf.producer", "pdf.creation_date"]),
        "document_versions": comp(list(vers.values()), ["sha256", "size_bytes", "page_count", "local_path", "download.downloaded_at"]),
        "plan_versions": comp(pvs, ["pdf.project_id_in_header", "pdf.executing_agency_text", "pdf.original_plan_approval_date", "pdf.revision_date",
                                    "pdf.loan_credit_numbers", "pdf.textual_part_plan_date_text", "inferred.reference_agency_code"]),
        "chunks": comp(chunks, ["section_id", "physical_page_start", "printed_page_label_start", "text", "token_count"]),
        "plan_activities": comp(acts, ["package_reference_core", "section", "review_type", "process_status", "estimated_amount_usd", "actual_amount_usd",
                                       "signed_contract_planned", "signed_contract_actual"]),
        "own_project_id_in_pdf": {d: docs[d]["pdf"]["own_project_id_in_text"] for d in sorted(docs)},
        "plan_project_matches_header": {p["document_id"]: p["pdf"]["project_id_in_header"] == p["project_id"] for p in pvs}}

    # 3 section detection
    sec_res = {}
    for d, doc in sorted(docs.items()):
        path = config.REPO / vers[d]["local_path"]
        det = [s["heading_text"] for s in secs if s["document_id"] == d and s["heading_level"] > 0]
        detk = {key(t) for t in det}
        full = "\n".join(pg.get_text() for pg in fitz.open(path))
        if doc["doc_type"] in ("PAD", "ICR"):
            exp = toc_expected(path)
            basis = "PDF outline" if fitz.open(path).get_toc() else "table of contents page"
        elif doc["doc_type"] == "ICRR":
            exp = [f"{n}. " for n in range(1, 15) if re.search(rf"^\s*{n}\.\s+[A-Z]", full, re.M)]
            basis = "ICRR numbered template (numbers present in text)"
        elif doc["doc_type"] == "ISR":
            exp = [t for t in ISR_TEMPLATES if re.search(rf"^\s*{re.escape(t)}\s*$", full, re.M | re.I)]
            basis = "ISR template headings present in text"
        else:
            with pdfplumber.open(path) as pl:
                labels = sorted({(t[0][0] or "").strip().upper() for p in pl.pages for t in p.extract_tables() if t and t[0] and t[0][0]} &
                                {"WORKS", "GOODS", "NON CONSULTING SERVICES", "CONSULTING FIRMS", "INDIVIDUAL CONSULTANTS"})
            exp = labels + (["General Information"] if "General Information" in full else [])
            basis = "plan category tables + General Information block"
        if doc["doc_type"] == "ICRR":
            found = [e for e in exp if any(t.startswith(e) for t in det)]
        else:
            found = [e for e in exp if key(e) in detk or any(key(e)[:14] and key(e)[:14] in k for k in detk)]
        strip_num = lambda t: re.sub(r"[^A-Z0-9]", "", re.sub(r"^\s*(?:[IVX]{1,4}|[A-H]|\d{1,2})[.)]\s*", "", t).upper())[:22]  # noqa: E731
        unnumbered = [e for e in exp if e not in found and strip_num(e) and any(strip_num(t) == strip_num(e) for t in det)]
        found += unnumbered
        sec_res[d] = {"doc_type": doc["doc_type"], "basis": basis, "expected": len(exp), "found": len(found),
                      "matched_without_numbering": len(unnumbered),
                      "recall": round(len(found) / len(exp), 3) if exp else None, "missing": [e[:60] for e in exp if e not in found],
                      "detected_headings": len(det)}
    m["section_detection"] = sec_res

    # 4 chunk quality
    text = [c for c in chunks if c["chunk_type"] == "text"]
    tab = [c for c in chunks if c["chunk_type"] == "table"]
    sec_by = {s["section_id"]: s for s in secs}
    groups = collections.defaultdict(list)
    for c in tab:
        groups[(c["section_id"], c["physical_page_start"], c["table_index"])].append(c)
    m["chunk_quality"] = {
        "chunks": collections.Counter(c["chunk_type"] for c in chunks),
        "text_tokens": _dist([c["token_count"] for c in text]), "table_tokens": _dist([c["token_count"] for c in tab]),
        "row_tokens": _dist([c["token_count"] for c in chunks if c["chunk_type"] == "table_row"]),
        "over_max_tokens": sum(c["token_count"] > config.CHUNK_MAX_TOKENS for c in chunks),
        "text_chunks_end_at_sentence_or_paragraph": round(sum(bool(re.search(r"[.:;!?)\]”\"]\s*$", c["text"])) for c in text) / len(text), 3),
        "short_text_chunks_under_min": sum(c["token_count"] < config.CHUNK_MIN_TOKENS for c in text),
        "short_text_chunks_that_are_whole_sections": sum(c["token_count"] < config.CHUNK_MIN_TOKENS and sum(x["section_id"] == c["section_id"] for x in text) == 1 for c in text),
        "chunks_outside_their_section_pages": sum(not (sec_by[c["section_id"]]["physical_page_start"] <= c["physical_page_start"] <= c["physical_page_end"] <= max(sec_by[c["section_id"]]["physical_page_end"], c["physical_page_start"]))
                                                  for c in chunks if c["chunk_type"] != "table_row"),
        "split_tables": sum(len(v) > 1 for v in groups.values()),
        "split_tables_with_header_repeated": sum(len(v) > 1 and len({x["text"].split("\n")[1 if x.get("heading_prefixed") else 0] for x in v}) == 1
                                                 for v in groups.values())}

    # 5 reference recall per plan
    rec = {}
    for pv in pvs:
        d = pv["document_id"]
        with fitz.open(config.REPO / vers[d]["local_path"]) as doc:
            # independent of the extractor: PyMuPDF page text, whitespace removed (references wrap across lines)
            in_text = {(i + 1, m.group(1), m.group(2)) for i, pg in enumerate(doc)
                       for m in re.finditer(r"-(\d{5,7})-(CW|GO|CS|NC|TA)-", re.sub(r"\s+", "", pg.get_text()))}
        got = {(a["pdf_page"], *re.search(r"-(\d{5,7})-(CW|GO|CS|NC|TA)-", a["package_reference_core"]).groups()) for a in acts
               if a["document_id"] == d and a["package_reference_core"]}
        table_only = {(a["pdf_page"], *re.search(r"-(\d{5,7})-(CW|GO|CS|NC|TA)-", a["package_reference_core"]).groups()) for a in acts
                      if a["document_id"] == d and a["package_reference_core"] and a["extraction_method"] == "pdf_table"}
        rec[d] = {"references_in_page_text": len(in_text), "extracted": len(got), "recall": round(len(in_text & got) / len(in_text), 3) if in_text else None,
                  "recall_table_only": round(len(in_text & table_only) / len(in_text), 3) if in_text else None,
                  "missed": sorted(map(list, in_text - got))[:10], "extracted_not_in_page_text": sorted(map(list, got - in_text))[:10],
                  "legacy_rows_without_step_reference": sum(a["document_id"] == d and not a["package_reference_core"] for a in acts)}
    m["reference_recall"] = rec

    # 6 table-row accuracy vs the pilot pdfplumber rows (benchmark) and the independent WDS-text values
    bench = collections.defaultdict(list)
    with (PILOT / "PDF_PLAN_ROWS.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r["activity_reference"]:
                bench[(r["document_id"], r["page"], r["activity_reference"])].append(r)
    cmp_ = collections.Counter()
    for a in acts:
        b = bench.get((a["document_id"], str(a["pdf_page"]), a["package_reference_core"]))
        if not b:
            continue
        b = b[0]
        cmp_["rows_compared"] += 1
        for f_a, f_b, conv in (("actual_amount_usd", "actual_amount_usd", float), ("estimated_amount_usd", "estimated_amount_usd", float),
                               ("signed_contract_planned", "signed_planned", str), ("signed_contract_actual", "signed_actual", str),
                               ("process_status", "status", str), ("review_type", "review", str)):
            bv = conv(b[f_b]) if b[f_b] not in ("", None) else None
            cmp_[f"{f_a}_equal"] += (a[f_a] == bv)
    bench_rows = sum(1 for k in bench if k[0] in {p["document_id"] for p in pvs})
    textv = collections.defaultdict(list)
    with TEXT_LINKS.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r["document_id"] in {p["document_id"] for p in pvs} and r["package_reference"]:
                textv[(r["document_id"], r["package_reference"])].append(r)
    tcmp = collections.Counter()
    for a in acts:
        t = textv.get((a["document_id"], a["package_reference_core"]))
        if not t:
            continue
        t = t[0]
        tcmp["activities_compared"] += 1
        ta = float(t["plan_actual_amount_usd"]) if t["plan_actual_amount_usd"] not in ("", None) else None
        if ta is not None and a["actual_amount_usd"] is not None:
            tcmp["actual_amount_both"] += 1
            tcmp["actual_amount_equal"] += abs(ta - a["actual_amount_usd"]) < 0.01
        if t["plan_signed_actual_date"] and t["text_form"] == "layout":
            tcmp["signed_actual_both"] += 1
            tcmp["signed_actual_equal"] += t["plan_signed_actual_date"] == a["signed_contract_actual"]
    m["table_row_accuracy"] = {"vs_pilot_pdfplumber_rows": dict(cmp_), "pilot_rows_with_reference": bench_rows,
                               "pilot_rows_recovered": cmp_["rows_compared"], "vs_wds_text_rows": dict(tcmp),
                               "extractor_disagreement_flags": sum("extractor_disagreement" in a["value_flags"] for a in acts),
                               "uncertain_value_activities": sum(bool(a["uncertain_fields"]) for a in acts),
                               "header_inherited_rows": sum(bool(a["header_inherited"]) for a in acts),
                               "text_before_reference_in_cell": sum("text_before_reference_in_cell" in a["value_flags"] for a in acts)}

    # 7 evidence-link accuracy vs pilot PDF links and WDS-text links
    mine = {(l["document_id"], l["wb_contract_number"]): l["evidence_status"] for l in links if l["wb_contract_number"]}
    res = {}
    for name, path, col in (("pilot_pdf_links", PILOT / "PDF_CONTRACT_LINKS.csv", "pdf_evidence_status"), ("wds_text_links", TEXT_LINKS, "evidence_status")):
        ref = {}
        with path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r["document_id"] in {p["document_id"] for p in pvs} and r.get("wb_contract_number") and STATUS_MAP.get(r[col]):
                    ref[(r["document_id"], r["wb_contract_number"])] = STATUS_MAP[r[col]]
        both = set(ref) & set(mine)
        dis = collections.Counter(f"{ref[k]} -> {mine[k]}" for k in both if ref[k] != mine[k])
        res[name] = {"benchmark_links": len(ref), "found_by_pipeline": len(both), "status_agrees": sum(ref[k] == mine[k] for k in both),
                     "status_changes": dict(dis), "benchmark_links_missing": sorted(map(list, set(ref) - set(mine)))[:20],
                     "pipeline_links_not_in_benchmark": len(set(mine) - set(ref))}
    res["pipeline_status_counts"] = dict(collections.Counter(l["evidence_status"] for l in links))
    res["project_only_links"] = sum(1 for l in links if l["wb_contract_number"] and not l["package_reference_core"] and not l.get("activity_reference_printed"))
    m["evidence_link_accuracy"] = res

    # 8 citation page accuracy
    page_text = {}
    for d in docs:
        with fitz.open(config.REPO / vers[d]["local_path"]) as doc:
            page_text[d] = [pg.get_text() for pg in doc]
    ok = tot = 0
    for l in links:
        if not l["package_reference_core"]:
            continue
        num = re.search(r"-(\d{5,7})-", l["package_reference_core"]).group(1)
        tot += 1
        ok += num in re.sub(r"\s+", "", page_text[l["document_id"]][l["pdf_page"] - 1])
    cok = ctot = 0
    for c in chunks:
        if c["chunk_type"] == "table_row":
            continue
        body = c["text"].split("\n", 1)[1] if c.get("heading_prefixed") and "\n" in c["text"] else c["text"]
        if c["chunk_type"] == "table":   # pdfplumber cell order differs from PyMuPDF reading order: probe one cell
            cells = [x for x in re.split(r"\s*\|\s*|\n", body) if len(alnum(x)) >= 12 and "[" not in x]
            probe = alnum(cells[0])[:30] if cells else ""
        else:
            words = [w for w in re.split(r"\s+", body) if w and "[" not in w and "|" not in w]
            probe = alnum(" ".join(words[:8]))[:40]
        if len(probe) < 12:
            continue
        ctot += 1
        span = alnum(" ".join(page_text[c["document_id"]][c["physical_page_start"] - 1:c["physical_page_end"]]))
        cok += probe in span
    m["citation_page_accuracy"] = {"evidence_links_reference_on_cited_page": [ok, tot], "chunks_text_found_on_cited_pages": [cok, ctot]}

    # 9 sanitisation: residual detectors + staff-table 'Name' column ground truth + label-adjacent names in raw text
    all_text = collections.defaultdict(str)
    for c in chunks:
        all_text[c["document_id"]] += "\n" + c["text"]
    broad_phone = re.compile(r"(?:tel|phone|fax|mobile|cell)\w*[^0-9]{0,12}\+?\d[\d\s()./-]{7,}\d", re.I)  # independent, broader detector
    resid = {"email_in_chunks": sum(len(EMAIL.findall(c["text"])) for c in chunks), "phone_in_chunks": sum(len(PHONE.findall(c["text"])) for c in chunks),
             "phone_like_after_label_in_chunks_broad_detector": sum(len(broad_phone.findall(c["text"])) for c in chunks)}
    staff_names = residual_staff = label_names = residual_label = 0
    for d in docs:
        path = config.REPO / vers[d]["local_path"]
        flat = alnum(all_text[d])
        with pdfplumber.open(path) as pl:
            for p in pl.pages:
                for t in p.extract_tables():
                    for r in t:   # continuation rows of staff tables: a World Bank unit-code cell marks a staff row
                        cells = [re.sub(r"\s+", " ", (c or "")).strip() for c in r]
                        if any(re.fullmatch(r"[A-Z]{2,4}\d{2}", c) for c in cells) and cells and len(cells[0].split()) >= 2 and re.match(r"^[A-Z][a-z]", cells[0]):
                            staff_names += 1
                            residual_staff += alnum(cells[0]) in flat
                    hdr = next((r for r in t[:3] if r and any((c or "").strip().lower() == "name" for c in r)), None)
                    if not hdr or not any(re.search(r"role|title|unit|specializ|responsib|position", (c or ""), re.I) for c in hdr):
                        continue
                    col = [(c or "").strip().lower() for c in hdr].index("name")
                    for r in t[t.index(hdr) + 1:]:
                        v = re.sub(r"\s+", " ", (r[col] or "")).strip() if col < len(r) else ""
                        if len(v.split()) >= 2 and re.match(r"^[A-Z]", v):
                            staff_names += 1
                            residual_staff += alnum(v) in flat
        for pg in page_text[d]:
            for lm in LABEL.finditer(pg):
                window = re.sub(r"\s*\n\s*", " | ", pg[lm.end():lm.end() + 90])   # a name never spans lines in this check
                for nm in NAME.finditer(window):
                    if _is_name(nm):
                        label_names += 1
                        residual_label += alnum(nm.group(0)) in flat
                    break
    resid.update({"staff_table_names": staff_names, "staff_table_names_remaining_in_chunks": residual_staff,
                  "label_adjacent_names": label_names, "label_adjacent_names_remaining_in_chunks": residual_label,
                  "masked_counts": dict(collections.Counter(k for c in chunks for k, v in c["sanitisation"].items() for _ in range(v))),
                  "possible_name_fragments_after_mask": sum(1 for c in chunks for f in re.findall(r"\[PERSON\][ \t]*\n?[ \t]*([A-Z][a-z'’\-]+)[ \t]*(?:[,/|\n]|$)", c["text"])
                                                            if f.lower() not in STOP)})
    m["sanitisation"] = resid
    (vdir / "metrics.json").write_text(json.dumps(m, indent=1, sort_keys=True, default=str))
    return m


def _get(r: dict, dotted: str):
    for k in dotted.split("."):
        r = r.get(k) if isinstance(r, dict) else None
    return r


def _dist(v: list[int]) -> dict:
    return {"n": len(v), "min": min(v), "median": statistics.median(v), "p90": sorted(v)[int(0.9 * (len(v) - 1))], "max": max(v)} if v else {}


if __name__ == "__main__":
    print(json.dumps(main(), indent=1, default=str))
