"""Acceptance 13-14: the existing conversation service as front door, isolation, memory and compression."""
from __future__ import annotations

import pytest

from conversation.models import Owner
from conversation.routing.routes import Route
from conversation.service import ConversationService
from conversation.store import InMemoryConversationStore, ThreadNotFound

from wbg_orchestrator.contracts import InvestigationStatus
from wbg_orchestrator.conversation_bridge import ConversationFrontDoor, ConversationRunner, MissingTrustedOwner, owner_ref
from wbg_orchestrator.engine import InvestigationNotFound
from wbg_orchestrator.memory import ConversationMemoryView, OwnerScopedMemoryRetriever, build_digest, digest_losses, render_digest
from wbg_orchestrator.planner import FakePlanner

from tests.helpers import AUTH, PolicyGuard, build_engine, plan, task

ALICE = Owner(tenant_id="tenant-1", user_id="alice")
BOB = Owner(tenant_id="tenant-1", user_id="bob")
DATA_QUESTION = "How many bid invitations were published in fiscal year 2024?"
COMBINED = "Compare the number of contract awards in Ethiopia with the lessons learned in the ICR documents"


def front_door(planner=None, **kwargs):
    engine, parts = build_engine(planner=planner, **kwargs)
    store = InMemoryConversationStore()
    runners = {route: ConversationRunner(engine, route, AUTH) for route in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)}
    service = ConversationService(store, runners, PolicyGuard())
    return ConversationFrontDoor(service, store, engine, AUTH), service, store, engine, parts


def test_fast_path_runs_behind_the_unchanged_conversation_service():
    door, service, store, engine, parts = front_door()
    thread = service.start_thread(ALICE)
    reply = door.handle_message(ALICE, thread.thread_id, DATA_QUESTION, execute=True)
    assert reply.status == "answered" and reply.investigation.status == InvestigationStatus.ANSWERED
    assistant = store.turns(ALICE, thread.thread_id)[-1]
    assert assistant.metadata.status == "answered" and assistant.metadata.route == "structured_data"
    assert assistant.metadata.source_objects == ["workspace.wbg_gold.vw_kpi_notice_activity"]
    assert "1234" in assistant.text and assistant.metadata.validated_sql is None          # no SQL or rows stored


def test_runner_refuses_without_a_trusted_owner():
    engine, _ = build_engine()
    runner = ConversationRunner(engine, Route.STRUCTURED_DATA, AUTH)
    with pytest.raises(MissingTrustedOwner):
        runner(DATA_QUESTION, "", False, "req_1")


def test_combined_data_and_document_question_becomes_one_investigation():
    planner = FakePlanner(plan(task("data", question="How many contract awards were made in Ethiopia in fiscal year 2024?"),
                               task("docs", worker="document_intelligence", operation="answer_document_question",
                                    question="What lessons learned are reported in the ICR documents?")))
    door, service, store, engine, parts = front_door(planner=planner)
    thread = service.start_thread(ALICE)
    reply = door.handle_message(ALICE, thread.thread_id, COMBINED)
    assert reply.conversation is None                                                  # no "which first?" question
    assert reply.investigation is not None and len(planner.calls) == 1
    request = reply.investigation.values["request"]
    assert request["mode"] == "investigation" and set(request["authorization"]["routes"]) == {"structured_data", "document_rag"}
    turns = store.turns(ALICE, thread.thread_id)
    assert [(t.role, t.metadata.route) for t in turns] == [("user", "investigation"), ("assistant", "investigation")]


def test_a_question_without_clear_intent_is_investigated_not_answered_with_a_question():
    planner = FakePlanner(plan(task("docs", worker="document_intelligence", operation="answer_document_question",
                                    question="What does the appraisal say about the Expressway?")))
    door, service, store, engine, parts = front_door(planner=planner)
    thread = service.start_thread(ALICE)
    reply = door.handle_message(ALICE, thread.thread_id, "Tell me about the Ethiopia Expressway")
    assert reply.investigation is not None and len(planner.calls) == 1
    assert all(t.metadata.status != "clarification_required" for t in store.turns(ALICE, thread.thread_id))


def test_a_prediction_request_asks_for_the_package_fields_and_runs_nothing():
    door, service, store, engine, parts = front_door()
    thread = service.start_thread(ALICE)
    seen: list[str] = []
    reply = door.handle_message(ALICE, thread.thread_id, "Predict the tender duration for a works contract in P148850",
                                on_start=seen.append)
    assert reply.status == "needs_package_details" and reply.investigation is None and seen == []
    answer = store.turns(ALICE, thread.thread_id)[-1].text
    for field in ("project ID", "procurement method", "category", "review type", "bid window", "tender date"):
        assert field in answer


def test_combined_escalation_can_be_disabled():
    engine, _ = build_engine()
    store = InMemoryConversationStore()
    service = ConversationService(store, {}, PolicyGuard())
    door = ConversationFrontDoor(service, store, engine, AUTH, allow_combined=False)
    thread = service.start_thread(ALICE)
    assert door.handle_message(ALICE, thread.thread_id, COMBINED).investigation is None


# ------------------------------------------------------------------------------------------------ acceptance 13
def test_cross_user_isolation_of_threads_investigations_approvals_and_memory():
    planner = FakePlanner(plan(task("metric", operation="describe_metric", question="Which metric?"),
                               task("count", deps=("metric",), execute=True, question=DATA_QUESTION)))
    door, service, store, engine, parts = front_door(planner=planner)
    alice_thread = service.start_thread(ALICE)
    service.record_decision(ALICE, alice_thread.thread_id, "constraint", "Only use fiscal year 2024 data for project P148850.")
    reply = door.handle_message(ALICE, alice_thread.thread_id,
                                "Investigate how many bid invitations were published in fiscal year 2024 and then explain the metric")
    pending = reply.investigation
    assert pending.status == InvestigationStatus.AWAITING_APPROVAL
    with pytest.raises(ThreadNotFound):
        door.handle_message(BOB, alice_thread.thread_id, DATA_QUESTION)
    with pytest.raises(ThreadNotFound):
        door.approve(BOB, alice_thread.thread_id, pending.thread_id, pending.pending_approval.approval_id, True)
    bob_thread = service.start_thread(BOB)
    with pytest.raises(InvestigationNotFound):
        door.approve(BOB, bob_thread.thread_id, pending.thread_id, pending.pending_approval.approval_id, True)
    assert OwnerScopedMemoryRetriever(store).retrieve(BOB, "bid invitations fiscal year 2024") == []
    assert OwnerScopedMemoryRetriever(store).retrieve(ALICE, "bid invitations fiscal year 2024")
    assert parts["workflow"].calls == []
    done = door.approve(ALICE, alice_thread.thread_id, pending.thread_id, pending.pending_approval.approval_id, True)
    assert done.status == InvestigationStatus.ANSWERED
    assert any("approved" in d.statement for d in service._store.decisions(ALICE, alice_thread.thread_id))


def test_memory_view_reads_only_the_owners_recent_messages_summary_and_decisions():
    door, service, store, engine, parts = front_door()
    thread = service.start_thread(ALICE)
    door.handle_message(ALICE, thread.thread_id, DATA_QUESTION, execute=True)
    view = ConversationMemoryView(store)
    assert [t.role for t in view.recent_messages(ALICE, thread.thread_id)] == ["user", "assistant"]
    with pytest.raises(ThreadNotFound):
        view.recent_messages(BOB, thread.thread_id)
    assert view.budget.available_for_context > 0 and view.decisions(ALICE, thread.thread_id) == []


# ------------------------------------------------------------------------------------------------ acceptance 14
def test_compression_preserves_constraints_approvals_projects_evidence_and_unresolved_tasks():
    planner = FakePlanner(plan(task("metric", operation="describe_metric", question="Which metric?"),
                               task("count", deps=("metric",), execute=True, question=DATA_QUESTION)))
    door, service, store, engine, parts = front_door(planner=planner)
    thread = service.start_thread(ALICE)
    decision = service.record_decision(ALICE, thread.thread_id, "constraint", "Only use fiscal year 2024 data for P148850.")
    reply = door.handle_message(ALICE, thread.thread_id,
                                "Investigate how many bid invitations were published for project P148850 in fiscal year 2024 and then explain the metric")
    pending = reply.investigation
    decisions = store.decisions(ALICE, thread.thread_id)
    approval_ids = (pending.pending_approval.approval_id,)
    digest = build_digest(pending.values, pending.thread_id, decisions, approval_ids)
    assert digest.constraint_ids == (decision.decision_id,) and digest.constraints == (decision.statement,)
    assert digest.pending_approval_ids == approval_ids and "P148850" in digest.project_ids
    assert set(digest.unresolved_task_ids) == set(pending.task_states)                # nothing ran yet: all unresolved
    assert digest_losses(digest, pending.values, decisions, approval_ids) == []
    lossy = digest.model_copy(update={"pending_approval_ids": (), "project_ids": ()})
    assert sorted(digest_losses(lossy, pending.values, decisions, approval_ids)) == [
        f"pending_approval_ids:{approval_ids[0]}", "project_ids:P148850"]
    rendered = render_digest(digest, budget_tokens=10)
    assert rendered.over_budget and approval_ids[0] in rendered.text and decision.statement in rendered.text   # not truncated
    done = door.approve(ALICE, thread.thread_id, pending.thread_id, approval_ids[0], True)
    final = build_digest(done.values, done.thread_id, store.decisions(ALICE, thread.thread_id))
    assert final.unresolved_task_ids == () and len(final.evidence_ids) == 2 and final.status == "answered"
    assert owner_ref(ALICE).user_hash != "alice"


# ------------------------------------------------------------------------------------- off-topic and conduct
def nemo_front_door(planner):
    from structured_agent.guardrails.nemo_rails import NemoGuardrails
    engine, _ = build_engine(planner=planner)
    store = InMemoryConversationStore()
    runners = {route: ConversationRunner(engine, route, AUTH) for route in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)}
    service = ConversationService(store, runners, NemoGuardrails())
    return ConversationFrontDoor(service, store, engine, AUTH), service, store


@pytest.mark.parametrize("text, status", [("Sing me a song", "out_of_scope"), ("Tell me something fun", "out_of_scope"),
                                          ("Is the contractor on the Expressway corrupt?", "declined")])
def test_off_topic_and_misconduct_requests_get_a_plain_answer_and_run_nothing(text, status):
    planner = FakePlanner()
    door, service, store = nemo_front_door(planner)
    thread = service.start_thread(ALICE)
    reply = door.handle_message(ALICE, thread.thread_id, text)
    answer = store.turns(ALICE, thread.thread_id)[-1]
    assert reply.status == status and answer.role == "assistant" and planner.calls == []
    assert "procurement" in answer.text and "guardrail" not in answer.text


# ------------------------------------------------------------------------------------------------- personal data
def masked_front_door():
    from conversation.pii import redact_personal_data
    engine, _ = build_engine()
    store = InMemoryConversationStore()
    runners = {route: ConversationRunner(engine, route, AUTH) for route in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)}
    service = ConversationService(store, runners, PolicyGuard(), text_filter=redact_personal_data)
    return ConversationFrontDoor(service, store, engine, AUTH), service, store


def test_personal_data_is_masked_before_it_is_stored_or_sent_to_an_agent():
    door, service, store = masked_front_door()
    thread = service.start_thread(ALICE)
    reply = door.handle_message(ALICE, thread.thread_id, "I am abebe@example.org, phone +251 911 234 567. What lessons were "
                                                         "learned about road works procurement in Ethiopia?")
    stored = store.turns(ALICE, thread.thread_id)[0].text
    assert "abebe@example.org" not in stored and "<EMAIL_ADDRESS>" in stored and "<PHONE_NUMBER>" in stored
    assert "abebe@example.org" not in reply.investigation.values["request"]["question"]


def test_a_brief_description_is_masked_before_it_is_stored_or_run():
    import datetime as dt
    from wbg_orchestrator.contracts import PlannedPackage, PlanningSpec, ProjectProfile
    door, service, store = masked_front_door()
    thread = service.start_thread(ALICE)
    spec = PlanningSpec(profile=ProjectProfile(country_code="ET", sector="Transportation", approval_date=dt.date(2027, 6, 30),
                                               description="Rural roads in Oromia; coordinator reachable at lead@example.org"),
                        packages=(PlannedPackage(name="Works", procurement_category="Works", bid_window_days=45,
                                                 tender_date=dt.date(2027, 10, 1)),))
    outcome = door.plan_project(ALICE, thread.thread_id, spec)
    assert "lead@example.org" not in outcome.values["request"]["planning"]["profile"]["description"]
    assert all("lead@example.org" not in t.text for t in store.turns(ALICE, thread.thread_id))
