"""Page text extraction with PyMuPDF: lines with font/position, repeated header/watermark cleanup, printed page labels,
and table regions from pdfplumber (so table text is not duplicated as prose)."""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import pdfplumber

BOILERPLATE = [re.compile(p, re.I) for p in (
    r"^public disclosure authori[sz]ed$", r"^the world bank$", r"^for official use only$", r"^official use only$",
    r"^page\s+\d+\s+of\s+\d+$", r"^report no[.:]?\s*[a-z0-9-]+$")]
PRINTED = re.compile(r"^(?:page\s+)?(\d{1,3}|[ivxlc]{1,7})(?:\s+of\s+\d+)?$", re.I)
TOC_LINE = re.compile(r"(\.{4,}|…{2,})\s*\d+\s*$|\s\d{1,3}$")


@dataclass
class Line:
    page: int
    text: str
    size: float
    bold: bool
    x0: float
    y0: float
    y1: float
    block: int
    in_table: bool = False
    start: int = 0          # character offsets of this line in Page.raw_text (the original extracted text)
    end: int = 0
    footnote: bool = False  # small-font line in the lower part of the page


@dataclass
class Page:
    number: int                       # 1-based physical page
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    printed_label: str | None = None  # read from the page margin
    chars: int = 0
    image_cover: float = 0.0
    is_toc: bool = False
    tables: list[dict] = field(default_factory=list)   # pdfplumber: bbox + rows
    raw_text: str = ""      # original PyMuPDF text: every line in extraction order (incl. removed boilerplate), '\n'-joined


def _norm(t: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", t.strip().lower()))


def extract(path: Path, with_tables: bool = True) -> list[Page]:
    pages: list[Page] = []
    with fitz.open(path) as doc:
        for pno, pg in enumerate(doc, 1):
            area = pg.rect.width * pg.rect.height
            cover = 0.0
            for img in pg.get_images(full=True):
                for r in pg.get_image_rects(img[0]):
                    cover += r.width * r.height
            page = Page(pno, pg.rect.width, pg.rect.height, image_cover=round(min(cover / area, 1.0), 3) if area else 0.0)
            for bno, b in enumerate(pg.get_text("dict")["blocks"]):
                for ln in b.get("lines", []):
                    spans = [s for s in ln["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    text = re.sub(r"\s+", " ", "".join(s["text"] for s in ln["spans"])).strip()
                    bold_chars = sum(len(s["text"].strip()) for s in spans if ("Bold" in s["font"]) or (s["flags"] & 16))
                    page.lines.append(Line(pno, text, round(max(s["size"] for s in spans), 1),
                                           bold_chars >= 0.9 * sum(len(s["text"].strip()) for s in spans),  # mostly bold, not a run-in lead
                                           round(ln["bbox"][0], 1), round(ln["bbox"][1], 1), round(ln["bbox"][3], 1), bno))
            pos = 0
            for ln in page.lines:
                ln.start, ln.end = pos, pos + len(ln.text)
                pos = ln.end + 1
            page.raw_text = "\n".join(ln.text for ln in page.lines)
            page.chars = sum(len(x.text) for x in page.lines)
            pages.append(page)
    if with_tables:
        with pdfplumber.open(path) as pl:
            for page, pp in zip(pages, pl.pages):
                for t in pp.find_tables():
                    rows = t.extract()
                    if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                        page.tables.append({"bbox": tuple(round(v, 1) for v in t.bbox), "rows": rows})
                for ln in page.lines:
                    ln.in_table = any(b[0] - 2 <= ln.x0 <= b[2] and b[1] - 2 <= ln.y0 <= b[3] + 2 for b in (t["bbox"] for t in page.tables))
    _clean(pages)
    sizes: collections.Counter = collections.Counter()
    for p in pages:
        for ln in p.lines:
            sizes[ln.size] += len(ln.text)
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    for p in pages:
        for ln in p.lines:
            ln.footnote = (not ln.in_table) and ln.size <= body - 1.5 and ln.y0 > 0.55 * p.height
    return pages


def _clean(pages: list[Page]) -> None:
    """Remove running headers/footers (same normalised text on >= 30% of pages, in the top or bottom 9% of the page),
    known watermark lines anywhere, and margin page numbers (kept as printed_label)."""
    n = len(pages)
    margin_count: collections.Counter = collections.Counter()
    for p in pages:
        seen = set()
        for ln in p.lines:
            if ln.y0 < 0.09 * p.height or ln.y1 > 0.91 * p.height:
                seen.add(_norm(ln.text))
        margin_count.update(seen)
    repeated = {t for t, c in margin_count.items() if n >= 4 and c >= max(3, 0.3 * n) and len(t) < 120}
    for p in pages:
        keep = []
        for ln in p.lines:
            in_margin = ln.y0 < 0.09 * p.height or ln.y1 > 0.91 * p.height
            m = PRINTED.match(ln.text) if in_margin else None
            if m and p.printed_label is None:
                p.printed_label = m.group(1)
                p.removed_lines.append("printed_page_number")
                continue
            if any(rx.match(ln.text) for rx in BOILERPLATE) or (in_margin and _norm(ln.text) in repeated):
                p.removed_lines.append(ln.text[:60])
                continue
            keep.append(ln)
        p.lines = keep
        toc_lines = sum(bool(TOC_LINE.search(ln.text)) for ln in p.lines)
        p.is_toc = any(re.match(r"^(table of contents|contents)$", ln.text.strip(), re.I) for ln in p.lines) or (
            len(p.lines) >= 8 and toc_lines >= 0.5 * len(p.lines) and any("...." in ln.text for ln in p.lines))


def infer_labels(pages: list[Page]) -> dict[int, str]:
    """Inferred printed labels for pages without one: only between two printed arabic labels with the same offset."""
    known = [(p.number, int(p.printed_label)) for p in pages if p.printed_label and p.printed_label.isdigit()]
    out = {}
    for (a, la), (b, lb) in zip(known, known[1:]):
        if la - a == lb - b:
            for k in range(a + 1, b):
                out[k] = str(k + (la - a))
    return out
