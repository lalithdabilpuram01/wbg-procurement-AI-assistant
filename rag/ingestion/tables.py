"""Structured tables for every document (pdfplumber), with continuation detection across pages.

Each table: id '<document_id>:t<page><index>', page, bbox, kind (step_plan | staff | generic), header rows (original and
cleaned), column names, caption (nearest text line above, sanitised), section, and the continuation group it belongs
to. A table that starts near the top of a page, directly after a table that ended near the bottom of the previous page
with the same column count, is a continuation: it inherits that header (its first row is data unless it repeats the
header). STEP plan tables also chain by column count (their continuation tables never carry the 'Activity Reference'
header). Rows keep the pdfplumber row number, original cells (restricted store) and cleaned cells.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .cell_clean import clean_cell
from .lost_values import check_row, column_ranges
from .sanitize import sanitize
from .stacked_values import clean_cell_lines
from .text_extract import Page

SEGMENT_SEP = " ¦ "   # stands in for a stacked-value line boundary while a table's text is sanitised (stacked_cells mode)
MASK_TOKENS = ("[PERSON]", "[EMAIL]", "[PHONE]")
SECTION_LABELS = {"WORKS", "GOODS", "NON CONSULTING SERVICES", "CONSULTING FIRMS", "INDIVIDUAL CONSULTANTS"}


def _flat(c) -> str:
    return re.sub(r"\s+", " ", c or "").strip()


def _numeric_share(row: list[str]) -> float:
    vals = [c for c in row if c]
    return sum(bool(re.fullmatch(r"[-–\d.,%$()/ ]+", c)) for c in vals) / len(vals) if vals else 0.0


def _header_rows(rows: list[list[str]], kind: str) -> int:
    if kind == "step_plan":
        hi = next((i for i, r in enumerate(rows) if r and _flat(r[0]).startswith("Activity Reference")), None)
        return hi + 2 if hi is not None else 0          # category label row(s) + header + Planned/Actual row
    if not rows:
        return 0
    # title rows (one filled cell spanning the table) above the real header: the header is the first of the first six
    # rows that labels at least half of the columns with non-numeric text
    width = max(len(r) for r in rows)
    for i, r in enumerate(rows[:6]):
        cells = [_flat(c) for c in r]
        filled = [c for c in cells if c]
        if len(filled) <= 1 and width > 2:
            continue
        if i > 0 and len(filled) >= 0.5 * width and _numeric_share(cells) < 0.2:
            return i + 1
        break
    first = [_flat(c) for c in rows[0]]
    if _numeric_share(first) > 0.5 or any(re.fullmatch(r"[A-Z]{2,4}\d{2}", c) for c in first):
        return 0                                          # numbers, or a staff row with a unit code: data, not a header
    n = 1
    if len(rows) > 2:
        second = [_flat(c) for c in rows[1]]
        if sum(not c for c in first) >= 0.3 * len(first) and sum(bool(c) for c in second) >= 0.5 * len(second) and _numeric_share(second) < 0.2 \
                and all(len(c) < 40 for c in second):
            n = 2                                         # two-level header (merged cells above)
    return n


def _kind(rows: list[list[str]]) -> str:
    top = " ".join(_flat(c) for r in rows[:4] for c in r)
    if "Activity Reference" in top:
        return "step_plan"
    if re.search(r"\bName\b", top) and re.search(r"\b(Role|Title|Unit|Specializ|Responsibilit)", top):
        return "staff"
    if any(re.fullmatch(r"[A-Z]{2,4}\d{2}", _flat(c)) for r in rows for c in r):
        return "staff"
    return "generic"


def extract(document_id: str, pdf_path: Path, pages: list[Page], vocab: set[str], section_at,
            lower_vocab: set[str] | None = None, stacked_cells: bool = False) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (tables, rows, original_cells). section_at(page, y) -> section dict.
    stacked_cells (extraction 2.1.0; default off): independent values stacked in one cell keep their line boundaries
    ('340.00\n18.00') instead of being glued, and rows carry `extraction_flags` (stacked values; values that the PDF
    shows but the table detector lost - flagged only, their text is never added to the cells)."""
    tables, rows_out, originals = [], [], []
    lost_flags: dict[str, dict[int, dict]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        raw = []
        for pno, pp in enumerate(pdf.pages, start=1):
            words = None
            for ti, t in enumerate(pp.find_tables(), start=1):
                data = t.extract()
                if len(data) >= 1 and max((len(r) for r in data), default=0) >= 2:
                    raw.append((pno, ti, tuple(round(v, 1) for v in t.bbox), data, pp.height))
                    if stacked_cells:
                        words = words if words is not None else pp.extract_words()
                        lost_flags[f"{document_id}:t{pno:03d}{ti:02d}"] = _lost_value_flags(t, words)
    prev = None
    step_header_by_cols: dict[int, dict] = {}
    for pno, ti, bbox, data, height in raw:
        tid = f"{document_id}:t{pno:03d}{ti:02d}"
        ncols = max(len(r) for r in data)
        kind = _kind(data)
        cont_of = None
        hdr_n = _header_rows(data, kind)
        # STEP continuation: headerless table with the column count of an earlier STEP header
        if kind != "step_plan" and ncols in step_header_by_cols and any(re.search(r"\d{5,7}\s*-\s*(CW|GO|CS|NC|TA)\s*-", r[0] or "") for r in data if r):
            kind, cont_of, hdr_n = "step_plan", step_header_by_cols[ncols]["table_id"], 0
        elif prev and prev["page"] == pno - 1 and prev["ncols"] == ncols and bbox[1] < 0.3 * height and prev["bottom"] > 0.6 * prev["height"] \
                and prev["last_on_page"] and ti == 1:
            first = [_flat(c) for c in data[0]]
            repeats = prev["header_flat"] and first == prev["header_flat"][0]
            cont_of, kind = prev["table_id"], prev["kind"]
            hdr_n = len(prev["header_flat"]) if repeats else 0
        group = (next(t["continuation_group"] for t in tables if t["table_id"] == cont_of) if cont_of else tid)
        header_src = data[:hdr_n] if hdr_n else None
        if cont_of and not header_src:
            parent = next(t for t in tables if t["table_id"] == cont_of)
            header_clean, header_rows_n, inherited = parent["header_clean"], 0, True
        else:
            header_clean = [[clean_cell(c, vocab)[0] for c in r] for r in (header_src or [])]
            header_rows_n, inherited = hdr_n, False
        name_rows = header_clean
        ar = next((i for i, r in enumerate(header_clean) if r and r[0].startswith("Activity Reference")), None)
        if ar is not None:
            name_rows = header_clean[ar:]          # STEP: the category label row above the header is not a column name
        columns = [sanitize(c, staff_rows=False)[0] for c in _column_names(name_rows, ncols)]
        sec = section_at(pno, (bbox[1] + bbox[3]) / 2)   # midpoint: a table that carries its own section heading belongs to it
        cap = _caption(pages[pno - 1], bbox)
        rec = {"table_id": tid, "document_id": document_id, "page": pno, "index_on_page": ti, "bbox": list(bbox), "kind": kind,
               "n_columns": ncols, "n_rows_total": len(data), "header_rows": header_rows_n, "header_inherited": inherited,
               "header_clean": [sanitize(" | ".join(r), staff_rows=False)[0].split(" | ") for r in header_clean], "columns": columns,
               "continuation_of": cont_of, "continuation_group": group, "caption": sanitize(cap, personnel_context=False)[0] if cap else None,
               "section_id": sec["section_id"] if sec else None, "section_path": sec["section_path"] if sec else None,
               "n_data_rows": len(data) - header_rows_n}
        tables.append(rec)
        hint = next((t.get("_name_col") for t in tables if t["table_id"] == cont_of), None) if cont_of else None
        safe_rows, closed_rows, staff, name_col = _sanitise_table(data, vocab, hint, lower_vocab, stacked_cells)
        row_flags: dict[int, dict] = {}
        if stacked_cells:
            safe_rows, row_flags = _stacked_row_flags(data, vocab, hint, lower_vocab, safe_rows, lost_flags.get(tid, {}))
        rec["_name_col"] = name_col
        if staff and kind == "generic":
            kind = rec["kind"] = "staff"
        for ri, r in enumerate(data, start=1):
            cells = safe_rows[ri - 1]
            rows_out.append({"table_id": tid, "document_id": document_id, "page": pno, "row_index": ri, "is_header": ri <= header_rows_n,
                             "cells_clean": cells, "word_breaks_closed": closed_rows[ri - 1],
                             "sanitisation": {k: v for k, v in (("person_name", sum(c.count("[PERSON]") for c in cells)),
                                                                ("email", sum(c.count("[EMAIL]") for c in cells)),
                                                                ("phone", sum(c.count("[PHONE]") for c in cells))) if v}})
            if ri in row_flags:
                rows_out[-1]["extraction_flags"] = row_flags[ri]
            originals.append({"table_id": tid, "row_index": ri, "cells_original": [c for c in r]})
        if kind == "step_plan" and hdr_n:
            step_header_by_cols[ncols] = rec
        prev = {"table_id": tid, "page": pno, "ncols": ncols, "bottom": bbox[3], "height": height, "kind": kind,
                "header_flat": [[_flat(c) for c in r] for r in (header_src or [])] or (prev["header_flat"] if cont_of and prev else []),
                "last_on_page": ti == max(x[1] for x in raw if x[0] == pno)}
    return tables, rows_out, originals


ROLE_HDR = re.compile(r"\b(Role|Title|Unit|Responsibilit|Specializ|Specialis|Position)", re.I)
UNIT = re.compile(r"[A-Z]{2,4}\d{2}")


def _sanitise_table(data: list[list[str]], vocab: set[str], name_col_hint: int | None = None,
                    lower_vocab: set[str] | None = None, stacked_cells: bool = False) -> tuple[list[list[str]], list[int], bool, int | None]:
    """Clean every cell, then sanitise the whole table at once so a label row's context reaches the value rows below it.
    Staff tables mask only their name column (the 'Name' column under a Name/Role header, or the first cell of a row
    with a World Bank unit code), so titles, roles and procurement terms in other columns are kept.
    stacked_cells: stacked values are kept apart (SEGMENT_SEP while sanitising, '\n' in the result)."""
    cleaned = [[_clean_stacked(c, vocab) if stacked_cells else clean_cell(c, vocab) for c in r] for r in data]
    vals = [[v for v, _ in r] for r in cleaned]
    closed = [sum(n for _, n in r) for r in cleaned]
    hdr = next((i for i, r in enumerate(vals) if any(v.strip().lower() == "name" for v in r) and any(ROLE_HDR.search(v) for v in r)), None)
    name_col = [v.strip().lower() for v in vals[hdr]].index("name") if hdr is not None else name_col_hint
    name_like = lambda r: bool(r) and bool(re.match(r"^[A-Z][a-z.]", r[0] or ""))  # noqa: E731
    unit_rows = [i for i, r in enumerate(vals) if any(UNIT.fullmatch(v.strip()) for v in r) and name_like(r)]
    if unit_rows:   # letters-only unit codes ('LEGES') as the last cell, only in a table that already has unit-code rows
        last = lambda r: next((v.strip() for v in reversed(r) if v.strip()), "")  # noqa: E731
        unit_rows = sorted(set(unit_rows) | {i for i, r in enumerate(vals) if name_like(r) and re.fullmatch(r"[A-Z]{4,6}", last(r))})
    if name_col is None and unit_rows:
        name_col = 0                                      # staff rows (unit codes): the name is the first column
    staff = name_col is not None
    for i, r in enumerate(vals):
        cols = set()
        if name_col is not None and (hdr is None or i > hdr):
            cols.add(name_col)
        if i in unit_rows:
            cols.add(0)                                   # a unit-code staff row: its first cell is the name
        if hdr is not None and i < hdr and name_col_hint is not None:
            cols.add(name_col_hint)                       # rows continuing the previous page's staff table, above a new header
        for c in cols:
            if c < len(r) and r[c] and not _common_words(r[c], lower_vocab):
                r[c] = sanitize(r[c], personnel_context=True)[0]
    text = "\n".join(" | ".join(r) for r in vals)
    safe, _ = sanitize(text, staff_rows=False)
    lines = safe.split("\n")
    if len(lines) == len(vals):
        out = [ln.split(" | ") if ln.count(" | ") == len(vals[i]) - 1 else [ln] for i, ln in enumerate(lines)]
    else:   # line structure changed: sanitise row by row instead
        out = [sanitize(" | ".join(r), staff_rows=False)[0].split(" | ") for r in vals]
    if stacked_cells:
        out = [[c.replace(SEGMENT_SEP, "\n") for c in r] for r in out]
    return out, closed, staff, name_col


def _clean_stacked(raw: str | None, vocab: set[str]) -> tuple[str, int]:
    """clean_cell_lines with stacked values joined by SEGMENT_SEP (a cell without stacked values = clean_cell)."""
    cell = clean_cell_lines(raw, vocab)
    return (SEGMENT_SEP.join(cell.segments) if cell.stacked else cell.text), cell.closed


def _mask_count(cells: list[str]) -> int:
    return sum(c.count(m) for c in cells for m in MASK_TOKENS)


def _stacked_row_flags(data, vocab, hint, lower_vocab, safe_rows, lost: dict[int, dict]) -> tuple[list[list[str]], dict[int, dict]]:
    """Flags for rows with stacked values or lost values (stacked_cells mode). Privacy guard: a row whose stacked
    cleaning would carry fewer masks than the default cleaning falls back to the default row (and is flagged)."""
    default_rows = _sanitise_table(data, vocab, hint, lower_vocab)[0]
    rows, flags = [], {}
    for ri, (raw, safe, default) in enumerate(zip(data, safe_rows, default_rows), start=1):
        row_flags: dict = {}
        stacked = [j for j, c in enumerate(raw) if clean_cell_lines(c, vocab).stacked]
        if stacked and _mask_count(safe) < _mask_count(default):
            safe, row_flags["sanitisation_guard_fallback"] = default, True
        elif stacked:
            row_flags["stacked_values"] = {"columns": stacked, "format": "newline_separated",
                                           "reason": "several source values in one cell; logical rows not split (alignment not established)"}
        if ri in lost:
            row_flags["lost_values"] = lost[ri]
        if row_flags:
            flags[ri] = {**row_flags, "needs_review": True}
        rows.append(safe)
    return rows, flags


def _lost_value_flags(found, words: list[dict]) -> dict[int, dict]:
    """Rows where the PDF shows words that no detected cell holds. Flag only: no word text is stored or indexed."""
    boxes = [r.cells for r in found.rows]
    ranges = column_ranges(boxes, max(len(r) for r in boxes))
    out = {}
    for ri, cells in enumerate(boxes, start=1):
        result = check_row(cells, found.cells, words, ranges, found.bbox)
        if result.status != "unchanged":
            out[ri] = {"status": "values_missing" if result.status == "values_missing" else "values_detected_not_extracted",
                       "columns": sorted(result.recovered), "unassigned_words": len(result.unassigned_words),
                       "values_withheld": True, "provenance": "pdf_source_words"}
    return out


def _common_words(cell: str, lower_vocab: set[str] | None) -> bool:
    """True when every word of the cell also occurs in lower case in the corpus prose ('Monthly Operational Summary'):
    such a cell is not a personal name even inside a staff table's name column."""
    if not lower_vocab:
        return False
    words = [w.strip(".,'’:()").lower() for w in cell.split()]
    words = [w for w in words if len(w) > 1]
    return bool(words) and all(w in lower_vocab for w in words)


def _column_names(header: list[list[str]], ncols: int) -> list[str]:
    names = []
    for i in range(ncols):
        parts = [r[i] for r in header if i < len(r) and r[i]]
        names.append(" / ".join(parts) if parts else f"col_{i + 1}")
    # STEP 'Planned' / 'Actual' sub-headers: carry the stage name across the empty cell to the right
    out, stage = [], ""
    for i, n in enumerate(names):
        top = header[0][i] if header and i < len(header[0]) else ""
        if top:
            stage = top
        if n in ("Planned", "Actual") or n.endswith(("/ Planned", "/ Actual")):
            n = f"{stage} | {n.split(' / ')[-1]}" if stage else n
        out.append(n)
    return out


def _caption(page: Page, bbox) -> str | None:
    above = [ln for ln in page.lines if not ln.in_table and ln.y1 <= bbox[1] + 1 and bbox[1] - ln.y1 < 40]
    return max(above, key=lambda ln: ln.y1).text if above else None
