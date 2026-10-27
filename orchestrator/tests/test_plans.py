"""Acceptance 5-7: dependency graphs, unauthorized tool requests, malformed planner output (and bounded replanning).
C1: evidence requirements follow each operation's real output, and impossible requirements are rejected up front."""
from __future__ import annotations

import pytest

from conversation.routing.routes import Route

from wbg_orchestrator.contracts import EvidenceKind, EvidenceRequirement, InvestigationBudget, InvestigationStatus
from wbg_orchestrator.planner import FakePlanner, PlannerError, build_plan

from tests.helpers import build_engine, plan, request, task

BOTH = (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)


def codes(engine_or_errors, plan_dict, req=None) -> list[str]:
    engine, parts = engine_or_errors
    req = req or request("Investigate the bids", routes=BOTH)
    built = build_plan(plan_dict, req, produced_by="fake_planner")
    return sorted(e.code for e in parts["controller"].validate_plan(built, req))


@pytest.fixture
def setup():
    return build_engine()


# ------------------------------------------------------------------------------------------------ acceptance 5
def test_valid_dependency_graph_passes(setup):
    valid = plan(task("a", operation="describe_metric"), task("b", deps=("a",)),
                 task("c", worker="document_intelligence", operation="answer_document_question", deps=("a",)))
    assert codes(setup, valid) == []


@pytest.mark.parametrize("bad, expected", [
    (plan(task("a", deps=("b",)), task("b", deps=("a",))), "dependency_cycle"),
    (plan(task("a", deps=("a",))), "self_dependency"),
    (plan(task("a", deps=("missing",))), "unknown_dependency"),
    (plan(task("a"), task("b", deps=("a",)), task("c", deps=("b",)), task("d", deps=("c",))), "dependency_too_deep"),
    (plan(*[task(f"t{i}") for i in range(7)]), "too_many_tasks"),
    (plan(task("a"), required=["zzz"]), "unknown_required_task"),
])
def test_invalid_dependency_graphs_are_rejected(setup, bad, expected):
    assert expected in codes(setup, bad)


# ------------------------------------------------------------------------------------------------ acceptance 6
@pytest.mark.parametrize("bad, expected", [
    (plan(task("a", operation="execute_readonly_sql")), "unknown_operation"),         # an MCP tool the planner may not call
    (plan(task("a", operation="validate_sql")), "unknown_operation"),
    (plan(task("a", operation="drop_table")), "unknown_operation"),
    (plan(task("a", worker="document_intelligence", operation="answer_document_question", execute=True)), "execution_not_authorized"),
])
def test_unauthorized_tool_requests_are_rejected(setup, bad, expected):
    assert expected in codes(setup, bad)


def test_operations_outside_the_routed_capabilities_are_rejected(setup):
    structured_only = request("Investigate the bids", routes=(Route.STRUCTURED_DATA,))
    document_task = plan(task("a", worker="document_intelligence", operation="answer_document_question"))
    assert "operation_not_authorized" in codes(setup, document_task, structured_only)


def test_unauthorized_plan_never_reaches_a_worker():
    planner = FakePlanner(plan(task("a", operation="execute_readonly_sql")), plan(task("b", operation="drop_table")))
    engine, parts = build_engine(planner=planner)
    out = engine.start(request("Investigate the number of bids and then explain the trend", mode="investigation"))
    assert out.status == InvestigationStatus.FAILED and "unknown_operation" in out.response.error_codes
    assert parts["workflow"].calls == [] and parts["tools"].calls == [] and len(planner.calls) == 2   # one bounded replan


def test_a_worker_worker_name_unknown_to_the_schema_is_a_planner_error():
    with pytest.raises(PlannerError):
        build_plan(plan(task("a", worker="email_sender")), request("x"), produced_by="fake_planner")


# ------------------------------------------------------------------------------------------------ acceptance 7
@pytest.mark.parametrize("raw, code", [("not json at all", "malformed_output"), ({"tasks": []}, "schema_violation"),
                                       ({"tasks": [task("a")], "sql": "DROP TABLE x"}, "schema_violation")])
def test_malformed_planner_output_is_rejected(raw, code):
    with pytest.raises(PlannerError) as error:
        build_plan(raw, request("x"), produced_by="fake_planner")
    assert error.value.code == code


def test_malformed_output_gets_one_bounded_replan_then_succeeds():
    planner = FakePlanner("{broken", plan(task("a", operation="describe_metric")))
    engine, parts = build_engine(planner=planner)
    out = engine.start(request("Investigate the metric definition step by step", mode="investigation"))
    assert out.status == InvestigationStatus.ANSWERED and len(planner.calls) == 2
    assert planner.calls[1].failures == ("malformed_output",)                     # typed feedback only, no content
    assert out.values["replans"] == 1


def test_repeated_malformed_output_fails_safely_without_running_anything():
    planner = FakePlanner("{broken", "still broken", plan(task("a")))
    engine, parts = build_engine(planner=planner)
    out = engine.start(request("Investigate the metric definition step by step", mode="investigation"))
    assert out.status == InvestigationStatus.FAILED and out.response.error_codes == ("malformed_output",)
    assert len(planner.calls) == 2 and parts["workflow"].calls == [] and parts["tools"].calls == []


def test_budget_without_llm_calls_refuses_planning():
    engine, parts = build_engine(planner=FakePlanner(plan(task("a"))))
    out = engine.start(request("Investigate step by step", mode="investigation", budget=InvestigationBudget(max_llm_calls=0)))
    assert out.status == InvestigationStatus.FAILED and "llm_call_limit" in out.response.error_codes


# ------------------------------------------------------------------------------------- C1: evidence contracts
DOCUMENT_KINDS = {"document_passage", "document_table", "typed_plan_record"}


def test_the_planner_cannot_choose_evidence_kinds():
    raw = plan(task("a", execute=True))
    raw["tasks"][0]["evidence"] = [{"kind": "validated_query"}]          # what the live planner asked for
    with pytest.raises(PlannerError) as raised:
        build_plan(raw, request("x"), produced_by="fake_planner")
    assert raised.value.code == "schema_violation"


@pytest.mark.parametrize("worker, operation, execute, kinds, verified", [
    ("structured_data", "answer_data_question", True, {"structured_result"}, True),
    ("structured_data", "answer_data_question", False, {"validated_query"}, True),
    ("structured_data", "describe_metric", False, {"metric_definition"}, True),
    ("structured_data", "find_tables", False, {"table_candidate"}, True),
    ("document_intelligence", "answer_document_question", False, DOCUMENT_KINDS, True),
    ("document_intelligence", "retrieve_passages", False, DOCUMENT_KINDS, False),
])
def test_requirements_follow_the_operation_and_its_execution_mode(setup, worker, operation, execute, kinds, verified):
    req = request("Investigate the bids", routes=BOTH)
    built = build_plan(plan(task("a", worker=worker, operation=operation, execute=execute),
                            task("m", operation="describe_metric")), req, produced_by="fake_planner")
    [requirement] = built.tasks[0].evidence_requirements
    assert {k.value for k in requirement.accepted_kinds} == kinds and requirement.must_be_verified is verified
    assert setup[1]["controller"].validate_plan(built, req) == []


def with_requirement(built, **requirement):
    first = built.tasks[0].model_copy(update={"evidence_requirements": (EvidenceRequirement(**requirement),)})
    return built.model_copy(update={"tasks": (first, *built.tasks[1:])})


@pytest.mark.parametrize("draft, requirement, expected", [
    # the live run: an executed query asked for a validated-only query
    (plan(task("a", execute=True)), {"kind": EvidenceKind.VALIDATED_QUERY}, "evidence_not_producible"),
    # the live run: raw retrieval asked for page-verified passages
    (plan(task("a", worker="document_intelligence", operation="retrieve_passages"), task("m", operation="describe_metric")),
     {"kind": EvidenceKind.DOCUMENT_PASSAGE, "must_be_verified": True}, "evidence_not_verifiable"),
    (plan(task("a", operation="describe_metric")), {"kind": EvidenceKind.DOCUMENT_PASSAGE}, "evidence_not_producible"),
])
def test_requirements_no_worker_can_meet_are_rejected_before_anything_runs(setup, draft, requirement, expected):
    req = request("Investigate the bids", routes=BOTH)
    built = with_requirement(build_plan(draft, req, produced_by="fake_planner"), **requirement)
    assert expected in [e.code for e in setup[1]["controller"].validate_plan(built, req)]


def test_a_plan_that_can_produce_no_verified_evidence_is_rejected(setup):
    only_raw_passages = plan(task("a", worker="document_intelligence", operation="retrieve_passages"))
    assert "verified_evidence_not_producible" in codes(setup, only_raw_passages)
