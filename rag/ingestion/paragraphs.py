"""Paragraph reconstruction with exact source provenance.

A unit's normalised text is built from PDF lines; every character maps back to (page, offset) in Page.raw_text through
`segs`, so any substring (sentence, chunk) yields exact source spans. Normalisation is limited to: whitespace
collapse, de-hyphenation at line ends ('deve-' + 'lopment'), and spaces at line joins; nothing is reworded.

Repairs (never across sections, never through tables):
  * lines of one PyMuPDF block form one paragraph;
  * a block that continues the previous one is merged when the previous does not end a sentence and the next starts in
    lower case, or the previous ends on a connective word / comma / hyphen;
  * the same rule across a page break, skipping the footnotes and running headers in between;
  * footnotes (small font, lower page) become separate 'footnote' units and do not break paragraphs.
"""
from __future__ import annotations

import re

from .text_extract import Page

TERMINAL = re.compile(r"[.!?:;”\"’)\]]\s*$")
LIST_START = re.compile(r"^\s*(?:[•●▪◦\-–]\s|\(?[a-zA-Z0-9]{1,4}[.)]\s|\(?[ivx]{1,4}\)\s)")
CONNECTIVE_END = re.compile(r"(?:\b(?:the|of|and|to|in|for|a|an|by|with|on|at|from|or|as|its|their|is|are|was|were|be|that|which|this|these|"
                            r"under|into|between|including|such|has|have|been|will|would|than|per|its|our)|[,–\-])\s*$", re.I)
ABBREV = {"e.g", "i.e", "no", "nos", "mr", "ms", "mrs", "dr", "para", "paras", "sr", "jr", "st", "vs", "etc", "fig", "figs", "vol", "op", "cf", "approx",
          "inc", "ltd", "co", "corp", "dept", "govt", "u.s", "p", "pp", "art", "sec", "ch", "no.", "est"}


def _new(kind: str, section_id: str, page: int) -> dict:
    return {"kind": kind, "section_id": section_id, "text": "", "segs": [], "page_start": page, "page_end": page, "ops": set()}


def _append(u: dict, line, joiner: str) -> None:
    t = line.text
    if u["text"]:
        if joiner == "dehyphen":
            u["text"] = u["text"][:-1]
            # the removed '-' belonged to the previous segment
            ns, ne, pg, rs = u["segs"][-1]
            u["segs"][-1] = (ns, ne - 1, pg, rs)
            u["ops"].add("dehyphenation")
        else:
            u["text"] += " "
    ns = len(u["text"])
    u["text"] += t
    u["segs"].append((ns, ns + len(t), line.page, line.start))
    u["page_end"] = line.page


def _continues(prev_text: str, next_text: str) -> bool:
    if not prev_text or TERMINAL.search(prev_text) or LIST_START.match(next_text):
        return False
    return next_text[:1].islower() or bool(CONNECTIVE_END.search(prev_text))


def units(pages: list[Page], sections: list[dict]) -> list[dict]:
    """Ordered units per section: heading, text, footnote. Table lines are excluded (tables have their own records)."""
    lines = [ln for p in pages for ln in p.lines]
    out: list[dict] = []
    for s in sections:
        a, b = s["_line_range"]
        sec_units: list[dict] = []
        if s["heading_level"]:
            h = _new("heading", s["section_id"], s["physical_page_start"])
            hl = [ln for ln in lines[max(0, a - 2):a] if ln.text.strip() and ln.text.strip() in s["heading_text"]] or lines[a - 1:a]
            for ln in hl:
                _append(h, ln, " ")
            sec_units.append(h)
        cur = None
        last_text = None
        prev_line = None
        for ln in lines[a:b]:
            if ln.in_table:
                cur = None
                last_text = None          # a table interrupts paragraphs
                prev_line = ln
                continue
            if ln.footnote:
                if cur is None or cur["kind"] != "footnote" or prev_line is None or prev_line.block != ln.block or prev_line.page != ln.page:
                    cur = _new("footnote", s["section_id"], ln.page)
                    sec_units.append(cur)
                    _append(cur, ln, " ")
                else:
                    _append(cur, ln, "dehyphen" if cur["text"].endswith("-") and ln.text[:1].islower() else " ")
                prev_line = ln
                continue
            same_block = cur is not None and cur["kind"] == "text" and prev_line is not None and not prev_line.footnote \
                and not prev_line.in_table and prev_line.page == ln.page and prev_line.block == ln.block
            if same_block:
                _append(cur, ln, "dehyphen" if cur["text"].endswith("-") and ln.text[:1].islower() else " ")
            elif last_text is not None and _continues(last_text["text"], ln.text):
                target = last_text
                if ln.page != target["page_end"]:
                    target["ops"].add("joined_across_page")
                else:
                    target["ops"].add("joined_across_block")
                _append(target, ln, "dehyphen" if target["text"].endswith("-") and ln.text[:1].islower() else " ")
                cur = target
            else:
                cur = _new("text", s["section_id"], ln.page)
                sec_units.append(cur)
                _append(cur, ln, " ")
            last_text = cur
            prev_line = ln
        out.extend(u for u in sec_units if u["text"].strip())
    for k, u in enumerate(out):
        u["unit_id"] = k
        u["ops"] = sorted(u["ops"])
    return out


def spans(u: dict, a: int, b: int) -> list[dict]:
    """Exact source spans (page, start, end in Page.raw_text) of unit text[a:b]."""
    res: list[dict] = []
    for ns, ne, pg, rs in u["segs"]:
        lo, hi = max(a, ns), min(b, ne)
        if lo >= hi:
            continue
        st, en = rs + (lo - ns), rs + (hi - ns)
        if res and res[-1]["page"] == pg and st - res[-1]["end"] <= 1:
            res[-1]["end"] = en
        else:
            res.append({"page": pg, "start": st, "end": en})
    return res


def sentences(text: str) -> list[tuple[int, int]]:
    """Sentence offsets. A split needs terminal punctuation, whitespace and an upper-case / digit / bracket start, and
    is suppressed after common abbreviations, initials and decimal numbers."""
    out, start = [], 0
    for m in re.finditer(r"[.!?;](?=\s+[\"“(\[]?[A-Z0-9])", text):
        end = m.end()
        prev = re.search(r"([A-Za-z.]+)\.$", text[:end])
        tok = prev.group(1).lower() if prev else ""
        if tok in ABBREV or re.fullmatch(r"[a-z]", tok) or re.search(r"\b[A-Z]\.$", text[:end]) or re.search(r"US\$?\.$", text[:end]):
            continue
        out.append((start, end))
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
    if start < len(text):
        out.append((start, len(text)))
    return [(a, b) for a, b in out if text[a:b].strip()]


def ends_sentence(t: str) -> bool:
    return bool(TERMINAL.search(t))
