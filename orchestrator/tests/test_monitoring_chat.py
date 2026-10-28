"""Execution & Monitoring in chat: monitoring questions are detected, run the fixed monitoring plan, and are answered
by restating the cited warnings (never by a model)."""
from __future__ import annotations

import pytest

from conversation.models import Owner
from conversation.routing.routes import Route
from conversation.service import ConversationService
from conversation.store import InMemoryConversationStore

from wbg_orchestrator.contracts import EvidenceKind, InvestigationStatus
from wbg_orchestrator.conversation_bridge import ConversationFrontDoor, ConversationRunner
from wbg_orchestrator.workers.monitoring import monitoring_project

from tests.helpers import AUTH, PolicyGuard, build_engine, request

ALICE = Owner(tenant_id="tenant-1", user_id="alice")


class NoModel:
    """A responder that fails the test if the answer is sent to a model."""

    name, is_llm = "no_model", True

    def compose(self, request, evidence, report):
        raise AssertionError("a monitoring answer must not call a model")


def front_door(**kwargs):
    engine, parts = build_engine(responder=NoModel(), **kwargs)
    store = InMemoryConversationStore()
    runners = {route: ConversationRunner(engine, route, AUTH) for route in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)}
    service = ConversationService(store, runners, PolicyGuard())
    return ConversationFrontDoor(service, store, engine, AUTH), service, store


@pytest.mark.parametrize("text, project", [
    ("Is P148850 slipping?", "P148850"), ("Which packages are overdue in P148850?", "P148850"),
    ("Any early warnings for P148850", "P148850"), ("Is project P148850 on track?", "P148850"),
    ("Why was P148850 delayed?", None), ("What lessons were learned in P148850?", None),
    ("Predict delays for P148850", None), ("Is P148850 or P163452 slipping?", None), ("Are we on track?", None),
])
def test_monitoring_questions_name_exactly_one_project_and_are_not_about_causes(text, project):
    assert monitoring_project(text) == project


def test_a_monitoring_question_is_answered_from_the_cited_warnings_without_a_model():
    door, service, store = front_door()
    thread = service.start_thread(ALICE)
    reply = door.handle_message(ALICE, thread.thread_id, "Is P148850 slipping?")
    outcome = reply.investigation
    assert outcome is not None and outcome.status == InvestigationStatus.ANSWERED
    assert outcome.values["plan"]["tasks"][0]["operation"] == "watch_project"
    warnings = [e for e in outcome.evidence if e.kind == EvidenceKind.EARLY_WARNING]
    assert {e.kind for e in outcome.evidence} == {EvidenceKind.EARLY_WARNING, EvidenceKind.LESSON_CARD}   # "none reviewed yet"
    assert all(e.project_ids == ("P148850",) and e.citation for e in warnings)
    message = outcome.response.message
    assert "103 packages" in message and "ET-RSIDP-517060-CS-INDV" in message and "Substantial" in message
    assert "OLD-1" not in message                                       # stale entries are counted, not listed
    assert [t.metadata.route for t in store.turns(ALICE, thread.thread_id)] == ["project_watch", "investigation"]


def test_a_project_without_plans_or_isrs_gets_no_answer_rather_than_a_guess():
    door, service, store = front_door()
    thread = service.start_thread(ALICE)
    outcome = door.handle_message(ALICE, thread.thread_id, "Is P000001 slipping?").investigation
    assert outcome.status in (InvestigationStatus.INSUFFICIENT_EVIDENCE, InvestigationStatus.FAILED)
    assert not outcome.evidence


def test_monitoring_needs_the_planning_route():
    engine, _ = build_engine(responder=NoModel())
    asked = request("Is P148850 slipping?", routes=(Route.DOCUMENT_RAG,)).model_copy(update={"monitor_project": "P148850"})
    outcome = engine.start(asked)
    assert outcome.status == InvestigationStatus.FAILED and not outcome.evidence
