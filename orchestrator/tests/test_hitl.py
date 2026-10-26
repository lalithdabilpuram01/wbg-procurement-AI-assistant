"""Acceptance 12: HITL interrupt, rejection, expiry, same-user resume after a restart, single use, audit metadata."""
from __future__ import annotations

import datetime as dt

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from wbg_orchestrator.contracts import ApprovalDecision, InvestigationStatus
from wbg_orchestrator.engine import ApprovalNotPending, InvestigationNotFound
from wbg_orchestrator.hitl import ApprovalPolicy, decision_problem
from wbg_orchestrator.planner import FakePlanner

from tests.helpers import ALICE, BOB, Clock, FakeWorkflow, build_engine, plan, request, task

QUESTION = "Investigate how many bid invitations were published in fiscal year 2024 and then explain the metric"


def planned():
    return FakePlanner(plan(task("metric", operation="describe_metric", question="Which metric counts bid invitations?"),
                            task("count", deps=("metric",), execute=True,
                                 question="How many bid invitations were published in fiscal year 2024?")))


def start_pending(**kwargs):
    engine, parts = build_engine(planner=planned(), **kwargs)
    out = engine.start(request(QUESTION))
    return engine, parts, out


def test_planned_sql_execution_interrupts_for_approval_before_anything_runs():
    engine, parts, out = start_pending()
    assert out.status == InvestigationStatus.AWAITING_APPROVAL and out.pending_approval is not None
    approval = out.pending_approval
    assert approval.kind == "execute_sql" and approval.requested_for == ALICE and "runs SQL, read-only" in approval.summary
    assert parts["workflow"].calls == [] and parts["tools"].calls == []
    assert set(out.task_states.values()) == {"pending"}


def test_another_user_cannot_resume_or_read_the_investigation():
    engine, parts, out = start_pending()
    with pytest.raises(InvestigationNotFound):
        engine.resume(BOB, out.thread_id, out.pending_approval.approval_id, approved=True)
    with pytest.raises(InvestigationNotFound):
        engine.outcome(BOB, out.thread_id)
    assert engine.outcome(ALICE, out.thread_id).status == InvestigationStatus.AWAITING_APPROVAL
    assert parts["workflow"].calls == []


def test_explicit_rejection_runs_nothing():
    engine, parts, out = start_pending()
    result = engine.resume(ALICE, out.thread_id, out.pending_approval.approval_id, approved=False, reason="not now")
    assert result.status == InvestigationStatus.REJECTED and parts["workflow"].calls == [] and parts["tools"].calls == []
    assert set(result.task_states.values()) == {"cancelled"}
    assert result.values["decisions"][0]["approved"] is False


def test_same_user_resume_after_a_restart_executes_exactly_once():
    saver = InMemorySaver()
    engine, parts, out = start_pending(checkpointer=saver)
    # "restart": a new engine process with new workers, controller and ticket secret; only the checkpoint survives
    workflow = FakeWorkflow()
    restarted, parts2 = build_engine(planner=FakePlanner(), workflow=workflow, checkpointer=saver)
    result = restarted.resume(ALICE, out.thread_id, out.pending_approval.approval_id, approved=True)
    assert result.status == InvestigationStatus.ANSWERED
    assert [c["execute"] for c in workflow.calls] == [True] and parts["workflow"].calls == []
    assert len(parts2["tools"].calls) == 1
    with pytest.raises(ApprovalNotPending):                                     # single use
        restarted.resume(ALICE, out.thread_id, out.pending_approval.approval_id, approved=True)
    assert len(workflow.calls) == 1


def test_expired_approval_runs_nothing():
    clock = Clock()
    engine, parts, out = start_pending(now=clock)
    clock.now += dt.timedelta(minutes=31)
    result = engine.resume(ALICE, out.thread_id, out.pending_approval.approval_id, approved=True)
    assert result.status == InvestigationStatus.REJECTED and "expired" in result.response.message
    assert parts["workflow"].calls == []
    assert any(e["code"] == "approval_expired" for e in result.values["errors"])


def test_decision_checks_owner_payload_expiry_and_reuse():
    engine, parts, out = start_pending()
    approval = out.pending_approval
    good = ApprovalDecision(approval_id=approval.approval_id, payload_sha256=approval.payload_sha256, approved=True, decided_by=ALICE)
    now = approval.created_at
    assert decision_problem(good, approval, now, set()) is None
    assert decision_problem(good.model_copy(update={"decided_by": BOB}), approval, now, set()) == "not_the_requesting_user"
    assert decision_problem(good.model_copy(update={"payload_sha256": "0" * 64}), approval, now, set()) == "payload_mismatch"
    assert decision_problem(good, approval, approval.expires_at + dt.timedelta(seconds=1), set()) == "approval_expired"
    assert decision_problem(good, approval, now, {approval.approval_id}) == "approval_already_used"


def test_approval_audit_metadata_contains_no_question_text():
    engine, parts, out = start_pending()
    engine.resume(ALICE, out.thread_id, out.pending_approval.approval_id, approved=True)
    events = parts["audit"].events
    names = [e["event"] for e in events]
    assert names[:2] == ["investigation.started", "approval.requested"] and "approval.decided" in names
    decided = next(e for e in events if e["event"] == "approval.decided")
    assert decided["approval_decision"] == "approved" and decided["approval_id"] == out.pending_approval.approval_id
    assert "bid invitations" not in str(events) and "fiscal" not in str(events)


def test_fast_path_execution_requested_by_the_user_needs_no_extra_approval_but_policy_can_require_it():
    engine, parts = build_engine()
    assert engine.start(request("How many bid invitations were published in fiscal year 2024?", execute=True)).status \
        == InvestigationStatus.ANSWERED
    strict, parts2 = build_engine(policy=ApprovalPolicy(execution_requires_approval="always"))
    pending = strict.start(request("How many bid invitations were published in fiscal year 2024?", execute=True))
    assert pending.status == InvestigationStatus.AWAITING_APPROVAL and parts2["workflow"].calls == []
