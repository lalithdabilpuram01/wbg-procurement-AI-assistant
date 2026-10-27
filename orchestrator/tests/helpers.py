"""Deterministic fixtures for offline graph tests. Results are built with the REAL result types of the existing agents
(WorkflowResult, AnswerResponse, Citation), so the adapters are exercised against the actual interfaces."""
from __future__ import annotations

import datetime as dt
import time
from typing import Any, Callable

from conversation.routing.routes import Route
from structured_agent.guardrails.decisions import GuardDecision, GuardStage
from structured_agent.guardrails.policies import evaluate_user_input
from structured_agent.nl2sql.result import ValidationSummary, WorkflowResult, WorkflowStatus
from structured_agent.nl2sql.resilience import CircuitBreaker

from wbg_orchestrator.contracts import InvestigationBudget, InvestigationRequest, OwnerRef, WorkerName, new_id
from wbg_orchestrator.controller import DEFAULT_BREAKER, ExecutionController, RetryBackoff
from wbg_orchestrator.engine import InvestigationEngine
from wbg_orchestrator.factory import authorization_factory
from wbg_orchestrator.graph import EngineDeps
from wbg_orchestrator.hitl import ApprovalPolicy
from wbg_orchestrator.observability import AuditTrail, MemoryAuditSink, OrchestratorTracer
from wbg_orchestrator.planner import FakePlanner
from wbg_orchestrator.rag_modules import answering
from wbg_orchestrator.response import DeterministicResponseGenerator
from wbg_orchestrator.supervisor import Supervisor
from wbg_orchestrator.workers.base import ResultCache, TicketAuthority
from wbg_orchestrator.workers.document import DocumentIntelligenceWorker
from wbg_orchestrator.workers.planning import PlanningWorker
from wbg_orchestrator.workers.prediction import PredictionWorker
from wbg_orchestrator.workers.structured import StructuredDataWorker

WORKSPACE, PRINCIPAL = "wbg-procurement-poc", "analyst-demo"
ALICE = OwnerRef.from_identity("tenant-1", "alice")
BOB = OwnerRef.from_identity("tenant-1", "bob")
AUTH = authorization_factory(PRINCIPAL, WORKSPACE)
GOLD_VIEW = "workspace.wbg_gold.vw_kpi_notice_activity"
BID_SQL = (f"SELECT publication_fiscal_year, sum(procurement_notices) AS tender_invitation_notices FROM {GOLD_VIEW} "
           "WHERE notice_class = 'tender_invitation' AND publication_fiscal_year = :fiscal_year GROUP BY publication_fiscal_year LIMIT 10")
SECRET = "pk-test-SECRET-do-not-leak-4242"


class PolicyGuard:
    """The existing deterministic input policy (the NeMo-backed guard is exercised in test_integration)."""

    def check_input(self, question: str) -> GuardDecision:
        result = evaluate_user_input(question)
        return GuardDecision(stage=GuardStage.INPUT, policy_id="app.input", allowed=result.allowed,
                             reason_category=result.reason, enforced_by="application_policy")


def request(question: str, *, owner: OwnerRef = ALICE, routes=(Route.STRUCTURED_DATA,), route_hint: str | None = None,
            mode: str = "auto", execute: bool = False, release: str = "pilot_v2", thread: str = "conv-1",
            budget: InvestigationBudget | None = None) -> InvestigationRequest:
    return InvestigationRequest(investigation_id=new_id("inv"), request_id=new_id("req"), conversation_thread_id=thread, owner=owner,
                                question=question, route_hint=route_hint, mode=mode, execute_requested=execute, rag_release=release,
                                authorization=AUTH(tuple(routes)), budget=budget or InvestigationBudget())


# ------------------------------------------------------------------------------------------------- structured
def workflow_result(question: str, *, execute: bool, value: Any = 1234, sql: str = BID_SQL, fiscal_year: int = 2024,
                    status: WorkflowStatus | None = None, **extra: Any) -> WorkflowResult:
    validation = ValidationSummary(approved=True, validation_id=f"val_{fiscal_year}", validated_sql=sql)
    if status is not None:
        return WorkflowResult(request_id="req_fake", question=question, status=status, **extra)
    if not execute:
        return WorkflowResult(request_id="req_fake", question=question, status=WorkflowStatus.VALIDATED_NOT_EXECUTED,
                              validated_sql=sql, validation=validation, context_tables=[GOLD_VIEW], llm_calls=1,
                              parameters={"fiscal_year": fiscal_year}, message="The query was validated but not executed.")
    return WorkflowResult(request_id="req_fake", question=question, status=WorkflowStatus.ANSWERED, validated_sql=sql, executed_sql=sql,
                          validation=validation, parameters={"fiscal_year": fiscal_year}, source_objects=[GOLD_VIEW],
                          rows=[{"publication_fiscal_year": fiscal_year, "tender_invitation_notices": value}], row_count=1,
                          columns=[{"name": "publication_fiscal_year"}, {"name": "tender_invitation_notices"}], llm_calls=1,
                          provenance={"credential_mode": "read_only_principal", "registry_build_id": "build_test",
                                      "executed_at": "2026-10-26T00:00:00+00:00"},
                          message=f"Query executed: 1 row from {GOLD_VIEW}.")


class FakeWorkflow:
    """Stands in for NlToSqlWorkflow.run. `respond(question, execute)` builds the WorkflowResult."""

    def __init__(self, respond: Callable[[str, bool], WorkflowResult] | None = None, delay_seconds: float = 0.0):
        self.respond = respond or (lambda q, execute: workflow_result(q, execute=execute))
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, Any]] = []

    def run(self, question, execute=False, request_id=None, conversation_context=None):
        self.calls.append({"question": question, "execute": execute, "context": conversation_context})
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.respond(question, execute)


class FakeTools:
    """Registry tools; `failures` raises the listed exceptions first (transient errors)."""

    def __init__(self, failures: list[Exception] | None = None, metrics: list[dict] | None = None):
        self.failures = list(failures or [])
        self.calls: list[tuple[str, dict]] = []
        self.metrics = metrics if metrics is not None else [{
            "metric_id": "tender_invitation_notices", "name": "Tender invitation notices", "object_fqn": GOLD_VIEW,
            "description": "Count of published tender invitation notices", "unit": "notices", "status": "verified"}]

    def call(self, tool, arguments, request_id):
        self.calls.append((tool, arguments))
        if self.failures:
            raise self.failures.pop(0)
        if tool == "get_metric_definition":
            return {"status": "ok" if self.metrics else "not_found", "metrics": self.metrics, "errors": []}
        if tool == "search_table_registry":
            return {"status": "ok", "candidates": [{"object_fqn": GOLD_VIEW, "logical_name": "Notice activity", "grain": "notice",
                                                    "short_description": "Notices by year"}], "errors": []}
        return {"status": "error", "errors": [{"code": "UNKNOWN"}]}


# ------------------------------------------------------------------------------------------------- documents
def citation(**overrides: Any) -> dict[str, Any]:
    base = {"evidence_id": "E1", "unit_id": "34455066:C:0023", "unit_type": "chunk", "project_id": "P148850", "project_name": "Expressway",
            "document_id": "34455066", "title": "ICR", "doc_type": "ICR", "document_date": "2023-01-01",
            "document_version_id": "34455066:abc", "physical_page_start": 12, "physical_page_end": 12, "printed_page_label": "9",
            "section": "Lessons", "table_id": None, "excerpt": "Early contractor mobilisation reduced delays on 3 contracts.",
            "source_url": "https://documents.worldbank.org/x", "association": "document text", "page_check": "verified"}
    return {**base, **overrides}


def answer_response(status: str = "answered", citations: list[dict] | None = None, removed: int = 0):
    schemas = answering("schemas")
    cites = [schemas.Citation(**c) for c in (citations if citations is not None else [citation()])]
    removed_claims = [schemas.ClaimResult(text="x", evidence_ids=["E1"], supported=False, checks={}) for _ in range(removed)]
    return schemas.AnswerResponse(question="q", status=status, answer="a", citations=cites, supporting_evidence_ids=["E1"],
                                  conflicts=[], limitations=[], removed_claims=removed_claims, generator="evidence_only",
                                  generator_is_llm=False)


class FakePipeline:
    def __init__(self, response=None, failures: list[Exception] | None = None):
        self.response = response or answer_response()
        self.failures = list(failures or [])
        self.questions: list[str] = []

    def answer(self, question):
        self.questions.append(question)
        if self.failures:
            raise self.failures.pop(0)
        return self.response


class FakeRetriever:
    """Stands in for the retriever's hybrid search: raw hits carry no page check (like the real search results)."""

    def __init__(self, hits: list[dict] | None = None):
        self.hits = hits if hits is not None else [{k: v for k, v in citation().items() if k != "page_check"}]
        self.queries: list[str] = []

    def search(self, query, method="hybrid", k=5, guarded=True):
        self.queries.append(query)
        return {"status": "ok" if self.hits else "no_results", "results": self.hits[:k]}


# ------------------------------------------------------------------------------------------------- engine
class Clock:
    def __init__(self):
        self.now = dt.datetime(2026, 10, 26, 12, 0, tzinfo=dt.timezone.utc)

    def __call__(self) -> dt.datetime:
        return self.now


GOLD_PROJECTS = [  # (project_id, name, country, commitment) in Eastern and Southern Africa
    ("P148850", "Ethiopia Expressway Development Support Project", "ET", 300e6),
    ("P163452", "Ethiopia Urban Institutional and Infrastructure Development", "ET", 650e6),
    ("P143302", "Ethiopia Competitiveness and Job Creation Project", "ET", 250e6),
    ("P111111", "Kenya Roads Project", "KE", 200e6),
]


def fake_gold_sql(sql: str, parameters: dict) -> list[dict]:
    """The prediction and planning workers' fixed Gold lookups (offline)."""
    from ml.serving.gold import INCOME_SQL, PROJECT_SQL, REGION_SQL
    from wbg_orchestrator.workers.planning import CANDIDATES_SQL, GOVERNANCE_SQL
    if sql == PROJECT_SQL:
        return [{"project_id": parameters["project_id"], "region_name": "Eastern and Southern Africa", "wdi_country_code": "ET",
                 "board_approval_date": "2016-06-28", "approval_fiscal_year": 2016}] if parameters["project_id"] == "P148850" else []
    if sql == INCOME_SQL:
        return [{"year": 2025, "income_group_fy_starting_in_year": "L"}]
    if sql == REGION_SQL:
        return [{"region_name": "Eastern and Southern Africa", "projects": 120}]
    if sql == CANDIDATES_SQL:
        return [{"project_id": pid, "project_name": name, "wdi_country_code": country, "region_name": parameters["region_name"],
                 "approval_fiscal_year": 2016, "project_status_snapshot": "Active", "current_total_commitment_usd_snapshot": size,
                 "contract_award_count": 40, "ieg_outcome_rating": None} for pid, name, country, size in GOLD_PROJECTS]
    if sql == GOVERNANCE_SQL:
        return [{"year": 2023, "cpia_irai_overall": 3.2, "wgi_government_effectiveness_est": -0.6, "income_group_fy_starting_in_year": "L"}]
    if "GROUP BY procurement_method" in sql:
        return [{"procurement_method": "Request for Bids", "contract_awards": 120, "median_contract_amount_usd": 2400000.0,
                 "median_days_from_approval_to_signing": 640.0},
                {"procurement_method": "Request for Quotations", "contract_awards": 45, "median_contract_amount_usd": 150000.0,
                 "median_days_from_approval_to_signing": 410.0}]
    if "GROUP BY review_type" in sql:
        return [{"review_type": "Prior", "contract_awards": 90}, {"review_type": "Post", "contract_awards": 75}]
    if "GROUP BY supplier_country_relation" in sql:
        return [{"supplier_country_relation": "domestic", "contract_awards": 110}, {"supplier_country_relation": "foreign", "contract_awards": 55}]
    return [{"prior_tender_notices": 34}]


def fake_semantic(text: str) -> dict[str, float]:
    """Description similarity for the projects that have documents (offline)."""
    return {"P148850": 0.71, "P163452": 0.52, "P143302": 0.40}


def build_engine(*, workflow=None, tools=None, pipeline=None, retriever=None, planner=None, responder=None, guard=None, policy=None,
                 checkpointer=None, tracer=None, audit_sink=None, now=None, releases: list[str] | None = None, prices=None,
                 breaker_settings=DEFAULT_BREAKER, backoff=RetryBackoff(base_seconds=0.0, max_seconds=0.0), run_sql=fake_gold_sql):
    authority, cache = TicketAuthority(), ResultCache()
    workflow = workflow or FakeWorkflow()
    tools = tools or FakeTools()
    pipeline = pipeline or FakePipeline()
    retriever = retriever or FakeRetriever()
    structured = StructuredDataWorker(lambda auth: workflow, lambda auth: tools, authority, cache)
    seen_releases = releases if releases is not None else []
    document = DocumentIntelligenceWorker(lambda release: (seen_releases.append(release), pipeline)[1], lambda release: retriever,
                                          authority)
    sleeps: list[float] = []
    tracer = tracer or OrchestratorTracer.disabled()
    prediction = PredictionWorker(run_sql, authority)
    planning = PlanningWorker(run_sql, authority, fake_semantic, sector_of=lambda pid: "Transportation" if pid in
                              ("P148850", "P111111") else "Urban Development")
    controller = ExecutionController({WorkerName.STRUCTURED: structured, WorkerName.DOCUMENT: document,
                                      WorkerName.PREDICTION: prediction, WorkerName.PLANNING: planning}, authority, tracer=tracer,
                                     backoff=backoff, sleep=sleeps.append, prices=prices,
                                     breaker_settings=breaker_settings)
    audit_sink = audit_sink or MemoryAuditSink()
    deps = EngineDeps(controller=controller, supervisor=Supervisor(), planner=planner or FakePlanner(),
                      responder=responder or DeterministicResponseGenerator(), guard=guard or PolicyGuard(),
                      llm_breaker=CircuitBreaker("llm.portkey", DEFAULT_BREAKER), approval_policy=policy or ApprovalPolicy(),
                      tracer=tracer, audit=AuditTrail(audit_sink), now=now or Clock(), prices=prices)
    engine = InvestigationEngine(deps, checkpointer=checkpointer)
    return engine, {"workflow": workflow, "tools": tools, "pipeline": pipeline, "retriever": retriever, "controller": controller,
                    "sleeps": sleeps, "audit": audit_sink, "authority": authority, "releases": seen_releases, "deps": deps}


def task(id: str, worker: str = "structured_data", operation: str = "answer_data_question", question: str | None = None,
         deps: tuple[str, ...] = (), execute: bool = False, optional: bool = False) -> dict:
    item = {"id": id, "worker": worker, "operation": operation, "question": question or f"Question for {id} in fiscal year 2024",
            "depends_on": list(deps), "requires_execution": execute, "optional": optional}
    return item


def plan(*tasks: dict, required: list[str] | None = None, partial_allowed: bool = True) -> dict:
    out = {"tasks": list(tasks), "partial_allowed": partial_allowed, "rationale": "scripted test plan"}
    if required is not None:
        out["required"] = required
    return out
