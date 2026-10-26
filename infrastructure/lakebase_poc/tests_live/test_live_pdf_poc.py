"""Live real-PDF POC checks (five tests). Run ONLY after explicit approval of real-data writes:

    LAKEBASE_LIVE_TESTS=1 LAKEBASE_PDF_POC_WRITE_APPROVED=1 uv run --locked pytest tests_live/test_live_pdf_poc.py -q -p no:cacheprovider

Writes are idempotent, verified row by row, and limited to schema wbg_rag_pdf_poc and document version
40089196:c32be7ca2d3d. Measured values are saved to results/pdf_live_tests_<UTC>.json (no identities or credentials).
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from lakebase_poc import pdf_evidence, pdf_loader, pdf_retrieval_check as rc
from lakebase_poc.connection import connect
from lakebase_poc.settings import PROJECT_DIR, load_settings

pytestmark = pytest.mark.skipif(not pdf_loader.write_approved(),
                                reason="real-data writes need LAKEBASE_LIVE_TESTS=1 and LAKEBASE_PDF_POC_WRITE_APPROVED=1")

VERSION = "40089196:c32be7ca2d3d"
METRICS: dict = {"run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


@pytest.fixture(scope="module")
def bundle():
    return pdf_evidence.build_bundle()


@pytest.fixture(scope="module")
def conn(bundle):
    with connect(load_settings(), autocommit=True) as connection:
        METRICS["first_write_inserted"] = pdf_loader.write_bundle(connection, bundle)
        yield connection
    out = PROJECT_DIR / "results" / f"pdf_live_tests_{METRICS['run_at_utc'].replace(':', '').replace('-', '')[:15]}Z.json"
    out.write_text(json.dumps(METRICS, indent=1, default=str))


def test_1_idempotent_rerun_and_unchanged_rows(conn, bundle):
    first = sum(METRICS["first_write_inserted"].values())
    assert first in (0, 311)                                   # fresh schema: all 311; earlier approved run: none
    rerun = pdf_loader.write_bundle(conn, bundle)
    METRICS["rerun_inserted"] = rerun
    assert sum(rerun.values()) == 0
    assert pdf_loader.stored_matches(conn, bundle) == []       # all 311 rows present and identical, nothing extra


def test_2_exact_search_and_hnsw_recall(conn, bundle):
    recall: dict[str, list[float]] = {}
    for query in rc.load_queries():
        expected10 = rc.exact_reference(bundle, query, 10)
        exact10 = rc.search(conn, query.vector, k=10)
        assert [u for u, _ in exact10] == [u for u, _ in expected10]
        assert max(abs(a - b) for (_, a), (_, b) in zip(exact10, expected10)) < 1e-5
        for ef in (None, 200):
            hnsw10 = rc.search(conn, query.vector, k=10, use_index=True, ef_search=ef)
            for k in (5, 10):
                truth = {u for u, _ in exact10[:k]}
                recall.setdefault(f"recall@{k}_ef_search_{ef or 'default'}", []).append(
                    len(truth & {u for u, _ in hnsw10[:k]}) / k)
    METRICS["hnsw_recall_vs_exact"] = recall
    assert min(recall["recall@5_ef_search_200"]) >= 0.8, recall


def test_3_scope_keyword_and_metadata_filters(conn, bundle):
    query = rc.load_queries()[0].vector
    in_scope = rc.search(conn, query, k=10, project_id="P148850", document_id="40089196", document_version_id=VERSION)
    assert len(in_scope) == 10
    assert rc.search(conn, query, project_id="P132833") == []            # another approved project: nothing stored
    assert rc.search(conn, query, document_version_id="40089196:000000000000") == []
    chunks = rc.search(conn, query, k=10, strategy_version="C-1.0")
    tables = rc.search(conn, query, k=10, unit_type="table")
    assert chunks and all(":C:" in u for u, _ in chunks) and tables and all(":t" in u for u, _ in tables)
    keyword = rc.search(conn, query, k=10, keyword="expressway", project_id="P148850")
    cites = {c["unit_id"]: c for c in rc.citations(conn, [u for u, _ in keyword])}
    texts = {u["unit_id"]: u["excerpt"].lower() for u in bundle.records["retrieval_units"]}
    assert keyword and all("expressway" in texts[u] and cites[u]["project_id"] == "P148850" for u, _ in keyword)
    METRICS["filters"] = {"in_scope": len(in_scope), "other_project": 0, "wrong_version": 0, "strategy_C": len(chunks),
                          "tables": len(tables), "keyword_expressway": len(keyword)}


def test_4_citations_map_to_the_original_pdf(conn, bundle):
    top = [u for u, _ in rc.search(conn, rc.load_queries()[0].vector, k=10)]
    cites = rc.citations(conn, top)
    pages = {p["page"]: p["printed_page_label"] for p in bundle.records["pages"]}
    assert {c["unit_id"] for c in cites} == set(top)
    for c in cites:
        assert (c["document_id"], c["document_version_id"], c["project_id"]) == ("40089196", VERSION, "P148850")
        assert c["sha256"] == pdf_evidence.EXPECTED_SHA256 and c["volume_path"] == pdf_evidence.VOLUME_PATH
        assert c["source_url"].startswith("https://documents") and c["source_url"].endswith(".pdf")
        assert 1 <= c["page_start"] <= c["page_end"] <= 8 and c["printed_page_label"] == pages[c["page_start"]]
        assert c["source_spans"]
    METRICS["citations_sample"] = [{k: c[k] for k in ("unit_id", "page_start", "page_end", "printed_page_label", "section_path")}
                                   for c in cites[:5]]


def test_5_project_and_document_isolation(conn):
    assert rc.projects_present(conn) == ["P148850"]
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT document_id, document_version_id FROM wbg_rag_pdf_poc.documents")
        assert cur.fetchall() == [("40089196", VERSION)]
        cur.execute("SELECT count(*) FROM wbg_rag_pdf_poc.retrieval_units WHERE document_version_id <> %s", (VERSION,))
        assert cur.fetchone()[0] == 0
    METRICS["isolation"] = {"projects": ["P148850"], "documents": ["40089196"]}
