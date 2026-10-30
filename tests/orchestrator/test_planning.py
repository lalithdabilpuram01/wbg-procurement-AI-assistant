"""Planning Brief for a new project: similar projects, the fixed brief plan, scoped document questions, models per
package with suggested methods, sections in the answer, and planner investigations carrying the brief's context."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from conversation.routing.routes import Route
from conversation.service import ConversationService
from conversation.store import InMemoryConversationStore
from tests.orchestrator.helpers import (ALICE, AUTH, GOLD_PROJECTS, FakePipeline, PolicyGuard, answer_response, build_engine, plan, request,
                           task)
from wbg_orchestrator.contracts import (EvidenceKind, InvestigationStatus, PlannedPackage, PlanningSpec, ProjectProfile,
                                        WorkerName)
from wbg_orchestrator.conversation_bridge import ConversationFrontDoor, ConversationRunner
from wbg_orchestrator.planner import BRIEF_BUDGET, FakePlanner, brief_plan
from wbg_orchestrator.response import DraftClaim, DraftResponse
from wbg_orchestrator.supervisor import Supervisor
from wbg_orchestrator.workers.planning import rank_similar
from conversation.models import Owner

PROFILE = ProjectProfile(country_code="ET", sector="Transportation", approval_date=dt.date(2027, 6, 30), total_cost_usd=300e6,
                         description="Rehabilitate 120 km of rural roads and build three bridges in Oromia region.")
SPEC = PlanningSpec(profile=PROFILE, packages=(
    PlannedPackage(name="Road works lot 1", procurement_category="Works", procurement_method="Request for Bids",
                   bid_window_days=45, tender_date=dt.date(2027, 10, 1)),
    PlannedPackage(name="Bridge works", procurement_category="Works", bid_window_days=45, tender_date=dt.date(2027, 12, 1))))
BRIEF_ROUTES = (Route.PROCUREMENT_PLANNING, Route.ML_PREDICTION, Route.DOCUMENT_RAG)
OWNER = Owner(tenant_id="tenant-1", user_id="alice")


def brief_request(routes=BRIEF_ROUTES):
    return request("Plan a new Transportation project in ET.", routes=routes, mode="investigation", release="pilot_v2",
                   budget=BRIEF_BUDGET).model_copy(update={"planning": SPEC})


# --------------------------------------------------------------------------------------------- ranking
def test_similar_projects_rank_country_sector_text_and_size_and_keep_a_document_scope():
    candidates = [{"project_id": pid, "project_name": name, "wdi_country_code": country, "approval_fiscal_year": Decimal(2016),
                   "current_total_commitment_usd_snapshot": Decimal(str(size))}                 # as Databricks returns them
                  for pid, name, country, size in GOLD_PROJECTS]
    sectors = {"P148850": "Transportation", "P111111": "Transportation"}
    ranked = rank_similar(PROFILE, candidates, lambda pid: sectors.get(pid, "Urban Development"),
                          {"P148850": 0.7, "P163452": 0.5, "P143302": 0.4})
    assert ranked[0].project_id == "P148850" and set(ranked[0].reasons) >= {"same country", "same sector"}
    assert [p.project_id for p in ranked if p.document_scope][:1] == ["P148850"]
    assert not next(p for p in ranked if p.project_id == "P111111").has_documents


# ------------------------------------------------------------------------------------------ the brief plan
def test_a_planning_request_gets_the_fixed_brief_plan():
    assert Supervisor().decide(brief_request()).mode == "brief"
    tasks = {t.local_id: t for t in brief_plan(brief_request()).tasks}
    assert set(tasks) == {"similar", "market", "governance", "reviewed", "predict", "risks", "issues", "lessons", "packaging"}
    assert tasks["predict"].worker == WorkerName.PREDICTION and tasks["predict"].depends_on == (tasks["market"].task_id,)
    assert all(tasks[k].depends_on == (tasks["similar"].task_id,) for k in ("risks", "issues", "lessons", "packaging"))


def test_a_brief_without_the_planning_route_is_refused():
    assert Supervisor().decide(brief_request(routes=(Route.DOCUMENT_RAG,))).mode == "refuse"


def test_the_brief_runs_end_to_end_with_scoped_documents_and_suggested_methods():
    pipeline = FakePipeline()
    engine, _ = build_engine(pipeline=pipeline)
    outcome = engine.start(brief_request())
    kinds = [e.kind for e in outcome.evidence]
    assert kinds.count(EvidenceKind.SIMILAR_PROJECT) == 4
    assert kinds.count(EvidenceKind.MODEL_PREDICTION) == 6                          # 3 models x 2 packages
    assert EvidenceKind.STRUCTURED_RESULT in kinds and EvidenceKind.DOCUMENT_PASSAGE in kinds
    assert pipeline.questions and all("(projects P148850" in q for q in pipeline.questions)     # only similar projects
    suggested = [e for e in outcome.evidence if "Bridge works" in e.citation]
    assert suggested and all("method suggested: Request for Bids" in e.citation for e in suggested)
    assert outcome.status in (InvestigationStatus.ANSWERED, InvestigationStatus.PARTIAL)


def test_brief_claims_carry_their_section():
    class Sectioned:
        name, is_llm = "sectioned", False

        def compose(self, request, evidence, report):
            from wbg_orchestrator.contracts import Usage
            similar = next(e for e in evidence if e.kind == EvidenceKind.SIMILAR_PROJECT)
            return DraftResponse(claims=[DraftClaim(text=similar.summary, evidence_ids=[similar.evidence_id],
                                                    section="similar_projects")]), Usage()
    engine, _ = build_engine(responder=Sectioned())
    assert engine.start(brief_request()).response.claims[0].section == "similar_projects"


# ------------------------------------------------------------------------------------- investigations
def front_door(planner):
    engine, parts = build_engine(planner=planner)
    store = InMemoryConversationStore()
    runners = {r: ConversationRunner(engine, r, AUTH) for r in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)}
    service = ConversationService(store, runners, PolicyGuard())
    return ConversationFrontDoor(service, store, engine, AUTH), service, store


def test_questions_after_a_brief_are_planned_with_its_similar_projects():
    planner = FakePlanner(plan(task("docs", worker="document_intelligence", operation="answer_document_question",
                                    question="What delayed works contracts in P148850?")))
    door, service, store = front_door(planner)
    thread = service.start_thread(OWNER)
    door.plan_project(OWNER, thread.thread_id, SPEC)
    reply = door.handle_message(OWNER, thread.thread_id, "What procurement delays were reported in these projects?")
    context = reply.investigation.values["request"]["planning_context"]
    assert planner.calls and "P148850" in context and "Rehabilitate 120 km" in context
    routes = [t.metadata.route for t in store.turns(OWNER, thread.thread_id) if t.role == "user"]
    assert routes == ["planning_brief", "investigation"]


def test_a_free_text_planning_request_lists_the_fields_and_runs_nothing():
    door, service, store = front_door(FakePlanner())
    thread = service.start_thread(OWNER)
    reply = door.handle_message(OWNER, thread.thread_id, "Draft a procurement plan for a new rural roads project")
    assert reply.status == "needs_project_details" and reply.investigation is None
    assert "Plan a new project" in store.turns(OWNER, thread.thread_id)[-1].text
