"""Extraction 2.1.0 (stacked-value cells): opt-in mode of tables.py and the built pilot_v2_1 corpus (Step 6D)."""
from __future__ import annotations

import hashlib
import json

import pytest

from rag.ingestion import config, pipeline_v2, tables

VOCAB = {"total", "costs", "component"}


def test_default_profile_is_unchanged_extraction_2_0_0():
    assert pipeline_v2.PROFILES["2.0.0"] == (pipeline_v2.EXTRACTION_VERSION, config.OUTPUT_V2, False)
    assert pipeline_v2.EXTRACTION_VERSION == "ingest-pilot-2.0.0"
    assert pipeline_v2.PROFILES["2.1.0"] == ("ingest-pilot-2.1.0", config.OUTPUT_V2_1, True)


DATA = [["Component", "Cost", "Share"], ["1. Roads\n2. Bridges", "340.00\n18.00", "95%\n5%"], ["Total", "9,890,912.0\n7", "100%"]]


def test_default_mode_keeps_the_previous_cleaning():
    rows = tables._sanitise_table(DATA, VOCAB)[0]
    assert rows[1][1] == "340.0018.00" and rows[2][1] == "9,890,912.07"


def test_stacked_mode_keeps_independent_values_apart_and_still_joins_wraps():
    rows = tables._sanitise_table(DATA, VOCAB, stacked_cells=True)[0]
    assert rows[1] == ["1. Roads 2. Bridges", "340.00\n18.00", "95%\n5%"]
    assert rows[2] == ["Total", "9,890,912.07", "100%"]
    assert rows[0] == tables._sanitise_table(DATA, VOCAB)[0][0]


def test_stacked_rows_are_flagged_for_review_without_splitting():
    safe = tables._sanitise_table(DATA, VOCAB, stacked_cells=True)[0]
    rows, flags = tables._stacked_row_flags(DATA, VOCAB, None, None, safe, {})
    assert rows == safe and set(flags) == {2}
    assert flags[2]["needs_review"] and flags[2]["stacked_values"]["columns"] == [1, 2]


def test_privacy_guard_falls_back_when_stacked_cleaning_would_lose_a_mask(monkeypatch):
    stacked = tables._sanitise_table(DATA, VOCAB, stacked_cells=True)[0]
    masked_default = [list(r) for r in stacked]
    masked_default[1][0] = "[PERSON]"
    monkeypatch.setattr(tables, "_sanitise_table", lambda *a, **k: (masked_default, [0, 0, 0], False, None))
    rows, flags = tables._stacked_row_flags(DATA, VOCAB, None, None, stacked, {})
    assert rows[1] == masked_default[1] and flags[2] == {"sanitisation_guard_fallback": True, "needs_review": True}


def test_lost_value_flags_carry_no_text():
    flags = {"status": "values_detected_not_extracted", "columns": [1], "unassigned_words": 0, "values_withheld": True,
             "provenance": "pdf_source_words"}
    rows, out = tables._stacked_row_flags([["a", "b"]], VOCAB, None, None, [["a", "b"]], {1: flags})
    assert out[1]["lost_values"] == flags and rows == [["a", "b"]]


# ------------------------------------------------------------------------------------------------ built corpus
V2, V21 = config.OUTPUT_V2, config.OUTPUT_V2_1
built = pytest.mark.skipif(not (V21 / "run_manifest.json").is_file(), reason="pilot_v2_1 not built")


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@built
def test_manifest_records_the_version_and_cached_tokenizer():
    manifest = json.loads((V21 / "run_manifest.json").read_text())
    assert manifest["extraction_version"] == "ingest-pilot-2.1.0" and manifest["stacked_cells"] is True
    assert manifest["tokenizer"] == "tiktoken:cl100k_base" and set(manifest["document_status"].values()) == {"processed"}


@built
@pytest.mark.parametrize("name", ["shared/tables.jsonl", "shared/pages.jsonl", "shared/sections.jsonl", "shared/paragraphs.jsonl",
                                  "shared/plan_activities.jsonl", "shared/evidence_links.jsonl", "strategies/A/chunks.jsonl",
                                  "strategies/B/chunks.jsonl", "strategies/C/chunks.jsonl", "strategies/C/parents.jsonl"])
def test_structures_citations_and_typed_plan_records_are_byte_identical(name):
    assert sha(V2 / name) == sha(V21 / name)


@built
def test_same_pdfs_and_row_identifiers():
    assert [(d["document_id"], d["sha256"]) for d in jsonl(V2 / "shared/document_versions.jsonl")] == \
           [(d["document_id"], d["sha256"]) for d in jsonl(V21 / "shared/document_versions.jsonl")]
    assert [(r["table_id"], r["row_index"], r["page"]) for r in jsonl(V2 / "shared/table_rows.jsonl")] == \
           [(r["table_id"], r["row_index"], r["page"]) for r in jsonl(V21 / "shared/table_rows.jsonl")]


@built
def test_exactly_the_18_glued_cells_are_corrected_and_flagged():
    changed = []
    for old, new in zip(jsonl(V2 / "shared/table_rows.jsonl"), jsonl(V21 / "shared/table_rows.jsonl")):
        for col, (a, b) in enumerate(zip(old["cells_clean"], new["cells_clean"])):
            if a != b:
                changed.append((new["table_id"], new["row_index"], col, b))
                assert b.replace("\n", "") == a and new["extraction_flags"]["needs_review"]
                assert col in new["extraction_flags"]["stacked_values"]["columns"]
    assert len(changed) == 18 and {c[0] for c in changed} == {"24440102:t02201", "24440102:t10401", "24440102:t10801", "24440102:t10901"}
    assert ("24440102:t02201", 2, 1, "340.00\n18.00\n12.00\n370.00\n0.00\n370.00") in changed


@built
def test_recovered_page_break_values_are_flagged_but_not_added():
    rows = {(r["table_id"], r["row_index"]): r for r in jsonl(V21 / "shared/table_rows.jsonl")}
    old = {(r["table_id"], r["row_index"]): r for r in jsonl(V2 / "shared/table_rows.jsonl")}
    isr = rows[("40089196:t00501", 1)]
    assert isr["cells_clean"] == old[("40089196:t00501", 1)]["cells_clean"]
    assert isr["cells_clean"][1:] == [""] * 8 and not any(c in ("Yes", "No") for c in isr["cells_clean"])
    assert isr["extraction_flags"]["lost_values"]["values_withheld"] and isr["extraction_flags"]["lost_values"]["columns"] == [1, 3, 5, 7]
    flagged = [r for r in rows.values() if "lost_values" in r.get("extraction_flags", {})]
    assert len(flagged) == 21 and all("text" not in json.dumps(r["extraction_flags"]) for r in flagged)


@built
def test_no_row_loses_a_privacy_mask():
    masks = lambda cells: sum(c.count(m) for c in cells for m in ("[PERSON]", "[EMAIL]", "[PHONE]"))  # noqa: E731
    for old, new in zip(jsonl(V2 / "shared/table_rows.jsonl"), jsonl(V21 / "shared/table_rows.jsonl")):
        assert masks(new["cells_clean"]) >= masks(old["cells_clean"])
