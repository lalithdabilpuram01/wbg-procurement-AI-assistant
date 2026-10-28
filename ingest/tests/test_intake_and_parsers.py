"""Intake (formats from bytes, document types from content), hierarchy and chunking, Word and tabular parsers."""
from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from ingest import detect, hierarchy
from ingest.model import Quarantined
from ingest.parsers import tabular, word
from ingest.reference import load as load_reference
from ingest.tests.conftest import CONTRACTS_CSV, PROJECT, write_pad_docx


# ------------------------------------------------------------------------------------------------- intake
@pytest.mark.parametrize("content, expected", [
    (b"%PDF-1.7 ...", "pdf"), (b"\x89PNG\r\n\x1a\n...", "image"), (b"\xff\xd8\xff\xe0...", "image"),
    (b'[{"a": 1}]', "json"), (b'{"a": 1}\n{"a": 2}\n', "json"), (b"a,b,c\n1,2,3\n4,5,6\n", "csv"),
])
def test_the_format_comes_from_the_bytes_not_the_extension(tmp_path, content, expected):
    path = tmp_path / "file.txt"
    path.write_bytes(content)
    assert detect.sniff(path) == expected


def test_office_files_are_told_apart_by_their_contents(tmp_path):
    word_file = write_pad_docx(tmp_path / "a.bin")
    book = openpyxl.Workbook()
    book.save(tmp_path / "b.bin")
    assert detect.sniff(word_file) == "docx" and detect.sniff(tmp_path / "b.bin") == "xlsx"


@pytest.mark.parametrize("content, reason", [
    (b"", "empty_file"), (b"\xd0\xcf\x11\xe0legacy", "legacy_office_format"), (b"just some prose without structure", "unsupported_format"),
    (b"PK\x03\x04broken", "corrupt_file"),
])
def test_unusable_files_are_refused_with_a_reason(tmp_path, content, reason):
    path = tmp_path / "x"
    path.write_bytes(content)
    with pytest.raises(Quarantined) as refused:
        detect.sniff(path)
    assert refused.value.reason == reason


def test_document_types_are_read_from_the_cover_and_checked_against_the_folder():
    assert detect.classify("Document of The World Bank\nPROJECT APPRAISAL DOCUMENT") == "PAD"
    assert detect.classify("Implementation Status & Results Report") == "ISR"
    assert detect.classify("ICR Review\nIndependent Evaluation Group") == "ICRR"
    assert detect.check_type("ICR", "ICRR") == ("ICRR", [])                     # an IEG review dropped in icr/ is fine
    assert detect.check_type("PAD", None)[1] == ["type_unconfirmed: the content does not name its document type"]
    with pytest.raises(Quarantined, match="looks like ISR"):
        detect.check_type("PAD", "ISR")
    with pytest.raises(Quarantined, match="type_unknown"):
        detect.check_type(None, None)
    assert detect.language("Le projet vise à améliorer la passation des marchés dans les régions") == "fr"


# ---------------------------------------------------------------------------------------------- hierarchy
@pytest.mark.parametrize("line, level", [
    ("3.2 Procurement Arrangements", 2), ("IV. BANK PERFORMANCE", 1), ("B. Key Risks", 2), ("Annex 2: Costs", 1),
    ("LESSONS LEARNED", 1), ("1. The project will finance roads in three regions and build bridges.", None),
    ("The contractor mobilised late.", None),
])
def test_headings_are_recognised_in_plain_text(line, level):
    assert hierarchy.heading_level(line) == level


def test_sections_form_a_tree_with_paths_topics_and_pages():
    lines = [hierarchy.Line("Cover text", 1), hierarchy.Line("1. Lessons Learned", 2), hierarchy.Line("Design early.", 2),
             hierarchy.Line("1.1 Procurement", 3), hierarchy.Line("Use RFB.", 3), hierarchy.Line("2. Risks", 4), hierarchy.Line("Low.", 4)]
    sections, blocks = hierarchy.build("d1", lines)
    by_heading = {s.heading: s for s in sections}
    child, parent = by_heading["1.1 Procurement"], by_heading["1. Lessons Learned"]
    assert child.parent_id == parent.section_id and child.path == "1. Lessons Learned > 1.1 Procurement"
    assert (parent.topic, child.topic, by_heading["2. Risks"].topic) == ("lessons", "procurement", "risks")
    assert by_heading["2. Risks"].parent_id is None and (child.page_start, child.page_end) == (3, 3)
    assert sections[0].heading == "front_matter" and blocks[0].text == "Cover text"


def test_chunks_never_cross_a_section_and_carry_its_path():
    lines = [hierarchy.Line("1. Context", 1)] + [hierarchy.Line(f"Sentence {i} about roads and markets. " * 20, 1) for i in range(6)] + \
            [hierarchy.Line("", 1), hierarchy.Line("2. Risks", 2), hierarchy.Line("Capacity is low.", 2)]
    sections, blocks = hierarchy.build("d2", lines)
    chunks = hierarchy.chunk("d2", sections, blocks)
    assert len({c.section_id for c in chunks}) == 2 and len(chunks) >= 3
    assert all(c.token_count <= hierarchy.CHUNK_MAX + 50 for c in chunks)
    assert chunks[-1].text.startswith("[2. Risks]") and "Capacity is low." in chunks[-1].text


# --------------------------------------------------------------------------------------------------- word
def test_a_word_document_keeps_its_heading_tree_lists_and_tables(tmp_path):
    path = write_pad_docx(tmp_path / f"{PROJECT}_draft.docx")
    doc = word.parse(path, "sha", "pad-draft", "PAD", load_reference())
    paths = {s.path for s in doc.sections}
    assert "1. Strategic Context > 1.1 Procurement arrangements" in paths
    assert (doc.doc_type, doc.project_id, doc.document_date) == ("PAD", PROJECT, "2015-05-12")
    assert {b.kind for b in doc.blocks} >= {"heading", "paragraph", "list_item", "table_text"}
    table = doc.tables[0]
    risks = next(s for s in doc.sections if s.heading == "2. Key Risks")
    assert (table.header, table.rows, table.section_id) == (["Risk", "Rating"], [["Fiduciary", "High"]], risks.section_id)
    assert any(s.topic == "lessons" for s in doc.sections) and all(c.text.startswith("[") for c in doc.chunks)


def test_a_word_document_in_the_wrong_folder_is_refused(tmp_path):
    path = write_pad_docx(tmp_path / "x.docx", title="Implementation Status & Results Report")
    with pytest.raises(Quarantined, match="looks like ISR"):
        word.parse(path, "sha", "x", "PAD", load_reference())


# ------------------------------------------------------------------------------------------------ tabular
def test_a_contract_feed_maps_its_columns_and_validates_each_row(tmp_path):
    path = tmp_path / "feed.csv"
    path.write_text(CONTRACTS_CSV + "P132833,7777777,LK-X-2-GO-RFB,500,04/19/2021,Shopping,Beta Co,India,Goods,extra\n")
    doc = tabular.parse(path, "sha", "feed", "contract", "csv")
    first = next(r for r in doc.records if r.record_id == "1674058").fields
    assert (first["amount_usd"], first["signing_date"], first["reference_norm"]) == (197801.29, "2021-04-19", "LKRDA257577GORFB")
    assert next(r for r in doc.records if r.record_id == "9999999").fields["amount_usd"] == 1_200_000.0
    assert next(r for r in doc.records if r.record_id == "7777777").fields["signing_date"] == "2021-04-19"     # month-first
    assert next(r for r in doc.records if r.record_id == "9999999").fields["supplier_name"] == "[INDIVIDUAL]"
    assert doc.rejected_rows == [{"row": 3, "errors": ["project_id: not a project id (BADID)", "amount_usd: not an amount (abc)",
                                                       "signing_date: not a date (notadate)", "missing project_id"]}]


def test_feeds_without_required_columns_or_with_mostly_bad_rows_are_quarantined(tmp_path):
    (tmp_path / "a.csv").write_text("Amount,Date\n1,2020-01-01\n2,2020-01-02\n")
    with pytest.raises(Quarantined, match="schema_mismatch"):
        tabular.parse(tmp_path / "a.csv", "sha", "a", "contract", "csv")
    (tmp_path / "b.csv").write_text("Project ID,Contract No\nX,1\nY,2\nP148850,3\n")
    with pytest.raises(Quarantined, match="too_many_invalid_rows"):
        tabular.parse(tmp_path / "b.csv", "sha", "b", "contract", "csv")


def test_json_excel_and_unknown_feeds_are_read_and_their_type_inferred(tmp_path):
    (tmp_path / "n.json").write_text(json.dumps({"records": [{"project_id": PROJECT, "notice_no": "OP0001", "notice_type": "Invitation for Bids",
                                                              "bid_reference": "ET-ERA-1-CW-RFB", "publication_date": "14-May-2019"}]}))
    notices = tabular.parse(tmp_path / "n.json", "sha", "n", None, "json")
    assert notices.doc_type == "notice_feed" and notices.records[0].fields["publication_date"] == "2019-05-14"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Procurement plan export"])                                   # a title row above the header
    sheet.append(["Activity Reference", "Description", "Method", "Estimated Amount", "Status", "Custom"])
    sheet.append(["ET-ERA-9-CW-RFB", "Road section", "Request for Bids", 1500000, "Signed", "x"])
    book.save(tmp_path / f"{PROJECT}_plan.xlsx")
    plan = tabular.parse(tmp_path / f"{PROJECT}_plan.xlsx", "sha", "p", "plan_activity", "xlsx")
    record = plan.records[0]
    assert (plan.doc_type, record.project_id, record.fields["estimated_amount_usd"], record.fields["extra"]) == \
        ("PP", PROJECT, 1500000.0, {"Custom": "x"})
    assert any(w.startswith("unmapped_columns_kept_in_extra") for w in plan.warnings)


def test_ocr_needs_tesseract_and_says_so(tmp_path, monkeypatch):
    from PIL import Image

    from ingest.parsers import ocr
    monkeypatch.setattr(detect, "ocr_available", lambda: False)
    Image.new("RGB", (50, 50), "white").save(tmp_path / "scan.png")
    with pytest.raises(Quarantined, match="needs_ocr"):
        ocr.parse(tmp_path / "scan.png", "sha", "s", "ISR", load_reference(), "image")
