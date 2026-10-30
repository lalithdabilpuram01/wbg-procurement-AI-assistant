"""The document flow offline (no Spark session, no Databricks, no Lakebase): what the tasks hand to each other, how a
file is quarantined, the lesson cards an ICR gives, and the job wiring."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import docx
import pytest
import yaml

from etl import run_documents
from etl.documents import EMBEDDING_REVISION, chunk_vectors, parse_one, understand
from etl.lakebase import pool
from ingest.layout import FOLDERS
from ingest.model import document_from_dict
from ingest.pipeline import MemorySink, Pipeline
from ingest.layout import Inbox
from ingest.reference import Reference
from rag.ingestion import evidence

REPO = Path(__file__).resolve().parents[2]
LESSON = ("Overly restrictive qualification requirements for civil works contracts reduced competition and led to a single "
          "responsive bid; qualification criteria should be proportionate to the size of each lot.")


@pytest.fixture
def reference() -> Reference:
    """One registered project, no contracts: enough to resolve and to propose lesson cards."""
    projects = {"P148850": {"title": "Expressway Development Support Project", "country_code": "ET", "country_name": "Ethiopia",
                            "sector": "Transportation", "approval_fy": "2015", "region": "Eastern and Southern Africa"}}
    return Reference(projects, [], set(), evidence.index_contracts([], set()), set(), set(), countries={"ethiopia": "ET"})


@pytest.fixture
def icr(tmp_path) -> Path:
    d = docx.Document()
    d.add_heading("Implementation Completion and Results Report", 0)
    d.add_paragraph("Ethiopia Expressway Development Support Project (P148850)")
    d.add_heading("V. LESSONS AND RECOMMENDATIONS", 1)
    d.add_paragraph(LESSON)
    path = tmp_path / "P148850_icr.docx"
    d.save(path)
    return path


def pipeline(reference: Reference) -> Pipeline:
    return Pipeline(Inbox(), MemorySink(), reference=reference)


def test_the_parsed_document_survives_the_hand_off_between_tasks(icr, reference):
    doc, reason, _ = parse_one(pipeline(reference), "icr", icr, "sha-icr")
    assert reason is None and doc.project_id == "P148850" and doc.sections
    again = document_from_dict(json.loads(json.dumps(asdict(doc), default=str)))
    assert again == doc


def test_an_icr_gives_proposed_lesson_cards_and_graph_links(icr, reference):
    doc, _, _ = parse_one(pipeline(reference), "icr", icr, "sha-icr")
    res, lessons, graph = understand(doc, reference)
    assert [c.headline[:30] for c in lessons] == ["Overly restrictive qualificati"] and lessons[0].status == "proposed"
    assert any(e.edge_type == "LESSON_FOR" and e.dst == "project:P148850" for e in graph.edges.values())


def test_a_file_in_the_wrong_folder_is_quarantined_with_a_reason(tmp_path, reference):
    sheet = tmp_path / "contracts.csv"
    sheet.write_text("a,b\n1,2\n")
    doc, reason, detail = parse_one(pipeline(reference), "icr", sheet, "sha-csv")
    assert doc is None and reason == "format_not_accepted" and "icr/" in detail


def test_publishing_refuses_the_production_branch():
    with pytest.raises(ValueError, match="production"):
        pool("projects/wbg-procurement-dev/branches/production/endpoints/primary", "wbg_kg", "test")


def test_every_chunk_gets_one_normalised_vector_in_chunk_order(icr, reference):
    doc, _, _ = parse_one(pipeline(reference), "icr", icr, "sha-icr")
    seen: list[str] = []

    def encode(texts):
        seen.extend(texts)
        return [[1.0] + [0.0] * 1023 for _ in texts]
    vectors = chunk_vectors(doc, encode)
    assert seen == [c.text for c in doc.chunks] and len(vectors) == len(doc.chunks) and len(vectors[0]) == 1024
    with pytest.raises(ValueError, match="1024"):
        chunk_vectors(doc, lambda texts: [[0.1] * 384 for _ in texts])


def test_the_job_embeds_with_the_same_model_revision_as_the_app_search():
    from rag.vector_store.encoder import MODEL_ID, snapshot_path
    from etl.documents import EMBEDDING_MODEL
    assert EMBEDDING_MODEL == MODEL_ID and EMBEDDING_REVISION == snapshot_path().name


def test_the_document_job_runs_the_five_stages_in_order_and_watches_the_inbox():
    job = yaml.safe_load((REPO / "resources" / "document_job.yml").read_text())["resources"]["jobs"]["wbg_document_ingest"]
    tasks = job["tasks"]
    assert [t["task_key"] for t in tasks] == list(run_documents.STEPS)
    assert all(t["spark_python_task"]["parameters"][0] == t["task_key"] for t in tasks)
    assert all(t["depends_on"] == [{"task_key": prev["task_key"]}] for prev, t in zip(tasks, tasks[1:]))
    assert "trigger" not in job and job["queue"]["enabled"]                       # started by etl/watch.py
    assert {t["task_key"]: t["environment_key"] for t in tasks}["embed"] == "embedding"
    assert set(FOLDERS) == {"pad", "isr", "icr", "procurement_plans", "contracts", "notices", "other"}
