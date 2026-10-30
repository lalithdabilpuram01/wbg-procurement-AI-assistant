"""Structure inspection of the 10 pilot PDFs (read-only; PyMuPDF + pdfplumber, already installed).

Per document: text readability per page (characters, image coverage, likely-scanned pages), PDF outline and font-size
heading candidates, tables (pdfplumber), annex pages, physical page vs PDF page label vs printed page number, procurement
reference formats, and sensitive-information indicators (counts and page numbers only: e-mail and phone patterns, staff /
team-member sections). Personal values are never written. Outputs go to pilot_first10_v1/.
"""
from __future__ import annotations

import collections
import csv
import json
import re
import statistics
from pathlib import Path

import fitz
import pdfplumber

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS = ROOT / "rag" / "corpus_v3" / "CORPUS_MANIFEST_FIRST10.csv"
OUT = HERE / "pilot_first10_v1"
STEP = re.compile(r"\b[A-Z]{2}-[A-Z0-9&()\- ]{1,40}?-\s?\d{5,7}\s?-\s?(?:CW|GO|CS|NC|TA)\s?-\s?[A-Z]{2,5}\b")
LEGACY = re.compile(r"\b(?:[A-Z]{2,6}[/-]){1,3}(?:ICB|NCB|SH|QCBS|CQS|IC|DC|LCS|SSS|NS|W|G|S|CS)[/-]?\d{1,3}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,4}[\s-]\d{3,4}(?:[\s-]\d{2,4})?")
STAFF = re.compile(r"Task Team Leader|Team Leader\(s\)|Team Composition|Bank Staff|Bank staff|Practice Manager|Country Director|"
                   r"Regional Vice President|Preparation team|Supervision/ICR|Prepared by|Peer Reviewers?", re.I)
ANNEX = re.compile(r"^\s*(ANNEX|Annex)\s+[0-9IVX]+", re.M)
PRINTED_NUM = re.compile(r"^\s*(?:Page\s+)?(\d{1,3}|[ivxlc]{1,6})(?:\s+of\s+\d+)?\s*$", re.I)


def headings(doc: fitz.Document) -> tuple[float, list[tuple[int, float, str]]]:
    sizes = collections.Counter()
    spans = []
    for pno, page in enumerate(doc, 1):
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                if not text:
                    continue
                size = max(s["size"] for s in line["spans"])
                bold = any("Bold" in s["font"] or s["flags"] & 16 for s in line["spans"])
                sizes[round(size, 1)] += len(text)
                spans.append((pno, size, bold, text))
    body = sizes.most_common(1)[0][0] if sizes else 10
    out = [(p, round(sz, 1), t[:90]) for p, sz, bd, t in spans
           if 3 <= len(t) <= 90 and (sz >= body + 1.5 or (bd and sz >= body and re.match(r"^([A-Z0-9IVX]+[.)]|[A-Z][A-Za-z ,&/-]+$)", t)))]
    return body, out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with CORPUS.open(newline="") as fh:
        docs = list(csv.DictReader(fh))
    report, pages_out = [], []
    for d in docs:
        path = ROOT / d["local_path"]
        doc = fitz.open(path)
        page_info = []
        for i, page in enumerate(doc, 1):
            text = page.get_text()
            area = page.rect.width * page.rect.height
            img_cover = sum((r.width * r.height) for img in page.get_images(full=True) for r in page.get_image_rects(img[0])) / area if area else 0
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            printed = next((m.group(1) for ln in (lines[-3:][::-1] + lines[:2]) if (m := PRINTED_NUM.match(ln))), None)
            page_info.append({"document_id": d["document_id"], "page": i, "label": page.get_label() or None, "printed_number": printed,
                              "chars": len(text.strip()), "image_cover": round(min(img_cover, 1.0), 2),
                              "likely_scanned": len(text.strip()) < 100 and img_cover > 0.5, "annex_heading": bool(ANNEX.search(text)),
                              "step_refs": len(STEP.findall(text)), "legacy_refs": len(LEGACY.findall(text)), "emails": len(EMAIL.findall(text)),
                              "phone_like": len(PHONE.findall(text)), "staff_section": bool(STAFF.search(text)), "orientation": "landscape" if page.rect.width > page.rect.height else "portrait"})
        body, heads = headings(doc)
        toc = doc.get_toc()
        tables = collections.Counter()
        table_shapes = []
        with pdfplumber.open(path) as pl:
            for i, page in enumerate(pl.pages, 1):
                for t in page.find_tables():
                    tables[i] += 1
                    data = t.extract()
                    table_shapes.append((i, len(data), max((len(r) for r in data), default=0)))
        full = "\n".join(p.get_text() for p in doc)
        ids = sorted(set(re.findall(r"\bP\d{6}\b", full)))
        printed_vs_physical = [(p["page"], p["printed_number"]) for p in page_info if p["printed_number"]]
        offset = collections.Counter(int(n) - pg for pg, n in printed_vs_physical if n.isdigit())
        report.append({
            "document_id": d["document_id"], "project_id": d["project_id"], "doc_type": d["doc_type"], "local_path": d["local_path"], "pages": doc.page_count,
            "pdf_metadata": {k: v for k, v in (doc.metadata or {}).items() if k in ("format", "creator", "producer", "creationDate")},
            "text_pages": sum(p["chars"] >= 100 for p in page_info), "low_text_pages": [p["page"] for p in page_info if p["chars"] < 100],
            "likely_scanned_pages": [p["page"] for p in page_info if p["likely_scanned"]],
            "median_chars_per_page": statistics.median(p["chars"] for p in page_info),
            "landscape_pages": sum(p["orientation"] == "landscape" for p in page_info),
            "outline_entries": len(toc), "outline_sample": [(lvl, t[:70], pg) for lvl, t, pg in toc[:14]],
            "body_font_size": body, "heading_candidates": len(heads), "heading_sample": heads[:30],
            "pages_with_tables": len(tables), "tables": sum(tables.values()), "largest_tables": sorted(table_shapes, key=lambda x: -x[1] * x[2])[:5],
            "annex_pages": [p["page"] for p in page_info if p["annex_heading"]][:20],
            "page_labels_defined": any(p["label"] for p in page_info),
            "printed_page_number_pages": len(printed_vs_physical), "printed_minus_physical_offsets": dict(offset.most_common(4)),
            "printed_numbers_sample": printed_vs_physical[:8],
            "project_ids_in_text": ids[:10], "own_project_id_in_text": d["project_id"] in ids,
            "step_reference_pages": [p["page"] for p in page_info if p["step_refs"]], "step_references": sum(p["step_refs"] for p in page_info),
            "legacy_reference_like": sum(p["legacy_refs"] for p in page_info),
            "sensitive": {"email_pattern_pages": [p["page"] for p in page_info if p["emails"]], "email_count": sum(p["emails"] for p in page_info),
                          "phone_like_pages": [p["page"] for p in page_info if p["phone_like"]][:15], "phone_like_count": sum(p["phone_like"] for p in page_info),
                          "staff_name_section_pages": [p["page"] for p in page_info if p["staff_section"]][:15]},
        })
        pages_out.extend(page_info)
        doc.close()
    (OUT / "pdf_structure_inspection.json").write_text(json.dumps(report, indent=1, default=str))
    with (OUT / "pdf_page_profile.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(pages_out[0]))
        w.writeheader()
        w.writerows(pages_out)
    for r in report:
        print(json.dumps({k: v for k, v in r.items() if k not in ("heading_sample", "outline_sample")}, default=str))


if __name__ == "__main__":
    main()
