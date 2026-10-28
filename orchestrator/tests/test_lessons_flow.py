"""Continuous Learning end to end: a reviewer approves lesson cards, and only approved cards reach tender checks and
monitoring answers (restated, no model call); until then the answer says how many await review."""
from __future__ import annotations

import datetime as dt

from conversation.models import Owner
from conversation.routing.routes import Route
from conversation.service import ConversationService
from conversation.store import InMemoryConversationStore
from lessons.cards import Paragraph, Source, cards
from lessons.store import InMemoryLessonStore

from wbg_orchestrator.api import UserDirectory, create_app
from wbg_orchestrator.contracts import EvidenceKind, TenderSpec
from wbg_orchestrator.conversation_bridge import ConversationFrontDoor, ConversationRunner

from fastapi.testclient import TestClient

from tests.helpers import AUTH, PolicyGuard, build_engine
from tests.test_api import Stack

ALICE = Owner(tenant_id="tenant-1", user_id="alice")
QUALIFICATION = ("Overly restrictive qualification requirements for civil works contracts reduced competition and led to a "
                 "single responsive bid; qualification criteria should be proportionate to the size of each lot.")
WEATHER = ("Insufficient attention to weather events in civil works contracts caused delays and cost increases, so works "
           "schedules and contractor work plans should build in the rainy season.")


def lesson_store(approve: bool) -> InMemoryLessonStore:
    store = InMemoryLessonStore()
    found = cards(Source("40056265", "ICRR", "P148850", "Federal Democratic Republic of Ethiopia", "Transportation", "12. Lessons"),
                  [Paragraph(QUALIFICATION, 21, 21), Paragraph(WEATHER, 21, 21)])
    store.upsert(found)
    if approve:
        for card in found:
            store.decide(card.card_id, True, "rita")
    return store


def door(store: InMemoryLessonStore):
    engine, _ = build_engine(lesson_store=store)
    conversations = InMemoryConversationStore()
    service = ConversationService(conversations, {r: ConversationRunner(engine, r, AUTH) for r in (Route.STRUCTURED_DATA,
                                                                                                    Route.DOCUMENT_RAG)}, PolicyGuard())
    return ConversationFrontDoor(service, conversations, engine, AUTH), service


TENDER = TenderSpec(project_id="P148850", procurement_method="Request for Bids", procurement_category="Works", review_type="Prior",
                    bid_window_days=45, tender_date=dt.date(2026, 12, 1), estimated_amount_usd=12e6)


def lessons_in(outcome) -> list:
    return [e for e in outcome.evidence if e.kind == EvidenceKind.LESSON_CARD]


def test_an_approved_lesson_reaches_the_tender_check_with_its_citation_and_reviewer():
    front, service = door(lesson_store(approve=True))
    outcome = front.check_tender(ALICE, service.start_thread(ALICE).thread_id, TENDER)
    found = lessons_in(outcome)
    assert found and found[0].provenance_value("reviewed_by") == "rita" and found[0].pages == (21, 21)
    assert "Overly restrictive qualification" in found[0].summary                 # the tender-design lesson ranks first
    assert "Overly restrictive qualification" in outcome.response.message


def test_proposed_lessons_never_reach_an_answer_the_answer_says_they_await_review():
    front, service = door(lesson_store(approve=False))
    outcome = front.check_tender(ALICE, service.start_thread(ALICE).thread_id, TENDER)
    [note] = lessons_in(outcome)
    assert "await a reviewer" in note.summary and "Overly restrictive" not in outcome.response.message


def test_a_monitoring_answer_gets_the_execution_lessons():
    front, service = door(lesson_store(approve=True))
    outcome = front.handle_message(ALICE, service.start_thread(ALICE).thread_id, "Is P148850 slipping?").investigation
    assert any("Insufficient attention to weather" in e.summary for e in lessons_in(outcome))


def test_only_reviewers_see_and_decide_lesson_cards(tmp_path):
    store = lesson_store(approve=False)
    stack = Stack(tmp_path, lesson_store=store)
    users = tmp_path / "users.json"
    stack.tokens["rita"] = UserDirectory.add_user(users, "rita", reviewer=True)
    stack.client = TestClient(create_app(stack.service, UserDirectory.load(users)))
    assert stack.call("GET", "/lessons").status_code == 403                                          # alice is not a reviewer
    queue = stack.call("GET", "/lessons", "rita").json()
    assert queue["counts"] == {"proposed": 2, "approved": 0, "rejected": 0} and len(queue["cards"]) == 2
    card = queue["cards"][0]
    decided = stack.call("POST", f"/lessons/{card['card_id']}", "rita", json={"approved": True, "note": "specific"}).json()
    assert decided["status"] == "approved" and decided["decided_by"] == "rita"
    assert stack.call("POST", "/lessons/unknown", "rita", json={"approved": True}).status_code == 404
    assert stack.call("GET", "/lessons?status=approved", "rita").json()["counts"]["approved"] == 1
