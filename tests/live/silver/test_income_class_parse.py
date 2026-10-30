"""Pure-Python tests of the OGHIST parser on a synthetic workbook (no Databricks needed)."""
from __future__ import annotations

import io

import openpyxl
import pytest

from silver.income_class.parse import classify, parse_oghist


def workbook(header_at: int = 5, extra_left_rows: int = 0) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.title = "Parameters"
    ws = wb.create_sheet("Country Analytical History")
    for _ in range(header_at - 1 + extra_left_rows):
        ws.append([None, "title text"])
    ws.append([None, "Bank's fiscal year:", "FY99", "FY00", "FY01"])
    ws.append([None, "Data for calendar year :", 1997, 1998, 1999])
    ws.append(["AAA", "Alpha", "L", "LM", "UM"])
    ws.append(["BBB", "Beta", "..", "..", "H"])
    ws.append(["CCC", "Gamma", "LM*", None, "H"])     # footnote + empty cell
    ws.append(["DDDf", "Delta (former)", "UM", None, None])
    ws.append([None, None])
    ws.append([None, "* footnote explaining LM*"])
    ws.append([None, "Note: classifications are fixed for the fiscal year."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_layout_detected_by_labels_not_position():
    for header_at in (3, 5, 9):
        p = parse_oghist(workbook(header_at))
        assert p.fy_header_row == header_at and p.data_year_row == header_at + 1
        assert [c["fiscal_year"] for c in p.fy_columns] == [1999, 2000, 2001]   # century resolved via data year
        assert [c["gni_data_year"] for c in p.fy_columns] == [1997, 1998, 1999]


def test_grid_complete_and_values_preserved():
    p = parse_oghist(workbook())
    assert [e["code"] for e in p.entities] == ["AAA", "BBB", "CCC", "DDDf"]
    assert len(p.cells) == 4 * 3 and not p.issues
    cell = {(c["iso3_code"], c["fiscal_year"]): c for c in p.cells}
    assert cell[("BBB", 1999)]["classification_status"] == "unclassified" and cell[("BBB", 1999)]["income_group_code"] is None
    assert cell[("BBB", 1999)]["income_group_code_raw"] == ".."
    assert cell[("CCC", 2000)]["classification_status"] == "no_entry" and cell[("CCC", 2000)]["income_group_code_raw"] is None
    c = cell[("CCC", 1999)]
    assert (c["income_group_code_raw"], c["income_group_code"], c["footnote_marker"]) == ("LM*", "LM", "*")
    assert c["footnote_text"].startswith("*")
    assert cell[("DDDf", 2000)]["classification_status"] == "no_entry"          # never filled from 1999
    assert cell[("AAA", 2000)]["source_cell"] == "D7" and str(cell[("AAA", 2000)]["fiscal_year_start_date"]) == "1999-07-01"


def test_notes_captured():
    p = parse_oghist(workbook())
    assert [n["text"][:1] for n in p.notes] == ["*", "N"]


@pytest.mark.parametrize("raw,status,code", [("L", "classified", "L"), ("UM", "classified", "UM"), ("..", "unclassified", None),
                                             (None, "no_entry", None), ("  ", "no_entry", None), ("X", "unrecognised", None)])
def test_classify(raw, status, code):
    k = classify(raw)
    assert (k["status"], k["code"]) == (status, code)


def test_missing_sheet_raises():
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError):
        parse_oghist(buf.getvalue())
