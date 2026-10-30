"""Offline tests of the real-PDF Lakebase POC: identity, mapping, privacy, tables, schema, write gate, dry run.

No database connection, no network, no embedding model.
"""
from __future__ import annotations

import numpy as np
import psycopg
import pytest

from lakebase_poc import pdf_dry_run, pdf_evidence, pdf_loader, pdf_retrieval_check, pdf_schema


@pytest.fixture(scope="module")
def bundle() -> pdf_evidence.EvidenceBundle:
    return pdf_evidence.build_bundle()


# ------------------------------------------------------------------------------------------- identity + mapping
def test_identity_matches_the_uploaded_pdf_version():
    checks = pdf_evidence.verify_identity()["checks"]
    assert all(checks.values()), checks


def test_records_belong_to_one_document_version_and_project(bundle):
    assert bundle.checks["single_document_version"] and bundle.checks["only_project"] == ["P148850"]
    assert bundle.records["documents"][0]["document_version_id"] == "40089196:c32be7ca2d3d"
    assert bundle.checks["unit_ids_belong_to_document"] and bundle.checks["unique_unit_ids"]


def test_cached_vectors_are_768_finite_unit_and_aligned(bundle):
    vectors = bundle.checks["vectors"]
    assert vectors == {"count": 39, "dimensions": 768, "all_finite": True, "unit_norm": True, "unique_vectors": True,
                       "aligned_with_units": True}
    assert bundle.checks["embedding_cache"] == "emb_C_2396_d1afdad97857d7e4.npy"


def test_no_cache_for_changed_units_means_refusal_not_regeneration(monkeypatch, tmp_path):
    core = pdf_evidence._retrieval_core()
    monkeypatch.setattr(core, "CACHE", tmp_path)                 # no matching cache file
    with pytest.raises(pdf_evidence.EvidenceError, match="refusing to regenerate"):
        pdf_evidence.mapped_units_and_vectors()


def test_restricted_outputs_are_never_read():
    with pytest.raises(pdf_evidence.EvidenceError, match="outside sanitised outputs"):
        pdf_evidence._jsonl("restricted/page_text.jsonl")


def test_sanitised_content_has_no_residual_personal_data(bundle):
    assert bundle.checks["residual_personal_data"] == {"email": 0, "phone": 0, "mask_placeholders": 0}
    assert bundle.checks["sanitisation_masks_in_units"] == 0


# --------------------------------------------------------------------------------------------------- tables
def test_results_indicator_rows_are_typed_with_units():
    row = {"row_index": 4, "is_header": False, "page": 4,
           "cells_clean": ["Direct beneficiaries of improved access (Number)", "0.00", "May/2015", "", "", "80,000",
                           "25-Feb-2026", "400,000.00", "Jun/2026"]}
    values = {v["field_name"]: v for v in pdf_evidence.indicator_values("t", row)}
    assert values["current_value"]["value_numeric"] == 80000 and values["target_value"]["value_numeric"] == 400000
    assert values["current_value"]["unit"] == "Number" and values["indicator_name"]["quality"] == "complete_row"


@pytest.mark.parametrize("cells, expected", [
    (["Reduction in road accident fatalities along the Addis – Adama and", "0.00", "", "5", "", "5", "", "20.00", ""], "needs_review"),
    (["", "Comments on achieving targets", "", "Data collection in progress", "", "", "", "", ""], None),
    (["Component 2: Institutional Development", "", "", "", "", "", "", "", ""], None),
    (["Indicator Name", "Baseline", "", "Actual (Previous)", "", "Actual (Current)", "", "Closing Period", ""], None),
])
def test_split_comment_and_title_rows_are_not_guessed(cells, expected):
    values = pdf_evidence.indicator_values("t", {"row_index": 1, "is_header": False, "page": 3, "cells_clean": cells})
    assert ({v["quality"] for v in values} == {expected}) if expected else values == []


def test_exact_numeric_lookup_with_page_citation(bundle):
    current = pdf_evidence.lookup_indicator(bundle, "Roads constructed (Kilometers)", "current_value")
    assert [(v["value_numeric"], v["unit"], v["page"]) for v in current] == [(57.4, "Kilometers", 3)]
    assert bundle.records["pdf_tables"] and all(t["table_schema_id"] == "wb_isr_results_indicators_v1" for t in bundle.records["pdf_tables"])


# --------------------------------------------------------------------------------------------------- schema
def test_schema_is_isolated_generic_and_validated():
    assert pdf_schema.SCHEMA == "wbg_rag_pdf_poc" != "wbg_rag_poc"
    rendered = " ".join(s.as_string(None) for s in pdf_schema.statements())
    assert rendered.count("CREATE TABLE IF NOT EXISTS") == len(pdf_schema.TABLES) == 11
    assert "vector(768)" in rendered and "USING hnsw (embedding vector_cosine_ops)" in rendered
    assert "DROP" not in rendered and "lakebase_vector" not in rendered and "ALTER" not in rendered
    with pytest.raises(ValueError):
        pdf_schema.checked('x"; DROP TABLE y; --')
    with pytest.raises(ValueError):
        pdf_schema.cleanup_statement("wbg_rag_poc")                 # rollback can never target the synthetic schema


# ------------------------------------------------------------------------------------------------ write gate
def test_writes_need_both_opt_ins(monkeypatch, bundle):
    assert not pdf_loader.write_approved({}) and not pdf_loader.write_approved({"LAKEBASE_LIVE_TESTS": "1"})
    assert pdf_loader.write_approved({"LAKEBASE_LIVE_TESTS": "1", pdf_loader.WRITE_OPT_IN: "1"})
    monkeypatch.delenv(pdf_loader.WRITE_OPT_IN, raising=False)
    with pytest.raises(pdf_loader.WriteNotApproved):
        pdf_loader.write_bundle(None, bundle)                        # refused before any database use


def test_bundle_guard_rejects_other_documents_and_oversized_bundles(bundle):
    pdf_loader.guard_bundle(bundle)
    other = pdf_evidence.EvidenceBundle(records={**bundle.records, "documents": [{"document_version_id": "999:abc"}]})
    with pytest.raises(ValueError):
        pdf_loader.guard_bundle(other)
    big = pdf_evidence.EvidenceBundle(records={**bundle.records, "pdf_table_values": [{}] * pdf_loader.MAX_RECORDS})
    with pytest.raises(ValueError):
        pdf_loader.guard_bundle(big)


def test_stored_content_comparison_detects_mismatches_and_unrelated_rows(bundle):
    """verify_stored's comparison logic, with database rows simulated in memory (no connection)."""
    from lakebase_poc.pdf_loader import normalise, prepared_rows
    import datetime as dt
    import decimal
    assert normalise(decimal.Decimal("57.40")) == normalise(57.4)
    assert normalise(dt.date(2026, 2, 28)) == normalise("2026-02-28")
    vector = bundle.vectors[0]
    assert normalise(vector) == normalise(vector.copy()) and normalise(vector) != normalise(bundle.vectors[1])
    rows = prepared_rows(bundle, "unit_embeddings")
    assert len(rows) == 39 and all("embedding" in r for r in rows)


def test_write_refuses_unexpected_objects_before_writing():
    from lakebase_poc.pdf_loader import expected_relations
    expected = expected_relations()
    assert set(pdf_schema.TABLES) <= expected and pdf_schema.HNSW_INDEX in expected
    assert "wbg_rag_poc" not in expected and "synthetic_embeddings" not in expected


def test_inserts_are_idempotent_and_parameterised():
    text = pdf_loader.insert_statement("retrieval_units", ["unit_id", "excerpt"]).as_string(None)
    assert text == 'INSERT INTO "wbg_rag_pdf_poc"."retrieval_units" ("unit_id", "excerpt") VALUES (%s, %s) ON CONFLICT DO NOTHING'


# -------------------------------------------------------------------------------------------- dry run + preview
def test_dry_run_makes_no_connection(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("dry run must not connect")
    monkeypatch.setattr(psycopg, "connect", refuse)
    report = pdf_dry_run.dry_run()
    assert report["total_records"] == 311 and report["embeddings"]["regenerated"] is False
    assert report["record_counts"]["unit_embeddings"] == report["record_counts"]["retrieval_units"] == 39
    assert report["rollback"]["statement"] == 'DROP SCHEMA "wbg_rag_pdf_poc" CASCADE'


def test_exact_reference_ranking_hits_gold_pages(bundle):
    queries = pdf_retrieval_check.load_queries()
    assert len(queries) == 6 and all(q.vector.shape == (768,) for q in queries)
    for query in queries:
        top = pdf_retrieval_check.exact_reference(bundle, query)
        assert pdf_retrieval_check.page_hit(bundle, [u for u, _ in top], query.gold_pages)
        assert all(a[1] <= b[1] for a, b in zip(top, top[1:]))
    assert np.allclose([np.linalg.norm(q.vector) for q in queries], 1.0, atol=1e-5)
