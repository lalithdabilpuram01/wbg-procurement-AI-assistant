"""Unit tests on synthetic inputs (no PDFs, no network, no real personal data)."""
from __future__ import annotations

from rag.ingestion import evidence
from rag.ingestion.chunking import count_tokens, make_chunks
from rag.ingestion.references import category_family, package_core, package_core_in_text, review_code
from rag.ingestion.sanitize import sanitize

CODES = {"RFQ", "RFB", "DIR", "QCBS", "CQS", "INDV"}


def test_package_core_variants():
    assert package_core("LK-RDA-204059-CW-RFQ") == "LK-RDA-204059-CW-RFQ"
    assert package_core("LK-RDA-106767-GO-RFQ-1") == "LK-RDA-106767-GO-RFQ"                     # sequence suffix
    assert package_core("lk-mihapclg-136348-cw- rfb") == "LK-MIHAPCLG-136348-CW-RFB"             # case, wrap space
    assert package_core("CERC-LK-MOE-246400-GO-RFB") == "LK-MOE-246400-GO-RFB"                   # programme prefix
    assert package_core("ET-ERA-221389-CW-DIRPENDING", CODES) == "ET-ERA-221389-CW-DIR"          # glued text cut
    assert package_core("MOT/RSDS/SR/02/09") is None                                             # legacy reference


def test_reference_after_spilled_text():
    assert package_core_in_text("Total LK-MIHAPCLG-164131-CW- RFB / Palaiyoothu road", {"LK"}, CODES) == ("LK-MIHAPCLG-164131-CW-RFB", True)
    assert package_core_in_text("tc. SN-PDEC-237142-CS-QCBS / Recrutement", {"SN"}, CODES) == ("SN-PDEC-237142-CS-QCBS", True)


def test_labels_multilingual():
    assert category_family("INDIVIDUAL CONSULTANTS") == category_family("Consultant Services") == "CONS"
    assert category_family("NON CONSULTING SERVICES") == "NONC"
    assert review_code("A posteriori") == "post" and review_code("Prior") == "prior"


def test_sanitize_masks_people_not_places_or_numbers():
    t, c = sanitize("Country Director: Jane Q Example Practice Director: John Sample")
    assert "Jane" not in t and "John" not in t and "Practice Director" in t and c["person_name"] == 2
    t, _ = sanitize("Name | Role | Unit\nJane Example | Team Leader | GTR01\nJohn Sample | Peer Reviewer | GTR02")
    assert "Jane" not in t and "John" not in t and "Team Leader" in t
    t, _ = sanitize("Jane Example | Member | Senior Engineer |  | GTI06")           # staff row without its header
    assert "Jane" not in t
    t, c = sanitize("The Batu (Zeway) road near Addis Ababa. Report Number: ICRR0024186, US$125,000,000.00")
    assert t.startswith("The Batu (Zeway) road near Addis Ababa") and "ICRR0024186" in t and c["person_name"] == 0
    t, c = sanitize("Telephone | 251-11-5516963 | Email | someone@example.org")
    assert "5516963" not in t and "@" not in t and t.startswith("Telephone |") and c["phone"] == 1 and c["email"] == 1


def _act(**kw):
    a = {"activity_id": "d:p1t1r1", "plan_version_id": "pv", "document_id": "d", "pdf_page": 1, "table_index": 1, "row_index": 1,
         "extraction_method": "pdf_table", "activity_reference_printed": "LK-RDA-1001-GO-RFQ", "activity_reference_normalized": "LK-RDA-1001-GO-RFQ",
         "package_reference_core": "LK-RDA-100001-GO-RFQ", "category_family": "GOOD", "review_type": "post", "process_status": "signed",
         "estimated_amount_usd": None, "actual_amount_usd": None, "signed_contract_planned": "2020-12-14", "signed_contract_actual": None,
         "value_flags": [], "uncertain_fields": []}
    a.update(kw)
    return a


def _contracts(*rows):
    cs = [{"project_id": "P1", "wb_contract_number": n, "borrower_contract_reference": ref, "procurement_category": "Goods", "procurement_method": "Request for Quotations",
           "review_type": "Post", "contract_signing_date": d, "contract_amount_usd": amt, "source_as_of_date": "2026-10-20", "source_dataset": "DS00005"}
          for n, ref, d, amt in rows]
    return evidence.index_contracts(cs, evidence.method_codes(cs))


def test_evidence_tiers():
    one = _contracts(("1", "LK-RDA-100001-GO-RFQ", "2020-12-24", "17652.71"))
    assert evidence.link("P1", _act(actual_amount_usd=17652.71, signed_contract_actual="2020-12-29"), one)[0]["evidence_status"] == "amount_verified"
    r = evidence.link("P1", _act(signed_contract_actual="2020-12-29"), one)[0]
    assert r["evidence_status"] == "candidate" and r["days_plan_actual_minus_recorded"] == 5        # 5 days is not proof
    assert evidence.link("P1", _act(signed_contract_actual="2020-12-24"), one)[0]["evidence_status"] == "exact_actual_date_supported"
    r = evidence.link("P1", _act(actual_amount_usd=52957.59), one)[0]
    assert r["evidence_status"] == "conflicting" and r["contract_amount_usd"] == 17652.71 and r["plan_actual_amount_usd"] == 52957.59
    lots = _contracts(("1", "LK-RDA-100001-GO-RFQ", "2021-11-01", "159.46"), ("2", "LK-RDA-100001-GO-RFQ-1", "2021-11-01", "4851.35"))
    rs = evidence.link("P1", _act(actual_amount_usd=5010.81), lots)
    assert {x["evidence_status"] for x in rs} == {"amount_verified"} and {x["cardinality"] for x in rs} == {"one_row_2_contracts"}
    assert evidence.link("P1", _act(package_reference_core="LK-RDA-999999-GO-RFQ"), one)[0]["evidence_status"] == "unmatched"
    fb = _act(extraction_method="text_fallback", actual_amount_usd=17652.71, uncertain_fields=["actual_amount_usd"])
    assert evidence.link("P1", fb, one)[0]["evidence_status"] == "candidate"                         # fallback values are never verified


def test_project_id_alone_never_links():
    one = _contracts(("1", "LK-RDA-100001-GO-RFQ", "2020-12-24", "100.00"))
    r = evidence.link("P1", _act(package_reference_core=None, activity_reference_normalized=None, activity_reference_printed=None), one)
    assert len(r) == 1 and r[0]["evidence_status"] == "unmatched" and r[0]["wb_contract_number"] is None


def test_chunking_tables_and_limits():
    doc = {"document_id": "d", "project_id": "P1", "country_code": "LK", "doc_type": "PAD"}
    sec = {"section_id": "d:s001", "section_path": "Annex 1", "heading_text": "Annex 1", "heading_level": 1}
    rows = [["Indicator", "Baseline", "Target"]] + [[f"Indicator {i} " + "word " * 30, "0", "100"] for i in range(40)]
    units = [{"kind": "text", "text": "Sentence one. " * 400, "page_start": 3, "page_end": 3, "pos": (3, 1)},
             {"kind": "table", "rows": rows, "page_start": 4, "page_end": 4, "pos": (4, 1), "table_index": 1}]
    ch = make_chunks(doc, sec, units, {3: "1", 4: "2"}, 0)
    assert all(c["token_count"] <= 700 for c in ch)
    tables = [c for c in ch if c["chunk_type"] == "table"]
    assert len(tables) > 1 and all("Indicator | Baseline | Target" in c["text"].split("\n")[:2] for c in tables)
    assert ch[0]["printed_page_label_start"] == "1" and ch[0]["heading_prefixed"] and count_tokens("abc") > 0
