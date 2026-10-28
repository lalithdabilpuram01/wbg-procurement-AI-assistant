"""Tender Design & Packaging: terms read from a draft, checks against history, and the fixed tender plan answered by
restating its checks (never by a model)."""
from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path

import pytest

from conversation.models import Owner
from conversation.routing.routes import Route
from conversation.service import ConversationService
from conversation.store import InMemoryConversationStore
from tender import check
from tender.extract import extract
from tender.text import UnreadableDraft, text_from_file

from wbg_orchestrator.contracts import EvidenceKind, InvestigationStatus, TenderSpec
from wbg_orchestrator.conversation_bridge import ConversationFrontDoor, ConversationRunner

from tests.helpers import AUTH, PolicyGuard, build_engine
from tests.test_api import Stack

ALICE = Owner(tenant_id="tenant-1", user_id="alice")
DRAFT = """Section III. Qualification Criteria.
The Bidder shall have a minimum average annual construction turnover of US$ 40 million over the last five years.
The Bidder shall have completed at least two (2) similar contracts, each with a value of at least US$ 30 million.
The Bidder shall have 10 years of general experience in road construction.
Bids shall remain valid for a period of 120 days after the bid submission deadline.
Bids must be submitted within 21 days of the publication of this invitation.
The Bid Security shall be 2% of the bid price. The works are procured in one lot.
The completion period is 18 months from the commencement date."""


class NoModel:
    name, is_llm = "no_model", True

    def compose(self, request, evidence, report):
        raise AssertionError("a tender check must not call a model")


@pytest.fixture
def history(tmp_path: Path) -> Path:
    """60 Works / Request for Bids packages of US$ 0.5M-30M: prior review from 10M, every fourth one cancelled."""
    rows = [{"activity_id": f"a{i}", "project_id": "P000001", "activity_reference_printed": f"W-{i}", "description_clean": "road",
             "category_family": "WORK", "method_clean": "Request for Bids", "review_type": "Prior" if i >= 20 else "Post",
             "process_status": "cancelled" if i % 4 == 0 else "signed", "document_id": "plan-1", "pdf_page": 1,
             "plan_version_id": "P000001:S:v1", "plan_stream_id": "P000001:S", "estimated_amount_usd": i * 5e5,
             "signed_contract_planned": "2020-01-01", "stage_dates": {}} for i in range(1, 61)]
    files = {"plan_versions": [{"plan_version_id": "P000001:S:v1", "plan_stream_id": "P000001:S", "pdf": {"revision_date": "2021-01-01"}}],
             "plan_activities": rows}
    for name, records in files.items():
        (tmp_path / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return tmp_path


def spec(**changes) -> TenderSpec:
    values = {"project_id": "P148850", "procurement_method": "Request for Bids", "procurement_category": "Works",
              "review_type": "Prior", "bid_window_days": 21, "tender_date": dt.date(2026, 12, 1), "estimated_amount_usd": 12e6,
              "lots": 1, "draft_text": DRAFT}
    return TenderSpec(**(values | changes))


# ------------------------------------------------------------------------------------------------ extraction
def test_terms_are_read_with_the_sentence_they_come_from():
    terms = {t.name: t for t in extract(DRAFT)}
    assert terms["turnover_usd"].value == 40e6 and terms["similar_contracts"].value == 2
    assert terms["similar_contract_value_usd"].value == 30e6 and terms["experience_years"].value == 10
    assert terms["bid_validity_days"].value == 120 and terms["bid_window_days"].value == 21     # validity is not the window
    assert terms["bid_security_percent"].value == 2 and terms["lots"].value == 1 and terms["completion_months"].value == 18
    assert "turnover of US$ 40 million" in terms["turnover_usd"].sentence


def test_a_draft_without_terms_yields_none():
    assert extract("General description of the road works and the project area.") == []


# ---------------------------------------------------------------------------------------------------- checks
def test_each_check_compares_the_package_with_its_benchmark(history):
    window = check.WindowBenchmark("Invitation for Bids", "category and method", 412, 35.0, 45.0, 60.0, 2016)
    package = check.Package("P148850", "Works", "Request for Bids", "Post", 12e6, 21, 1)
    checks = {c.code: c for c in check.run(package, extract(DRAFT), window, history)}
    assert checks["bid_window"].status == "fail" and "median 45" in checks["bid_window"].message    # under half the median
    assert checks["method_fit"].status == "ok" and checks["review_type"].status == "warn"      # most similar-size were prior
    assert checks["similar_contracts"].status == "fail" and checks["turnover"].status == "warn"
    assert all(c.sources for c in checks.values())


def test_without_an_estimate_only_the_bid_window_is_checked(history):
    package = check.Package("P148850", "Works", "Request for Bids", "Prior", None, 60, 1)
    assert [c.code for c in check.run(package, [], None, history)] == ["bid_window", "estimate_missing"]


# ------------------------------------------------------------------------------------------------- end to end
def test_a_tender_check_is_answered_from_its_checks_without_a_model(history):
    engine, _ = build_engine(responder=NoModel(), tender_history=history)
    store = InMemoryConversationStore()
    service = ConversationService(store, {r: ConversationRunner(engine, r, AUTH) for r in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)},
                                  PolicyGuard())
    door = ConversationFrontDoor(service, store, engine, AUTH)
    thread = service.start_thread(ALICE)
    outcome = door.check_tender(ALICE, thread.thread_id, spec())
    assert outcome.status in (InvestigationStatus.ANSWERED, InvestigationStatus.PARTIAL)
    tasks = [t["operation"] for t in outcome.values["plan"]["tasks"]]
    assert tasks == ["check_tender_design", "predict_package", "answer_document_question", "find_lessons"]
    checks = [e for e in outcome.evidence if e.kind == EvidenceKind.TENDER_CHECK]
    assert any(e.provenance_value("check") == "similar_contracts" and e.provenance_value("status") == "fail" for e in checks)
    assert "Read from the draft" in outcome.response.message and "Similar experience" in outcome.response.message
    assert [t.metadata.route for t in store.turns(ALICE, thread.thread_id)] == ["tender_check", "investigation"]


def test_asking_in_chat_points_to_the_tender_form_and_runs_nothing():
    engine, _ = build_engine(responder=NoModel())
    store = InMemoryConversationStore()
    service = ConversationService(store, {r: ConversationRunner(engine, r, AUTH) for r in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)},
                                  PolicyGuard())
    door = ConversationFrontDoor(service, store, engine, AUTH)
    thread = service.start_thread(ALICE)
    reply = door.handle_message(ALICE, thread.thread_id, "Can you check my draft ToR against past tenders?")
    assert reply.status == "needs_tender_details" and reply.investigation is None


# --------------------------------------------------------------------------------------------------------- API
def test_the_api_runs_a_tender_check_as_a_job_and_reads_uploaded_drafts(tmp_path, history):
    stack = Stack(tmp_path, tender_history=history)
    cid = stack.conversation()
    body = spec().model_dump(mode="json", exclude={"schema_version"}) | {"client_message_id": "tdr-0001"}
    job = stack.wait(stack.call("POST", f"/conversations/{cid}/tenders", json=body).json()["job_id"])
    assert job["state"] == "succeeded"
    view = stack.call("GET", f"/conversations/{cid}/investigations/{job['request_id']}").json()
    assert sum(e["kind"] == "tender_check" for e in view["evidence"]) >= 5
    upload = {"filename": "draft.txt", "content_base64": base64.b64encode(DRAFT.encode()).decode()}
    assert "US$ 40 million" in stack.call("POST", "/drafts/text", json=upload).json()["text"]
    bad = {"filename": "draft.exe", "content_base64": base64.b64encode(b"MZ...").decode()}
    assert stack.call("POST", "/drafts/text", json=bad).status_code == 422


def test_unreadable_files_are_refused_with_a_reason():
    with pytest.raises(UnreadableDraft):
        text_from_file("empty.txt", b"   ")
