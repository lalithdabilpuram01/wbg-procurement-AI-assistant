"""Document-type-aware heading detection and section tree (PAD, ISR, ICR, ICRR, PP).

A heading is a short line, outside tables and table-of-contents pages, that is mostly bold or clearly larger than the
body font, and matches the document type's heading grammar. Lines holding only a section number ('III.') are joined
with the following heading line.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass

from .text_extract import Line, Page

ROMAN = r"[IVX]{1,4}"
PAD_L1 = re.compile(rf"^(?:{ROMAN}\.\s*)?(STRATEGIC CONTEXT|PROJECT DEVELOPMENT OBJECTIVES?|PROJECT DESCRIPTION|IMPLEMENTATION|"
                    r"KEY RISKS.*|APPRAISAL SUMMARY|PAD DATA SHEET|DATA SHEET|PROJECT CONTEXT AND DEVELOPMENT OBJECTIVES|OUTCOME|"
                    r"KEY FACTORS.*|BANK PERFORMANCE.*|LESSONS AND RECOMMENDATIONS|PROGRAM CONTEXT AND DEVELOPMENT OBJECTIVES)\b")
NUM_ROMAN = re.compile(rf"^{ROMAN}\.\s+[A-Z]")
LETTER = re.compile(r"^[A-H]\.\s+[A-Z]")
ANNEX = re.compile(r"^(ANNEX|Annex)\s*\d+[A-Z]?\s*[:.\-–]?\s*[A-Z]")
ICRR_L1 = re.compile(r"^\d{1,2}\.\s+[A-Z]")
ICRR_L2 = re.compile(r"^[a-h]\.\s+[A-Z]")
ISR_TEMPLATE = {x.lower() for x in (
    "Key Dates", "Key Project Dates", "Project Development Objectives", "Components Table", "Components", "Overall Ratings",
    "Implementation Status and Key Decisions", "Risks", "Systematic Operations Risk-rating Tool", "Results", "PDO Indicators",
    "Intermediate Results Indicators", "Disbursements", "Key Decisions Regarding Implementation", "Restructuring History",
    "Related Project(s)", "Project Development Objective Indicators", "Overall Comments PDO", "Overall Comments IR")}
ISR_NUM = re.compile(r"^\d(?:\.\d)?\.?\s+[A-Z]")
PP_L1 = re.compile(rf"^(PROCUREMENT PLAN[^.]*|{ROMAN}\.\s+[A-Z][^.]*|General Information|Preamble|Procurement Plan for [^.]{{3,70}})$")
PP_TABLE_SECTIONS = ("WORKS", "GOODS", "NON CONSULTING SERVICES", "CONSULTING FIRMS", "INDIVIDUAL CONSULTANTS")


@dataclass
class Heading:
    page: int
    text: str
    level: int
    kind: str          # numbered | named | annex | template | plan_table
    line_index: int    # index in the document-wide line list (last line of a joined heading)
    first_line_index: int = -1   # first line of the heading ('III.' of a joined heading)


def body_size(pages: list[Page]) -> float:
    c: collections.Counter = collections.Counter()
    for p in pages:
        for ln in p.lines:
            c[ln.size] += len(ln.text)
    return c.most_common(1)[0][0] if c else 10.0


def _level(doc_type: str, text: str) -> tuple[int, str] | None:
    t = text.strip()
    if doc_type in ("PAD", "ICR"):
        if ANNEX.match(t):
            return 1, "annex"
        if PAD_L1.match(t.upper()) or (NUM_ROMAN.match(t) and t.upper() == t):
            return 1, "numbered" if NUM_ROMAN.match(t) else "named"
        if LETTER.match(t):
            return 2, "numbered"
        return None
    if doc_type == "ICRR":
        if ICRR_L1.match(t):
            return 1, "numbered"
        if ICRR_L2.match(t):
            return 2, "numbered"
        return None
    if doc_type == "ISR":
        if t.lower() in ISR_TEMPLATE:
            return 1, "template"
        if ISR_NUM.match(t):
            return (2 if re.match(r"^\d\.\d", t) else 1), "template"
        return None
    if doc_type == "PP":
        if t.upper() in PP_TABLE_SECTIONS:
            return 1, "plan_table"
        if PP_L1.match(t):
            return 1, "named"
        return None
    return None


def detect(pages: list[Page], doc_type: str) -> list[Heading]:
    body = body_size(pages)
    lines: list[Line] = [ln for p in pages for ln in p.lines]
    toc = {p.number for p in pages if p.is_toc}
    out: list[Heading] = []
    skip = -1
    for i, ln in enumerate(lines):
        annex_line = doc_type in ("PAD", "ICR") and bool(ANNEX.match(ln.text.strip()))
        if i <= skip or ln.page in toc or (ln.in_table and not annex_line and not (doc_type == "PP" and ln.text.strip().upper() in PP_TABLE_SECTIONS)):
            continue
        text = ln.text.strip()
        prominent = ln.bold or ln.size >= body + 1.5
        # 'III.' alone on a line: join with the next line on the same page
        if re.fullmatch(rf"{ROMAN}\.|[A-H]\.", text) and i + 1 < len(lines) and lines[i + 1].page == ln.page:
            nxt = lines[i + 1]
            text = f"{text} {nxt.text.strip()}"
            prominent = prominent or nxt.bold
            skip = i + 1
        # distinctive PAD/ICR patterns are accepted without bold (some PADs set section titles in plain caps)
        strict = doc_type in ("PAD", "ICR") and len(text) <= 100 and (
            bool(ANNEX.match(text)) or (bool(NUM_ROMAN.match(text)) and text.upper() == text) or bool(PAD_L1.match(text)))
        if not (3 <= len(text) <= 110) or not (prominent or strict) or re.search(r"\.{4,}\s*\d+$", text):
            continue
        if ANNEX.match(text) and "," in text and text.upper() != text:
            continue  # a sentence that starts with 'Annex 6. For the ...' is a cross-reference, not a heading
        lv = _level(doc_type, text)
        if lv and out and out[-1].page == ln.page and out[-1].text.lower() == text.lower():
            continue  # same heading repeated on the page (e.g. map caption)
        if lv:
            out.append(Heading(ln.page, text, lv[0], lv[1], skip if skip == i + 1 else i, i))  # joined heading ends on its 2nd line
    return out


def build_sections(document_id: str, pages: list[Page], headings: list[Heading]) -> list[dict]:
    """Sections from consecutive headings. Section path keeps the current level-1 heading above level-2 headings.
    Content before the first heading is 'front_matter'."""
    lines = [ln for p in pages for ln in p.lines]
    bounds = [(-1, None)] + [(h.line_index, h) for h in headings]
    sections = []
    l1 = None
    for k, (start, h) in enumerate(bounds):
        nxt = bounds[k + 1][1] if k + 1 < len(bounds) else None
        end = (nxt.first_line_index if nxt.first_line_index >= 0 else nxt.line_index) if nxt else len(lines)   # stop before the next heading's first line
        body_lines = lines[start + 1:end] if start >= 0 else lines[:end]
        if h is None and not body_lines:
            continue
        if h is not None and h.level == 1:
            l1 = h.text
        title = h.text if h else "front_matter"
        path = title if (h is None or h.level == 1 or l1 is None) else f"{l1} > {h.text}"
        first_page = h.page if h else (body_lines[0].page if body_lines else pages[0].number)
        last_page = body_lines[-1].page if body_lines else first_page
        sections.append({"section_id": f"{document_id}:s{len(sections):03d}", "document_id": document_id, "order": len(sections),
                         "heading_text": title, "heading_level": h.level if h else 0, "heading_kind": h.kind if h else "front_matter",
                         "section_path": path, "physical_page_start": first_page, "physical_page_end": last_page,
                         "_line_range": (start + 1 if start >= 0 else 0, end)})
    return sections
