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
    assert reply.conversation.status == "clarification_required"                      # the existing router: mixed intent
    assert reply.investigation is not None and len(planner.calls) == 1
    request = reply.investigation.values["request"]
    assert request["mode"] == "investigation" and set(request["authorization"]["routes"]) == {"structured_data", "document_rag"}
    last = store.turns(ALICE, thread.thread_id)[-1]
    assert last.metadata.route == "investigation" and last.role == "assistant"


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
