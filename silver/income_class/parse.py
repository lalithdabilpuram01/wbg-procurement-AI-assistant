"""Parse the World Bank historical income classification workbook (OGHIST) — pure Python, no Spark.

Sheet `Country Analytical History`. Layout is detected, not assumed:
  * fiscal-year header row  = the row whose label cell reads "Bank's fiscal year" and which holds FYnn cells
  * data-year row           = the row whose label cell reads "Data for calendar year"
  * fiscal-year columns     = the columns holding FYnn in the header row; each must hold an integer GNI
                              data year in the data-year row
  * entity rows             = rows below the data-year row whose first cell is a country code
                              (3 upper-case letters, optionally followed by one lower-case footnote letter,
                              e.g. "YUGf") and whose second cell is a name
  * note rows               = any other non-empty rows (footnotes and the official note), kept as text
Fiscal years are resolved from the data-year row (fiscal year = data year + 2 for every column, verified),
so no two-digit-century pivot is assumed.

Cell values are kept exactly as published. A cell is one of: a classification code (L, LM, UM, H, possibly
with a footnote marker such as "LM*"), ".." (published as not classified), or empty (no entry published).
Nothing is filled from adjacent years.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter

SHEET = "Country Analytical History"
FY_LABEL = re.compile(r"bank'?s\s+fiscal\s+year", re.I)
DATA_YEAR_LABEL = re.compile(r"data\s+for\s+calendar\s+year", re.I)
FY_CELL = re.compile(r"FY(\d{2})")
ENTITY_CODE = re.compile(r"[A-Z]{3}[a-z]?")
CODE_MARKER = re.compile(r"^(L|LM|UM|H)([*†‡§]?)$")
NAMES = {"L": "Low income", "LM": "Lower middle income", "UM": "Upper middle income", "H": "High income"}


@dataclass
class ParsedWorkbook:
    """The parsed OGHIST sheet: header rows, fiscal-year columns, entities, note rows, one cell per entity and year."""
    sheet_names: list[str]
    fy_header_row: int            # 1-based Excel row
    data_year_row: int
    fy_columns: list[dict]        # {"col": 1-based, "letter", "label", "fiscal_year", "gni_data_year"}
    entities: list[dict]          # {"code", "name", "row"}
    notes: list[dict]             # {"row", "text"}
    cells: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _label(row: tuple) -> str:
    return " ".join(str(c) for c in row[:2] if isinstance(c, str))


def classify(raw) -> dict:
    """Map one published cell to code / name / status without altering the raw value."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return {"raw": None, "code": None, "name": None, "status": "no_entry", "marker": None}
    s = str(raw).strip()
    if s == "..":
        return {"raw": s, "code": None, "name": None, "status": "unclassified", "marker": None}
    m = CODE_MARKER.match(s)
    if m:
        return {"raw": s, "code": m.group(1), "name": NAMES[m.group(1)], "status": "classified", "marker": m.group(2) or None}
    return {"raw": s, "code": None, "name": None, "status": "unrecognised", "marker": None}


def parse_oghist(content: bytes) -> ParsedWorkbook:
    """Parse the OGHIST workbook bytes; raises ValueError when the sheet layout is not as expected."""
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        raise ValueError(f"sheet {SHEET!r} not found; sheets={wb.sheetnames}")
    rows = list(wb[SHEET].iter_rows(values_only=True))
    issues: list[str] = []

    fy_rows = [i for i, r in enumerate(rows) if FY_LABEL.search(_label(r)) and sum(isinstance(c, str) and bool(FY_CELL.fullmatch(c)) for c in r) >= 2]
    dy_rows = [i for i, r in enumerate(rows) if DATA_YEAR_LABEL.search(_label(r))]
    if len(fy_rows) != 1 or len(dy_rows) != 1:
        raise ValueError(f"expected one fiscal-year and one data-year header row, found {len(fy_rows)} and {len(dy_rows)}")
    fy_i, dy_i = fy_rows[0], dy_rows[0]
    if dy_i <= fy_i:
        raise ValueError("data-year row must follow the fiscal-year row")

    fy_columns = []
    for j, c in enumerate(rows[fy_i]):
        m = FY_CELL.fullmatch(c) if isinstance(c, str) else None
        if not m:
            continue
        dy = rows[dy_i][j] if j < len(rows[dy_i]) else None
        if not isinstance(dy, int):
            raise ValueError(f"fiscal-year column {c} has no integer data year (found {dy!r})")
        # A fiscal year's classification uses GNI data from two years earlier, so the FY label must equal data year + 2.
        fy = dy + 2
        if fy % 100 != int(m.group(1)):
            raise ValueError(f"{c}: data year {dy} + 2 = {fy} does not match the FY label")
        fy_columns.append({"col": j + 1, "letter": get_column_letter(j + 1), "label": c, "fiscal_year": fy, "gni_data_year": dy})

    entities: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for i in range(dy_i + 1, len(rows)):
        r = rows[i]
        if not any(c is not None and str(c).strip() for c in r):
            continue
        code, name = r[0], r[1] if len(r) > 1 else None
        if isinstance(code, str) and ENTITY_CODE.fullmatch(code.strip()) and isinstance(name, str) and name.strip():
            entities.append({"code": code.strip(), "name": name.strip(), "row": i + 1, "values": r})
        else:
            notes.append({"row": i + 1, "text": " ".join(str(c).strip() for c in r if c is not None and str(c).strip())})

    cells = []
    footnotes = {n["text"][0]: n["text"] for n in notes if n["text"][:1] in "*†‡§"}
    for e in entities:
        for fc in fy_columns:
            raw = e["values"][fc["col"] - 1] if fc["col"] - 1 < len(e["values"]) else None
            k = classify(raw)
            if k["status"] == "unrecognised":
                issues.append(f"unrecognised value {k['raw']!r} at {fc['letter']}{e['row']}")
            if k["marker"] and k["marker"] not in footnotes:
                issues.append(f"footnote marker {k['marker']!r} at {fc['letter']}{e['row']} has no note row")
            cells.append({
                "iso3_code": e["code"], "country_name": e["name"],
                "fiscal_year": fc["fiscal_year"], "fiscal_year_label": fc["label"],
                "fiscal_year_start_date": date(fc["fiscal_year"] - 1, 7, 1), "fiscal_year_end_date": date(fc["fiscal_year"], 6, 30),
                "gni_data_year": fc["gni_data_year"],
                "income_group_code_raw": k["raw"], "income_group_code": k["code"], "income_group_name": k["name"],
                "classification_status": k["status"], "is_classified": k["status"] == "classified",
                "footnote_marker": k["marker"], "footnote_text": footnotes.get(k["marker"]) if k["marker"] else None,
                "source_sheet": SHEET, "source_row_number": e["row"], "source_column_letter": fc["letter"],
                "source_cell": f"{fc['letter']}{e['row']}",
            })
        del e["values"]
    return ParsedWorkbook(wb.sheetnames, fy_i + 1, dy_i + 1, fy_columns, entities, notes, cells, issues)
