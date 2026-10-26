"""Integrity tests on the frozen pilot_v1 outputs (skipped when the local corpus is absent)."""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from rag.ingestion import config, pipeline
from rag.ingestion.evidence import STATUSES
from rag.ingestion.sanitize import EMAIL, PHONE

OUT = config.OUTPUT
pytestmark = pytest.mark.skipif(not (OUT / "run_manifest.json").exists() or not config.CORPUS_MANIFEST.exists(), reason="pilot outputs not present")


def ld(name):
    return [json.loads(x) for x in (OUT / f"{name}.jsonl").read_text().splitlines()]


def test_all_documents_processed():
    m = json.loads((OUT / "run_manifest.json").read_text())
    assert len(m["document_status"]) == 10 and all(s in ("processed", "cached") for s in m["document_status"].values())


def test_v1_outputs_frozen():
    """pilot_v1 is a frozen artefact (produced by code hash recorded in its run manifest); the shared modules have since
    been improved for pilot_v2, so v1 is checked for integrity rather than re-generated."""
    m = json.loads((OUT / "run_manifest.json").read_text())
    for e in pipeline.ENTITIES:
        assert hashlib.sha256((OUT / f"{e}.jsonl").read_bytes()).hexdigest() == m["outputs"][e]["sha256"], e


def test_metadata_provenance_layers():
    for d in ld("documents"):
        assert {"api", "pdf", "inferred"} <= set(d) and d["pdf"]["own_project_id_in_text"]
    for v in ld("document_versions"):
        assert re.fullmatch(r"[0-9a-f]{64}", v["sha256"]) and not v["validation_errors"]
    for c in ld("chunks"):
        assert c["section_id"] and c["physical_page_start"] and c["extraction_version"] and c["chunk_id"].startswith(c["document_id"])


def test_plan_streams_never_chained_across_agencies():
    pvs = ld("plan_versions")
    assert {p["plan_stream_id"] for p in pvs} == {"P132833:MIHAPCLG", "P148850:ERA", "P175325:PDEC"}
    assert all(p["previous_version_id"] is None and p["pdf"]["revision_date"] for p in pvs)
    assert {p["document_id"]: p["pdf"]["revision_date"] for p in pvs} == {"33761744": "2022-03-18", "40025990": "2025-06-27", "40009757": "2025-04-29"}


def test_evidence_links_have_reference_and_provenance():
    for l in ld("evidence_links"):
        assert l["evidence_status"] in STATUSES
        assert l["document_id"] and l["pdf_page"] and l["extraction_method"]
        if l["wb_contract_number"]:
            assert l["package_reference_core"] or l["activity_reference_printed"]       # never project id alone
            assert l["table_index"] and l["row_index"]


def test_known_rows_and_corrections():
    acts = {(a["document_id"], a["package_reference_core"]): a for a in ld("plan_activities")}
    links = {(l["document_id"], l["wb_contract_number"]): l for l in ld("evidence_links") if l["wb_contract_number"]}
    assert links[("40025990", "1672571")]["evidence_status"] == "amount_verified" and links[("40025990", "1672571")]["pdf_page"] == 5
    assert links[("40025990", "1675726")]["plan_actual_amount_usd"] == 9890912.07
    assert links[("40009757", "1729238")]["evidence_status"] == "exact_actual_date_supported"
    # the WDS-text parser read a road length (1.44 km) as the actual amount; the PDF table holds 0.00
    assert acts[("33761744", "LK-MIHAPCLG-136349-CW-RFB")]["actual_amount_usd"] == 0.0
    assert acts[("40009757", "SN-PDEC-437408-CS-INDV")]["signed_contract_actual"] == "2024-09-19"


def test_no_contact_data_in_chunks():
    for c in ld("chunks"):
        assert not EMAIL.search(c["text"]) and not PHONE.search(c["text"])


def test_validation_thresholds():
    p = OUT / "validation" / "metrics.json"
    if not p.exists():
        pytest.skip("run python -m rag.ingestion.validate first")
    m = json.loads(p.read_text())
    assert all(v["recall"] == 1.0 for v in m["reference_recall"].values())
    assert all(v["recall"] is None or v["recall"] >= 0.95 for v in m["section_detection"].values())
    ok, tot = m["citation_page_accuracy"]["evidence_links_reference_on_cited_page"]
    assert ok == tot
    s = m["sanitisation"]
    assert s["staff_table_names_remaining_in_chunks"] == 0 and s["label_adjacent_names_remaining_in_chunks"] == 0
    assert m["evidence_link_accuracy"]["pilot_pdf_links"]["status_agrees"] == m["evidence_link_accuracy"]["pilot_pdf_links"]["benchmark_links"]
