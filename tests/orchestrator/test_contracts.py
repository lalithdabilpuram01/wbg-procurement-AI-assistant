"""Acceptance 15: immutable contracts, versioned schemas, append-only state and monotonic task states."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from wbg_orchestrator.contracts import (AgentResult, EvidenceKind, EvidenceRecord, EvidenceRequirement, ExecutionError, ErrorCategory,
                                        InvestigationRequest, NumericUse, SourceSystem, TaskState, ValidationStatus, WorkerName)
from wbg_orchestrator.state import IllegalTransitionError, ImmutableRecordError, append_records, merge_task_states

from tests.orchestrator.helpers import ALICE, request


def evidence(**overrides) -> EvidenceRecord:
    base = dict(evidence_id="ev_1", investigation_id="inv_1", task_id="t1", source_system=SourceSystem.DATABRICKS_PUBLIC,
                kind=EvidenceKind.STRUCTURED_RESULT, citation="c", validation_status=ValidationStatus.VERIFIED,
                numeric_use=NumericUse.APPROVED)
    return EvidenceRecord(**{**base, **overrides})


def test_records_are_frozen_and_closed():
    record = evidence()
    with pytest.raises(ValidationError):
        record.citation = "changed"
    with pytest.raises(ValidationError):
        evidence(unexpected_field="x")
    with pytest.raises(ValidationError):
        evidence(schema_version="2")


def test_records_round_trip_as_plain_json():
    record = evidence(facts=(), quality_warnings=("w",), pages=(3, 4))
    state = record.to_state()
    assert isinstance(state["pages"], list) and EvidenceRecord.from_state(state) == record
    assert record.record_sha256() == EvidenceRecord.from_state(state).record_sha256()


def test_requests_are_bounded_and_carry_no_free_identity():
    with pytest.raises(ValidationError):
        request("x" * 2001)
    data = request("How many bids?").to_state()
    data["owner"]["user_id"] = "alice"                    # identities are hashed references, never raw ids
    with pytest.raises(ValidationError):
        InvestigationRequest.from_state(data)


def test_agent_results_record_only_terminal_states():
    with pytest.raises(ValidationError):
        AgentResult(task_id="t1", attempt=1, worker=WorkerName.STRUCTURED, idempotency_key="k", status=TaskState.RUNNING)


def test_append_only_reducer_rejects_rewrites_but_accepts_identical_resends():
    reducer = append_records("error_id")
    first = ExecutionError(error_id="e1", investigation_id="i", node="n", category=ErrorCategory.INTERNAL, code="c",
                           safe_message="m").to_state()
    merged = reducer([first], [first])
    assert merged == [first]
    with pytest.raises(ImmutableRecordError):
        reducer([first], [{**first, "code": "changed"}])


@pytest.mark.parametrize("path", [("pending", "running", "succeeded"), ("pending", "cancelled"), ("pending", "running", "skipped")])
def test_task_states_move_forward(path):
    states = {}
    for state in path:
        states = merge_task_states(states, {"t1": state})
    assert states["t1"] == path[-1]


@pytest.mark.parametrize("start, target", [("succeeded", "running"), ("failed", "succeeded"), ("cancelled", "pending")])
def test_task_states_never_move_backwards(start, target):
    states = {"t1": "pending"}
    for step in {"succeeded": ["running", "succeeded"], "failed": ["running", "failed"], "cancelled": ["cancelled"]}[start]:
        states = merge_task_states(states, {"t1": step})
    with pytest.raises(IllegalTransitionError):
        merge_task_states(states, {"t1": target})


def test_a_task_must_start_pending():
    with pytest.raises(IllegalTransitionError):
        merge_task_states({}, {"t1": "succeeded"})


def test_owner_reference_is_a_hash():
    assert ALICE.user_hash != "alice" and len(ALICE.user_hash) == 32


def test_requirements_stored_before_c1_still_load():
    """Checkpoints written before evidence alternatives existed (e.g. the Milestone B Lakebase threads) stay readable."""
    old = {"schema_version": "1", "kind": "document_passage", "min_count": 1, "must_be_verified": True}
    requirement = EvidenceRequirement.from_state(old)
    assert requirement.accepted_kinds == (EvidenceKind.DOCUMENT_PASSAGE,)
