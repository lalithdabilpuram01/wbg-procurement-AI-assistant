"""Lesson cards: which paragraphs are lessons, their tags, stable ids, review decisions that survive re-ingestion,
matching to a situation, the ingestion path (Lesson nodes in the graph) and the pilot-corpus backfill."""
from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import docx
import psycopg
import pytest

from conversation.postgres_store import DEFAULT_ADMIN_DSN
from conversation.tests.conftest import offline  # noqa: F401  (autouse: blocks external connections)
from conversation.tests.test_postgres_store import roles_available
from ingest.graph import build
from ingest.lessons import cards_for
from ingest.parsers import word
from ingest.reference import load as load_reference
from ingest.resolve import resolve
from lessons.cards import Paragraph, Source, cards, headline
from lessons.corpus import SHARED, corpus_cards
from lessons.match import Situation, relevant
from lessons.store import InMemoryLessonStore, PostgresLessonStore

SOURCE = Source("40056265", "ICRR", "P148850", "Ethiopia", "Transportation", "12. Lessons")
TEXTS = [
    "12. Lessons",
    "The following lessons are derived from the ICR with some modification of the language:",
    "Overly restrictive qualification requirements for civil works contracts reduced competition and led to a single "
    "responsive bid; qualification criteria should be proportionate to the size of each lot.",
    "Insufficient attention to weather events in civil works contract conditions and implementation might cause project",
    "delays and cost increases, so works schedules should build in the rainy season.",
    "18 World Bank (2018), ibidem.",
    "Ethiopia Expressway Development Support Project (P148850)",
]


def paragraphs() -> list[Paragraph]:
    return [Paragraph(t, 21, 21) for t in TEXTS]


# --------------------------------------------------------------------------------------------------- rules
def test_only_real_lessons_become_cards_and_page_breaks_are_joined():
    found = cards(SOURCE, paragraphs())
    assert [c.headline[:30] for c in found] == ["Overly restrictive qualificati", "Insufficient attention to weat"]
    assert found[1].text.endswith("build in the rainy season.")                    # joined across the break
    assert {"qualification", "procurement"} <= set(found[0].topics) and "tender_design" in found[0].stages
    assert found[0].categories == ("Works",) and {"timeline", "cost"} <= set(found[1].topics) and "execution" in found[1].stages


def test_card_ids_are_stable_and_titles_join_the_next_sentence():
    assert cards(SOURCE, paragraphs())[0].card_id == cards(SOURCE, paragraphs())[0].card_id
    assert headline("Cost overruns. Contracts priced before the devaluation needed variations.") == \
        "Cost overruns: Contracts priced before the devaluation needed variations."


# ---------------------------------------------------------------------------------------------- the stores
def test_a_decision_survives_the_document_being_ingested_again():
    store = InMemoryLessonStore()
    card = cards(SOURCE, paragraphs())[0]
    store.upsert([card])
    store.decide(card.card_id, True, "rita", "clear and specific")
    store.upsert([replace(card, page_start=22)])                                  # re-ingested: same text, new page
    kept = store.cards("approved")
    assert len(kept) == 1 and kept[0].decided_by == "rita" and kept[0].page_start == 22
    assert store.counts() == {"proposed": 0, "approved": 1, "rejected": 0}


@pytest.fixture
def pg_store():
    if not roles_available():
        pytest.skip("local PostgreSQL roles not bootstrapped")
    schema = f"lessons_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DEFAULT_ADMIN_DSN) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    store = PostgresLessonStore(lambda: psycopg.connect(DEFAULT_ADMIN_DSN), schema)
    store.apply_schema()
    store.apply_schema()                                                         # re-applying is non-destructive
    yield store
    with psycopg.connect(DEFAULT_ADMIN_DSN) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")                              # the temporary test schema only


def test_the_postgres_store_keeps_decisions_on_upsert(pg_store):
    found = cards(SOURCE, paragraphs())
    assert pg_store.upsert(found) == 2
    decided = pg_store.decide(found[0].card_id, False, "rita", "too generic")
    assert decided.status == "rejected" and decided.note == "too generic"
    pg_store.upsert(found)
    assert pg_store.counts() == {"proposed": 1, "approved": 0, "rejected": 1}
    assert pg_store.cards("rejected")[0].topics == found[0].topics


# ------------------------------------------------------------------------------------------------ matching
def test_only_approved_cards_for_the_stage_are_matched_best_first():
    qualification, weather = cards(SOURCE, paragraphs())
    approved = [replace(qualification, status="approved"), replace(weather, status="approved")]
    tender = Situation("tender_design", frozenset({"qualification", "procurement"}), "Works", "P148850")
    assert relevant(approved, tender)[0].card_id == qualification.card_id
    assert relevant([qualification, weather], tender) == []                       # proposed cards are never used
    assert [c.card_id for c in relevant(approved, Situation("learning"))] == []


# ------------------------------------------------------------------------------------------------ ingestion
def write_icr(path: Path) -> Path:
    d = docx.Document()
    d.add_heading("Implementation Completion and Results Report", 0)
    d.add_paragraph("Ethiopia Expressway Development Support Project (P148850)")
    d.add_heading("V. LESSONS AND RECOMMENDATIONS", 1)
    for text in TEXTS[2:3] + ["Early contractor mobilisation before the rainy season reduced delays on three road works contracts "
                              "and should be planned from the start."]:
        d.add_paragraph(text)
    d.save(path)
    return path


def test_an_ingested_completion_report_gets_lesson_nodes_in_the_graph(tmp_path):
    ref = load_reference()
    doc = word.parse(write_icr(tmp_path / "P148850_icr.docx"), "sha-icr", "icr-1", "ICR", ref)
    found = cards_for(doc, ref)
    assert len(found) == 2 and all(c.section_id for c in found)
    graph = build(doc, resolve(doc, ref), ref, found)
    lesson = f"lesson:{found[0].card_id}"
    edges = {(e.edge_type, e.dst) for e in graph.edges.values() if e.src == lesson}
    assert ("LESSON_FOR", "project:P148850") in edges and ("FOR_STAGE", "stage:tender_design") in edges
    assert any(e.edge_type == "HAS_LESSON" and e.dst == lesson for e in graph.edges.values())


@pytest.mark.skipif(not (SHARED / "paragraphs.jsonl").exists(), reason="the processed corpus is not on this machine")
def test_the_pilot_corpus_backfill_proposes_cards_for_every_project():
    found = corpus_cards()
    assert len(found) > 200 and len({c.project_id for c in found}) >= 30
    assert len({c.card_id for c in found}) == len(found) and all(c.status == "proposed" for c in found)
