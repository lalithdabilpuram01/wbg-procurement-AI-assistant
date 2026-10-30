"""The automated run (quarantine, ledger, duplicates, failures), the graph topology, and the Knowledge Graph store on a
temporary local PostgreSQL schema (the same SQL as Lakebase, with real[] in place of pgvector)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import psycopg
import pytest

from conversation.postgres_store import DEFAULT_ADMIN_DSN
from tests.conversation.test_postgres_store import roles_available
from ingest import detect
from ingest.graph import Graph, GraphError, build
from ingest.layout import FOLDERS
from ingest.parsers import tabular, word
from ingest.pipeline import MemorySink, Pipeline
from ingest.reference import load as load_reference
from ingest.resolve import resolve
from ingest.store import KnowledgeGraphStore
from tests.ingest.conftest import CONTRACTS_CSV, PROJECT, write_pad_docx

FAKE_VECTOR = [0.1] * 4


def fake_embed(texts):
    return [FAKE_VECTOR for _ in texts]


# ------------------------------------------------------------------------------------------------ pipeline
def test_a_mixed_drop_is_routed_loaded_or_quarantined_with_reasons(inbox, monkeypatch):
    monkeypatch.setattr(detect, "ocr_available", lambda: False)
    write_pad_docx(inbox.folder("pad") / f"{PROJECT}_pad.docx")
    (inbox.folder("contracts") / "awards.csv").write_text(CONTRACTS_CSV)
    (inbox.folder("pad") / "awards_in_wrong_folder.csv").write_text(CONTRACTS_CSV)
    write_pad_docx(inbox.folder("isr") / "really_a_pad.docx", project="P132833")    # different bytes: not a duplicate
    (inbox.folder("other") / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0" + b"0" * 64)
    sink = MemorySink()
    report = Pipeline(inbox, sink, fake_embed).run()
    status = {o["file"]: (o["status"], o["reason"]) for o in report["outcomes"]}
    assert status == {f"{PROJECT}_pad.docx": ("processed", None), "awards.csv": ("processed", None),
                      "awards_in_wrong_folder.csv": ("quarantined", "format_not_accepted"),
                      "really_a_pad.docx": ("quarantined", "type_mismatch"), "old.doc": ("quarantined", "legacy_office_format")}
    assert inbox.pending() == []                                                       # every file left the drop folders
    reason = json.loads((inbox.folder("_quarantine") / "isr" / "really_a_pad.docx.reason.json").read_text())
    assert reason["reason"] == "type_mismatch" and "PAD" in reason["what_to_do"]
    assert len((inbox.folder("_ledger") / "ledger.jsonl").read_text().splitlines()) == 5
    doc, graph, mentions, vectors = sink.documents[f"pad-{PROJECT.lower()}-pad"]
    assert len(vectors) == len(doc.chunks) and graph.nodes[f"project:{PROJECT}"].props["registered"] is True


def test_dropping_the_same_bytes_again_changes_nothing(inbox):
    sink = MemorySink()
    pipeline = Pipeline(inbox, sink)
    (inbox.folder("contracts") / "awards.csv").write_text(CONTRACTS_CSV)
    pipeline.run()
    (inbox.folder("contracts") / "awards_copy.csv").write_text(CONTRACTS_CSV)
    second = pipeline.run()
    assert [o["status"] for o in second["outcomes"]] == ["duplicate"] and len(sink.documents) == 1


def test_a_file_loaded_by_an_older_pipeline_version_is_processed_again(inbox):
    sink = MemorySink()
    (inbox.folder("contracts") / "awards.csv").write_text(CONTRACTS_CSV)
    sha = detect.sha256_of(inbox.folder("contracts") / "awards.csv")
    sink.sources[sha] = {"status": "processed", "pipeline": "ingest-0.9"}
    assert Pipeline(inbox, sink).run()["outcomes"][0]["status"] == "processed"


def test_an_unexpected_failure_is_quarantined_not_lost(inbox, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("parser crashed")
    monkeypatch.setattr(tabular, "parse", broken)
    (inbox.folder("contracts") / "awards.csv").write_text(CONTRACTS_CSV)
    outcome = Pipeline(inbox, MemorySink()).run()["outcomes"][0]
    assert (outcome["status"], outcome["reason"]) == ("quarantined", "pipeline_error")
    assert (inbox.folder("_quarantine") / "contracts" / "awards.csv").exists()


def test_every_folder_accepts_only_its_formats():
    assert FOLDERS["contracts"].formats == ("csv", "json", "xlsx") and "pdf" not in FOLDERS["notices"].formats
    assert set(FOLDERS["procurement_plans"].formats) >= {"pdf", "xlsx", "csv"} and "image" in FOLDERS["isr"].formats


# --------------------------------------------------------------------------------------------------- graph
def test_the_graph_joins_a_document_its_hierarchy_and_resolved_entities(tmp_path):
    ref = load_reference()
    doc = word.parse(write_pad_docx(tmp_path / f"{PROJECT}_pad.docx"), "sha", "pad-x", "PAD", ref)
    graph = build(doc, resolve(doc, ref), ref)
    edges = {(e.edge_type, e.src.split(":")[0], e.dst.split(":")[0]) for e in graph.edges.values()}
    assert {("HAS_DOCUMENT", "project", "document"), ("HAS_SECTION", "document", "section"), ("PARENT_OF", "section", "section"),
            ("HAS_CHUNK", "section", "chunk"), ("HAS_TABLE", "section", "table"), ("ABOUT_TOPIC", "section", "topic"),
            ("IN_COUNTRY", "project", "country"), ("IN_SECTOR", "project", "sector")} <= edges
    mentioned = {e.dst for e in graph.edges.values() if e.edge_type == "MENTIONS"}
    assert {"method:request-for-bids", "loan:IDA5678", "country:ET", f"project:{PROJECT}"} <= mentioned


def test_the_same_contract_from_two_sources_is_one_node(tmp_path):
    ref = load_reference()
    path = tmp_path / "awards.csv"
    path.write_text(CONTRACTS_CSV)
    feed = tabular.parse(path, "sha", "feed", "contract", "csv")
    graph = build(feed, resolve(feed, ref), ref)
    assert "contract:1674058" in graph.nodes and ref.contracts_by_number["1674058"]["project_id"] == "P132833"
    assert graph.nodes["supplier:individual-sri-lanka"].label == "[INDIVIDUAL]"                       # no person's name


def test_edges_that_break_the_topology_are_refused():
    graph = Graph()
    graph.node("chunk:a", "a")
    graph.node("project:P1", "p")
    graph.edge("HAS_SECTION", "chunk:a", "project:P1")
    with pytest.raises(GraphError, match="cannot join"):
        graph.validate()


# --------------------------------------------------------------------------------------------------- store
@pytest.fixture
def store():
    if not roles_available():
        pytest.skip("local PostgreSQL roles not bootstrapped")
    schema = f"kg_test_{uuid.uuid4().hex[:10]}"
    kg = KnowledgeGraphStore.for_dsn(DEFAULT_ADMIN_DSN, schema)
    kg.apply_schema()
    kg.apply_schema()                                                  # re-applying is non-destructive
    yield kg
    with psycopg.connect(DEFAULT_ADMIN_DSN) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")                  # the temporary test schema only


def test_the_store_loads_a_document_and_replaces_it_cleanly(store, inbox):
    pad = write_pad_docx(inbox.folder("pad") / f"{PROJECT}_pad.docx")
    Pipeline(inbox, store, fake_embed).run()
    first = store.stats()
    assert first["files"] == {"processed": 1} and first["nodes"]["Section"] >= 4 and "PARENT_OF[structural]" in first["edges"]
    write_pad_docx(pad, title="PROJECT APPRAISAL DOCUMENT (revised)")                # same name, new content
    Pipeline(inbox, store, fake_embed).run()
    second = store.stats()
    assert second["nodes"]["Chunk"] == first["nodes"]["Chunk"] and second["files"] == {"processed": 2}   # replaced, not doubled
    assert any(row["node_type"] == "Section" for row in store.neighbourhood(f"project:{PROJECT}", depth=2))


def test_records_from_different_files_are_reconciled_and_lessons_found(store, inbox, tmp_path):
    feed = tmp_path / "feed.csv"
    feed.write_text("Project ID,WB Contract No.,Contract Reference,Signing Date\nP132833,555,LK-RDA-1-CW-RFB,2021-03-01\n")
    plan = tmp_path / "P132833_plan.csv"
    plan.write_text("Activity Reference,Description,Method\nLK RDA 1 CW RFB,Road works,Request for Bids\n")
    sink_feed = tabular.parse(feed, "sha-feed", "contracts-feed", "contract", "csv")
    sink_plan = tabular.parse(plan, "sha-plan", "procurement_plans-p132833-plan", "plan_activity", "csv")
    ref = load_reference()
    for doc in (sink_feed, sink_plan):
        res = resolve(doc, ref)
        store.load(doc, build(doc, res, ref), res.mentions, None, {"sha256": doc.source_sha256, "file_name": doc.source_name,
                                                                   "folder": "test", "pipeline": "test"})
    reconciled = store.reconcile()
    assert reconciled["PLANNED_AS"] == 1 and store.reconcile()["PLANNED_AS"] == 0                    # idempotent
    lessons = tmp_path / "34345370.docx"
    write_pad_docx(lessons, project="P132833", title="Implementation Completion and Results Report")
    icr = word.parse(lessons, "sha-icr", "34345370", "ICR", ref)
    icr.document_date = "2021-06-01"
    res = resolve(icr, ref)
    store.load(icr, build(icr, res, ref), res.mentions, None, {"sha256": "sha-icr", "file_name": "x", "folder": "icr", "pipeline": "t"})
    assert store.reconcile()["TEMPORALLY_NEAR"] == 1                                                 # 92 days apart
    found = store.lessons_for_contract("555")
    assert found and found[0]["via"] == "same_project" and found[0]["path"] == "Lessons Learned" and found[0]["days_apart"] == 92


# ------------------------------------------------------------------------------------------------ real PDF
REAL_PLAN = Path(__file__).resolve().parents[2] / "rag/corpus_v3/pdf/ET/P148850/PP_2025-06-27_40025990.pdf"


@pytest.mark.skipif(not REAL_PLAN.exists(), reason="the pilot corpus PDFs are not on this machine")
def test_a_real_procurement_plan_pdf_becomes_activities_linked_to_contracts(inbox):
    import shutil
    shutil.copy(REAL_PLAN, inbox.folder("procurement_plans") / f"{PROJECT}_plan.pdf")
    sink = MemorySink()
    outcome = Pipeline(inbox, sink).run()["outcomes"][0]
    doc, graph, _, _ = sink.documents[outcome["document_id"]]
    planned = [e for e in graph.edges.values() if e.edge_type == "PLANNED_AS"]
    assert (outcome["status"], doc.doc_type, doc.modality, doc.project_id) == ("processed", "PP", "pdf_text", PROJECT)
    assert len(doc.records) >= 20 and len(planned) >= 10 and {e.match_class for e in planned} <= {"E", "EN", "P"}
    assert all(0 < e.confidence <= 1 for e in planned) and any(s.topic == "procurement" for s in doc.sections)
