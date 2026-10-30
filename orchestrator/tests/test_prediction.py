"""Package assessments through the supervisor, the fixed plan, the ML worker and the response. The project comes from the
user (it may have no ID yet); only its country's region and income history are read from Gold."""
from __future__ import annotations

import datetime as dt

from conversation.routing.routes import Route
from tests.helpers import FakePipeline, answer_response, build_engine, request
from wbg_orchestrator.app_service import summarize
from wbg_orchestrator.contracts import (EvidenceKind, InvestigationRequest, InvestigationStatus, NumericUse, PackageAssessment,
                                        ValidationStatus, WorkerName)
from wbg_orchestrator.workers.prediction import COUNTRY_OBJECTS, USER_INPUTS
from wbg_orchestrator.planner import package_plan
from wbg_orchestrator.supervisor import Supervisor

PACKAGE = PackageAssessment(project_id="P148850", procurement_method="Request for Bids", procurement_category="Works",
                            review_type="Prior", bid_window_days=60, tender_date=dt.date(2026, 12, 1),
                            country_code="ET", sector="Transportation", board_approval_date=dt.date(2015, 6, 30),
                            prior_tender_notices=40)
PACKAGE_ROUTES = (Route.ML_PREDICTION, Route.DOCUMENT_RAG)


def package_request(package: PackageAssessment = PACKAGE, routes=PACKAGE_ROUTES) -> InvestigationRequest:
    return request("Assess a Works package for project P148850.", routes=routes, mode="investigation",
                   release="pilot_v2").model_copy(update={"package": package})


def test_a_package_request_gets_the_fixed_assessment_plan():
    assert Supervisor().decide(package_request()).mode == "package"
    plan = package_plan(package_request())
    assert [(t.worker, t.operation, t.optional) for t in plan.tasks] == [
        (WorkerName.PREDICTION, "predict_package", False), (WorkerName.DOCUMENT, "answer_document_question", True)]
    assert plan.completion.required_task_ids == (plan.tasks[0].task_id,)


def test_a_package_request_without_the_prediction_route_is_refused():
    assert Supervisor().decide(package_request(routes=(Route.DOCUMENT_RAG,))).mode == "refuse"


def test_message_text_alone_cannot_start_a_package_assessment():
    decision = Supervisor().decide(request("Predict the tender duration for P148850", routes=PACKAGE_ROUTES))
    assert decision.mode != "package"


def test_the_assessment_returns_three_model_estimates_with_the_synthetic_disclaimer():
    engine, _ = build_engine()
    outcome = engine.start(package_request())
    predictions = [e for e in outcome.evidence if e.kind == EvidenceKind.MODEL_PREDICTION]
    assert len(predictions) == 3
    assert all(e.numeric_use == NumericUse.MODEL_ESTIMATE and e.validation_status == ValidationStatus.VERIFIED for e in predictions)
    assert [("synthetic_training_data" in e.quality_warnings) for e in predictions] == [False, True, True]
    assert outcome.status in (InvestigationStatus.ANSWERED, InvestigationStatus.PARTIAL)
    assert any("synthetic data" in limitation for limitation in outcome.response.limitations)
    assert "Estimated" in outcome.response.message


def test_lessons_from_the_documents_are_added_when_found():
    engine, _ = build_engine()
    outcome = engine.start(package_request())
    assert any(e.kind == EvidenceKind.DOCUMENT_PASSAGE for e in outcome.evidence)


def test_a_project_without_an_id_is_predicted_from_the_users_inputs_and_its_countrys_gold_context():
    engine, _ = build_engine()
    new = PACKAGE.model_copy(update={"project_id": None, "board_approval_date": dt.date(2026, 3, 1), "prior_tender_notices": 0})
    outcome = engine.start(package_request(new))
    predictions = [e for e in outcome.evidence if e.kind == EvidenceKind.MODEL_PREDICTION]
    assert len(predictions) == 3 and all(e.source_objects == USER_INPUTS + COUNTRY_OBJECTS and e.project_ids == () for e in predictions)
    assert "a new Transportation project in ET" in predictions[0].citation


def test_a_regional_project_gives_its_region_and_reads_nothing_from_gold():
    engine, _ = build_engine()
    regional = PACKAGE.model_copy(update={"project_id": None, "country_code": "REGIONAL", "region_name": "Western and Central Africa"})
    predictions = [e for e in engine.start(package_request(regional)).evidence if e.kind == EvidenceKind.MODEL_PREDICTION]
    assert len(predictions) == 3 and all(e.source_objects == USER_INPUTS for e in predictions)
    no_region = engine.start(package_request(regional.model_copy(update={"region_name": None})))
    assert "invalid_package" in summarize(no_region)["error_codes"]


def test_an_assessment_without_the_project_facts_gives_no_prediction_and_a_safe_error():
    engine, _ = build_engine()
    outcome = engine.start(package_request(PACKAGE.model_copy(update={"sector": None, "board_approval_date": None})))
    assert outcome.status != InvestigationStatus.ANSWERED          # the required prediction is missing
    assert not [e for e in outcome.evidence if e.kind == EvidenceKind.MODEL_PREDICTION]
    assert "invalid_package" in summarize(outcome)["error_codes"]               # what the API shows its owner


class PredictionsOnlyResponder:
    """Cites only the model predictions (as Claude did live when a document record was irrelevant)."""

    name, is_llm = "predictions_only", False

    def compose(self, request, evidence, report):
        from wbg_orchestrator.contracts import Usage
        from wbg_orchestrator.response import DraftClaim, DraftResponse
        return DraftResponse(claims=[DraftClaim(text=e.summary[:600], evidence_ids=[e.evidence_id])
                                     for e in evidence if e.kind == EvidenceKind.MODEL_PREDICTION]), Usage()


def test_a_conflicting_document_record_the_answer_does_not_use_does_not_mark_it_conflicting():
    engine, _ = build_engine(pipeline=FakePipeline(answer_response(status="conflicting_evidence")),
                             responder=PredictionsOnlyResponder())
    outcome = engine.start(package_request())
    assert any("conflicting_evidence" in e.quality_warnings for e in outcome.evidence)     # present in the ledger
    assert outcome.status != InvestigationStatus.CONFLICTING_EVIDENCE                      # but not relied on
