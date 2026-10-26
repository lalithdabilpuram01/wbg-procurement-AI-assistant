"""Task 7: parsing-complexity assessment of sampled World Bank PDFs.

For every PDF in samples/documents/files/ this measures (no embeddings, no indexing):
  * pages, text-layer coverage, likely-scanned pages (< 80 extractable chars, has images)
  * PDF outline (bookmarks) depth/size — a cheap source of section hierarchy
  * heading candidates from layout (font size/bold) and from numbering patterns
  * tables detected by pdfplumber (bounded to the first 60 pages for runtime)
  * page locations of procurement / lessons / risk sections (citation feasibility)
  * comparison with the WB-published .txt rendition (length ratio, page-break markers)

Output: profiling/document_parsing.json and profiling/document_outlines/<file>.md
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz  # noqa: E402  (PyMuPDF)
import pdfplumber  # noqa: E402

from wbdisc.config import SETTINGS  # noqa: E402

FILES = SETTINGS.samples_dir / "documents" / "files"
OUTL = SETTINGS.profiling_dir / "document_outlines"
NUMBERED = re.compile(r"^\s*((?:[IVX]{1,5}|[A-H]|\d{1,2}(?:\.\d{1,2}){0,2})[.)]|ANNEX\s+\d+|Annex\s+\d+|SECTION\s+\d+)\s+[A-Z]")
TOPICS = {
    "procurement": re.compile(r"\bprocurement\b", re.I),
    "lessons": re.compile(r"\blessons?(\s+learned)?\b", re.I),
    "risk": re.compile(r"\brisks?\b", re.I),
    "fiduciary": re.compile(r"\bfiduciary\b", re.I),
    "contract": re.compile(r"\bcontracts?\b", re.I),
}
PP_COLUMNS = re.compile(r"(Planned|Actual)\s*(Date|Bid|Contract|Signed|Opening|Evaluation|Award|Invitation)", re.I)


def analyse(pdf: Path) -> dict:
    doc = fitz.open(pdf)
    res: dict = {"file": pdf.name, "bytes": pdf.stat().st_size, "pages": doc.page_count,
                 "encrypted": doc.is_encrypted, "pdf_producer": (doc.metadata or {}).get("producer"),
                 "pdf_creator": (doc.metadata or {}).get("creator")}
    toc = doc.get_toc()
    res["outline_entries"] = len(toc)
    res["outline_max_depth"] = max((t[0] for t in toc), default=0)
    chars, scanned, image_pages, sizes = [], [], 0, []
    heading_layout, heading_numbered = [], []
    topic_pages: dict[str, list[int]] = {k: [] for k in TOPICS}
    pp_column_hits = 0
    for pno, page in enumerate(doc, start=1):
        text = page.get_text("text")
        n = len(text.strip())
        chars.append(n)
        has_img = bool(page.get_images(full=False))
        image_pages += has_img
        if n < 80 and has_img:
            scanned.append(pno)
        pp_column_hits += len(PP_COLUMNS.findall(text))
        for line in text.splitlines():
            if NUMBERED.match(line) and len(line) < 120:
                heading_numbered.append((pno, line.strip()[:100]))
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            for ln in block.get("lines", []):
                spans = [s for s in ln.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                size = max(s["size"] for s in spans)
                sizes.append(round(size, 1))
                bold = all("Bold" in s.get("font", "") or (s.get("flags", 0) & 16) for s in spans)
                txt = "".join(s["text"] for s in spans).strip()
                if 3 < len(txt) < 120 and (bold or size >= 13):
                    heading_layout.append((pno, round(size, 1), bool(bold), txt[:100]))
                    for k, rx in TOPICS.items():
                        if rx.search(txt):
                            topic_pages[k].append(pno)
    body = statistics.mode(sizes) if sizes else None
    res.update({
        "text_chars_total": sum(chars), "text_chars_median_per_page": statistics.median(chars) if chars else 0,
        "pages_lt80_chars": sum(1 for c in chars if c < 80), "likely_scanned_pages": len(scanned),
        "likely_scanned_fraction": round(len(scanned) / max(1, doc.page_count), 3),
        "pages_with_images": image_pages, "body_font_size_mode": body,
        "layout_heading_candidates": len(heading_layout), "numbered_heading_candidates": len(heading_numbered),
        "topic_heading_pages": {k: sorted(set(v))[:15] for k, v in topic_pages.items()},
        "procurement_plan_planned_actual_column_hits": pp_column_hits,
    })
    tables, table_pages = 0, []
    try:
        with pdfplumber.open(pdf) as pl:
            for i, page in enumerate(pl.pages[:60], start=1):
                found = page.find_tables()
                if found:
                    tables += len(found)
                    table_pages.append(i)
    except Exception as exc:  # parsing failure is itself a finding
        res["pdfplumber_error"] = repr(exc)[:200]
    res["tables_first60_pages"] = tables
    res["pages_with_tables_first60"] = len(table_pages)

    txt = pdf.with_suffix(".txt")
    if txt.exists():
        t = txt.read_text(errors="replace")
        res["wb_txt_chars"] = len(t)
        res["wb_txt_to_pdf_text_ratio"] = round(len(t) / max(1, sum(chars)), 3)
        res["wb_txt_formfeeds"] = t.count("\f")
        res["wb_txt_has_page_markers"] = bool(re.search(r"(?im)^\s*(page\s+\d+|-\s*\d+\s*-)\s*$", t)) or "\f" in t
    OUTL.mkdir(parents=True, exist_ok=True)
    md = [f"# {pdf.name}", "", f"pages={doc.page_count} outline_entries={len(toc)} body_font={body}", "", "## PDF outline (first 40)"]
    md += [f"{'  ' * (lvl - 1)}- {title} (p.{page})" for lvl, title, page in toc[:40]]
    md += ["", "## Layout heading candidates (first 40)"]
    md += [f"- p.{p} size={s} bold={b}: {t}" for p, s, b, t in heading_layout[:40]]
    md += ["", "## Numbered heading candidates (first 30)"]
    md += [f"- p.{p}: {t}" for p, t in heading_numbered[:30]]
    (OUTL / f"{pdf.stem}.md").write_text("\n".join(md) + "\n")
    return res


def main() -> None:
    results = []
    for pdf in sorted(FILES.glob("*.pdf")):
        try:
            r = analyse(pdf)
        except Exception as exc:
            r = {"file": pdf.name, "error": repr(exc)[:300]}
        results.append(r)
        print(pdf.name, {k: r.get(k) for k in ("pages", "likely_scanned_pages", "outline_entries", "tables_first60_pages",
                                               "wb_txt_to_pdf_text_ratio")})
    (SETTINGS.profiling_dir / "document_parsing.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
