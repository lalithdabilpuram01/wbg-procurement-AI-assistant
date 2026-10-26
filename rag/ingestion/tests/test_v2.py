"""Tests for pilot_v2: unit tests of the new components on synthetic input, and integration / regression tests on the
10-PDF outputs (a forced re-run into a temporary folder and a cached restart must reproduce every output byte for byte)."""
from __future__ import annotations

import json
import re

import pytest

from rag.ingestion import config, pipeline_v2
from rag.ingestion.cell_clean import clean_cell
from rag.ingestion.paragraphs import sentences
from rag.ingestion.sanitize import TOKEN, apply_masks, mask_intervals, sanitize
from rag.ingestion.strategies import CONFIG, chunk

VOCAB = {"request", "for", "quotations", "technical", "assistance", "supply", "and", "installation", "process", "single", "stage", "envelope"}


def test_cell_repair():
    assert clean_cell("Request for Quota\ntions", VOCAB) == ("Request for Quotations", 1)
    assert clean_cell("T\nechnical Assistance", VOCAB) == ("Technical Assistance", 1)
    assert clean_cell("Supply and\ninstallation", VOCAB) == ("Supply and installation", 0)
    assert clean_cell("9,890,912.0\n7", VOCAB) == ("9,890,912.07", 1)
    assert clean_cell("Quality And Cost-\nBased Selection", VOCAB)[0] == "Quality And Cost-Based Selection"


def test_sentences_respect_abbreviations():
    t = "The project (e.g. Component 1) is rated Satisfactory. Mr. A. Smith left. US$ 5.3 million was paid. Next one"
    s = [t[a:b] for a, b in sentences(t)]
    assert s[0].endswith("Satisfactory.") and s[-1] == "Next one" and len(s) == 4


def test_mask_intervals_roundtrip():
    raw = "Country Director: Jane Q Example. Contact jane@x.org or Tel: +94 11 234 5678."
    clean, _ = sanitize(raw)
    m = mask_intervals(raw, clean)
    assert m is not None and apply_masks(raw, 0, len(raw), m) == clean
    assert TOKEN.search(apply_masks(raw, 10, 30, m))                       # a substring keeps its mask


def test_label_context_and_false_positives():
    t, _ = sanitize("Task Team Leader(s): ICR Main Contributor:\nJane Example-Doe, John Sample, Ann Q Other\nABBREVIATIONS AND ACRONYMS")
    assert t.count("[PERSON]") == 3 and "ABBREVIATIONS" in t
    t, _ = sanitize("The Team Leader of the RDA is leading the preparation of Road Sector Development Programs.")
    assert "[PERSON]" not in t                                             # prose after a role word is not a name list
    t, _ = sanitize("Practice Manager | Country Director\n |  \nJane Example | John Sample")
    assert t.count("[PERSON]") == 2                                         # value row below a spacer row
    t, _ = sanitize("Contact: Mr. Example | Title: Director")
    assert "Example" not in t


def _units():
    s1 = "Alpha beta gamma delta. " * 60
    u1 = {"kind": "text", "section_id": "d:s001", "text": s1.strip(), "segs": [(0, len(s1.strip()), 1, 0)], "page_start": 1, "page_end": 1, "masks": []}
    s2 = "Epsilon zeta eta theta iota. " * 90
    u2 = {"kind": "text", "section_id": "d:s001", "text": s2.strip(), "segs": [(0, 1300, 1, 2000), (1300, len(s2.strip()), 2, 0)],
          "page_start": 1, "page_end": 2, "masks": []}
    h = {"kind": "heading", "section_id": "d:s002", "text": "B. Next", "segs": [(0, 7, 2, 3000)], "page_start": 2, "page_end": 2, "masks": []}
    u3 = {"kind": "text", "section_id": "d:s002", "text": "Kappa lambda mu.", "segs": [(0, 16, 2, 3010)], "page_start": 2, "page_end": 2, "masks": []}
    return [u1, u2, h, u3]


def test_strategies_invariants():
    doc = {"document_id": "d", "project_id": "P1", "country_code": "LK", "doc_type": "PAD"}
    secs = [{"section_id": "d:s001", "section_path": "I. A", "heading_text": "I. A", "heading_level": 1, "physical_page_start": 1},
            {"section_id": "d:s002", "section_path": "I. A > B. Next", "heading_text": "B. Next", "heading_level": 2, "physical_page_start": 2}]
    for s in "ABC":
        ch, parents = chunk(s, doc, secs, _units(), {1: "1"}, {})
        assert ch and len({c["chunk_id"] for c in ch}) == len(ch)
        for c in ch:
            assert len({c["section_id"]}) == 1 and c["source_spans"]
            if s == "B":
                assert len({x["page"] for x in c["source_spans"]}) == 1      # page-aware
            if s == "C":
                assert c["token_count"] <= CONFIG["C"]["child_max"] + CONFIG["C"]["overlap_max"] and c["parent_id"]
        if s == "C":
            assert all(p["token_count"] <= CONFIG["C"]["parent_max"] + 20 for p in parents)
            assert {c["parent_id"] for c in ch} <= {p["chunk_id"] for p in parents}


OUT = config.OUTPUT_V2
needs = pytest.mark.skipif(not (OUT / "run_manifest.json").exists(), reason="pilot_v2 outputs not present")


def ld(p):
    return [json.loads(x) for x in (OUT / p).read_text().splitlines()]


@needs
def test_v2_deterministic_and_restartable(tmp_path):
    first = pipeline_v2.run(force=True, out_dir=tmp_path)
    second = pipeline_v2.run(force=False, out_dir=tmp_path)
    committed = json.loads((OUT / "run_manifest.json").read_text())["outputs"]
    assert set(second["document_status"].values()) == {"cached"}
    assert first["outputs"] == second["outputs"] == committed


@needs
def test_v2_validation_gates():
    m = json.loads((OUT / "validation" / "metrics_v2.json").read_text())
    for s, r in m["strategies"].items():
        ok, tot = r["citation_spans_verified"]
        assert ok == tot and r["paragraph_alnum_characters_not_covered"] == 0 and r["section_boundary_violations"] <= 1
        assert r["citation_probe_vs_pdf"].get("found_on_other_page", 0) <= 1
    assert m["strategies"]["A"]["mid_sentence_ends"] == 0 and m["strategies"]["C"]["mid_sentence_ends"] == 0
    assert m["strategies"]["B"]["chunks_spanning_pages"] == 0
    integ = m["strategies"]["C"]["parent_child_integrity"]
    assert integ["children"] == integ["parent_exists"] == integ["child_inside_parent"] == integ["child_section_in_parent"]
    assert m["strategies"]["C"]["parents"]["cross_top_level_section"] == 0
    assert all(v["recall"] == 1.0 for v in m["reference_recall"].values())
    rec = m["reconciliation"]
    assert rec["activities"]["same_ids"] and rec["evidence_links"]["same_status_per_link"] == rec["evidence_links"]["v1"] == 243
    t = m["tables"]["continuation_checks"]
    assert t["same_column_count"] == t["continuations"] and t["step_continuation_first_row_has_reference"] == t["step_continuations"]
    pv = m["tables"]["plan_rows_vs_activities"]
    assert pv["actual_amount_in_row"] == pv["actual_amount_checked"] and pv["reference_in_row"] == pv["reference_checked"]
    assert all(v["ground_truth_names_present"] == 0 for v in m["privacy"]["possible_misses"].values())


@needs
def test_v2_outputs_sanitised_and_restricted_separate():
    from rag.ingestion.sanitize import EMAIL, PHONE
    for f in ("shared/paragraphs.jsonl", "strategies/A/chunks.jsonl", "strategies/B/chunks.jsonl", "strategies/C/chunks.jsonl", "strategies/C/parents.jsonl"):
        for r in ld(f):
            assert not EMAIL.search(r["text"]) and not PHONE.search(r["text"])
    assert (OUT / "restricted" / "README.txt").exists()
    for f in ("shared/tables.jsonl", "shared/table_rows.jsonl", "shared/plan_activities.jsonl"):
        assert "cells_original" not in (OUT / f).read_text()


@needs
def test_v2_tables_and_activities():
    tabs = {t["table_id"]: t for t in ld("shared/tables.jsonl")}
    acts = {a["package_reference_core"]: a for a in ld("shared/plan_activities.jsonl") if a["package_reference_core"]}
    assert acts["ET-ERA-147679-CS-QCBS"]["method_clean"] == "Quality And Cost-Based Selection"
    assert acts["ET-ERA-147679-CS-QCBS"]["description_clean"].startswith("Technical Assistance for Enhancement")
    for a in acts.values():
        assert a["table_id"] in tabs and tabs[a["table_id"]]["kind"] == "step_plan"
    cont = [t for t in tabs.values() if t["continuation_of"] and t["header_rows"] == 0]
    assert cont and all(t["header_inherited"] and t["columns"] == tabs[t["continuation_of"]]["columns"] for t in cont if tabs[t["continuation_of"]]["header_rows"])
    assert all(re.match(r"^.+:t\d{5}$", t) for t in tabs)
