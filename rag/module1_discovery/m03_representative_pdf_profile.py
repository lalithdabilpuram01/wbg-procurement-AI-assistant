"""Module 1 discovery (READ-ONLY, local files only): structural profile of representative PDFs.

For each selected PDF: page count, body font size (mode), heading candidates (bold or larger-than-body short lines, or
numbered patterns such as 'I.', 'A.', '1.2', 'ANNEX 1'), the first top-level headings, annex starts, table count from
PyMuPDF find_tables (all pages, or a page sample for very long files), dot-leader table-of-contents lines, printed page
labels vs physical pages, repeated running header/footer lines, cl100k token count, and a text-layer check. The 4,184-page
procurement plan is profiled on a page sample only. Nothing is downloaded or written outside the evidence folder.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import fitz
import tiktoken

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "evidence"
D = "discovery/samples/documents/files/"
SELECTED = [D + "P131049_PAD_17555469.pdf", D + "P145502_PAD_19368603.pdf", D + "P131049_ISR_27391778.pdf", D + "P131049_ICR_32160299.pdf",
            D + "P131049_ICRR_32800471.pdf", "discovery/samples/documents/pp_files/../files/P145502_PP_31837484.pdf", D + "P127203_ICRFR_33407968.pdf",
            D + "P039892_SAR1990_696824.pdf", "discovery/project_deep_dive/raw/P163870/pp_pdf/2021-05-18_PP_33215505.pdf"]
NUMBERED = re.compile(r"^(?:[IVXL]+\.|[A-H]\.|\d+(?:\.\d+){0,2}\.?|ANNEX\s+\d+|Annex\s+\d+|ANNEXE\s+\d+|APPENDIX\s+\w+)\s+\S", re.U)
TOC_LINE = re.compile(r"\.{5,}\s*\d+\s*$")
ENC = tiktoken.get_encoding("cl100k_base")


def lines(page):
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if spans:
                yield "".join(s["text"] for s in spans).strip(), max(s["size"] for s in spans), all(s["flags"] & 16 for s in spans)


def profile(rel: str) -> dict:
    p = (ROOT / rel).resolve()
    doc = fitz.open(p)
    n = doc.page_count
    pages = list(range(n)) if n <= 300 else sorted({*range(5), *range(n // 2, n // 2 + 5), *range(n - 5, n)})
    sizes, heads, annex, toc, first_last = Counter(), [], [], 0, Counter()
    text_all, tables, table_pages = [], 0, []
    for i in pages:
        page = doc[i]
        ls = list(lines(page))
        for t, sz, _ in ls:
            sizes[round(sz)] += len(t)
        if ls:
            first_last[ls[0][0][:60]] += 1
            first_last[ls[-1][0][:60]] += 1
        text_all.append(page.get_text("text"))
        try:
            tf = page.find_tables()
            if tf.tables:
                tables += len(tf.tables)
                table_pages.append(i + 1)
        except Exception:  # noqa: BLE001
            pass
        for t, sz, bold in ls:
            toc += bool(TOC_LINE.search(t))
            if TOC_LINE.search(t) or len(t) > 110:
                continue
            body = sizes.most_common(1)[0][0] if sizes else 11
            if (bold or sz >= body + 1.5) and (NUMBERED.match(t) or t.isupper()) and len(t) > 3:
                heads.append({"page": i + 1, "text": t[:90], "size": round(sz, 1), "bold": bold})
            if re.match(r"^(ANNEX|Annex|ANNEXE|APPENDIX)\s+\w+", t) and (bold or sz >= body):
                annex.append({"page": i + 1, "text": t[:80]})
    body = sizes.most_common(1)[0][0] if sizes else None
    labels = [doc[i].get_label() for i in range(min(n, 12))]
    text = "\n".join(text_all)
    running = [t for t, c in first_last.most_common(8) if c >= max(3, len(pages) // 3)]
    out = {"path": rel.replace("pp_files/../", ""), "pages": n, "pages_profiled": len(pages), "body_font_size": body,
           "heading_candidates": len(heads), "top_headings_sample": [h for h in heads if NUMBERED.match(h["text"]) and re.match(r"^[IVX]+\.|^ANNEX|^Annex", h["text"])][:14],
           "annex_starts": annex[:12], "annex_count": len({a["text"].split()[1] for a in annex if len(a["text"].split()) > 1}),
           "tables_detected": tables, "pages_with_tables": len(table_pages), "toc_dot_leader_lines": toc,
           "printed_page_labels_first_12": labels, "running_header_footer_lines": running,
           "tokens_cl100k_profiled_pages": len(ENC.encode(text)), "text_chars_profiled_pages": len(text),
           "low_text_pages_profiled": sum(1 for t in text_all if len(t.strip()) < 80)}
    doc.close()
    return out


def main() -> None:
    res = [profile(r) for r in SELECTED]
    (OUT / "representative_pdf_profiles.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    for r in res:
        print(json.dumps({k: r[k] for k in ("path", "pages", "pages_profiled", "body_font_size", "heading_candidates", "annex_count", "tables_detected",
                                            "pages_with_tables", "toc_dot_leader_lines", "tokens_cl100k_profiled_pages", "low_text_pages_profiled", "running_header_footer_lines")},
                         ensure_ascii=False))
        print("   headings:", [f'p{h["page"]}:{h["text"][:45]}' for h in r["top_headings_sample"][:10]])
        print("   annexes:", [f'p{a["page"]}:{a["text"][:30]}' for a in r["annex_starts"][:8]], "labels:", r["printed_page_labels_first_12"][:6])


if __name__ == "__main__":
    main()
