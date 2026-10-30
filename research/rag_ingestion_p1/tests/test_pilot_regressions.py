"""Regression tests on the pilot PDFs (read-only) for the audited defects: four PAD tables of 24440102 with glued
stacked values and ISR 40089196:t00501 with values lost at the page break. Skipped when the PDFs are absent."""
from __future__ import annotations

import json
import sys
from functools import lru_cache

import pdfplumber
import pytest

from rag.ingestion_p1.shadow_run import PILOT, REPO, jsonl, process_table

sys.path.insert(0, str(REPO / "audits" / "pdf_table_quality"))
import audit_tables as audit  # noqa: E402

VERSIONS = {v["document_id"]: v for v in jsonl(PILOT / "shared" / "document_versions.jsonl")} if PILOT.is_dir() else {}
pytestmark = pytest.mark.skipif(not VERSIONS, reason="pilot_v2 not present")


@lru_cache(maxsize=None)
def shadow(table_id: str) -> tuple[dict, dict, list]:
    """(rows by index, source cells by index, P1 changes) for one table, processed by the shadow path in memory."""
    did, ref = table_id.split(":")
    pno, ti = int(ref[1:4]), int(ref[4:])
    tables = {t["table_id"]: t for t in jsonl(PILOT / "shared" / "tables.jsonl")}
    pilot_rows = sorted((r for r in jsonl(PILOT / "shared" / "table_rows.jsonl") if r["table_id"] == table_id), key=lambda r: r["row_index"])
    vocab = set(json.loads((PILOT / "reference" / "vocab.json").read_text())["words"])
    with pdfplumber.open(REPO / VERSIONS[did]["local_path"]) as pdf:
        page = pdf.pages[pno - 1]
        found = page.find_tables()[ti - 1]
        rows, changes, _ = process_table(tables[table_id], found, page, page.extract_words(), vocab, pilot_rows)
        source = found.extract()
    return {r["row_index"]: r for r in rows}, {i: r for i, r in enumerate(source, start=1)}, changes


PAD_TABLES = ["24440102:t02201", "24440102:t10401", "24440102:t10801", "24440102:t10901"]


@pytest.mark.parametrize("table_id", PAD_TABLES)
def test_no_glued_values_remain_and_every_value_matches_its_source_cell(table_id):
    rows, source, _ = shadow(table_id)
    verdicts = {v for i, row in rows.items() for col, cell in enumerate(row["cells_clean"])
                for _, v in audit.compare_cell(cell, source[i][col])}
    assert "line_merge" not in verdicts and "not_in_source" not in verdicts


@pytest.mark.parametrize("table_id", PAD_TABLES)
def test_only_line_boundaries_were_added(table_id):
    _, _, changes = shadow(table_id)
    pilot = {(c["row_index"], j): v for c in changes for j, v in enumerate(c["pilot_v2_cells"])}
    for change in changes:
        for j, cell in enumerate(change["p1_cells"]):
            if cell != pilot[(change["row_index"], j)] and j in change.get("stacked_columns", []):
                assert cell.replace("\n", "") == pilot[(change["row_index"], j)]


@pytest.mark.parametrize("table_id, row, col, expected", [
    ("24440102:t02201", 2, 1, "340.00\n18.00\n12.00\n370.00\n0.00\n370.00"),
    ("24440102:t02201", 2, 3, "100\n100\n100\n100\n0\n100"),
    ("24440102:t10401", 2, 1, "57\n37\n57\n52"),
    ("24440102:t10401", 2, 2, "126.0\n0.0\n0.0\n0.0"),
    ("24440102:t10401", 2, 7, "225.1\n122.0\n370.0\n360.0"),
    ("24440102:t10801", 3, 3, "4\n6\n6\n6\n3\n12"),
    ("24440102:t10801", 9, 3, "12\n48\n48\n15\n24"),
    ("24440102:t10901", 5, 3, "6\n20\n20\n10\n6"),
])
def test_stacked_values_are_kept_as_separate_lines(table_id, row, col, expected):
    rows, _, _ = shadow(table_id)
    assert rows[row]["cells_clean"][col] == expected


@pytest.mark.parametrize("table_id, row", [("24440102:t02201", 2), ("24440102:t10401", 2), ("24440102:t10801", 8),
                                           ("24440102:t10901", 5)])
def test_rows_with_ambiguous_label_alignment_are_flagged_not_split(table_id, row):
    rows, _, _ = shadow(table_id)
    p1 = rows[row]["p1"]
    assert p1["needs_review"] and "sub_rows" not in p1 and any(r.endswith("_alignment_ambiguous") for r in p1["reasons"])


def test_isr_t00501_lost_row_is_detected_and_filled_only_from_source_words():
    rows, source, changes = shadow("40089196:t00501")
    assert source[1][1:] == [None] * 8                                  # the detector lost these cells
    row = rows[1]
    assert row["p1"]["needs_review"] and "values_recovered" in row["p1"]["status"]
    assert row["p1"]["lost_values"]["provenance"] == "pdf_source_words"
    assert row["cells_clean"][1:] == ["No", "", "Yes", "", "Yes", "", "Yes", ""]   # value columns only; dates stay empty
    assert [c["row_index"] for c in changes] == [1]                     # no other row of the table touched


def test_isr_t00501_values_are_not_invented_when_the_source_words_are_missing():
    from rag.ingestion_p1.lost_values import check_row, column_ranges
    did = "40089196"
    with pdfplumber.open(REPO / VERSIONS[did]["local_path"]) as pdf:
        found = pdf.pages[4].find_tables()[0]
        rows = [r.cells for r in found.rows]
        words = [w for w in pdf.pages[4].extract_words() if w["text"] not in ("Yes", "No")]
        result = check_row(rows[0], found.cells, words, column_ranges(rows, 9), found.bbox)
    assert result.status == "unchanged" and result.recovered == {}


def test_spanning_recovered_value_is_flagged_as_possible_row_span():
    rows, _, _ = shadow("24440102:t10801")
    p1 = rows[9]["p1"]
    assert rows[9]["cells_clean"][0] == "12-48 months" and "col1_possible_row_span" in p1["reasons"]
    assert rows[10]["cells_clean"][0] == ""                             # not copied down to the next row
