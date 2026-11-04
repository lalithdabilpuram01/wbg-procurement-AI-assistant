"""Immutable application records of an investigation (schema version 1).

Every record is a frozen Pydantic model that forbids unknown fields and uses tuples, so it cannot be changed after it
is created; a correction is a new record. Records travel through the LangGraph state as plain JSON dicts
(`to_state` / `from_state`), so checkpoints never need custom deserialisation and never contain Python objects.
Graph state holds identifiers, references and bounded summaries only: no credentials, no raw result rows, no document
dumps, no prompts.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1"
MAX_QUESTION_CHARS = 2000
MAX_SUMMARY_CHARS = 1500
MAX_FACTS_PER_EVIDENCE = 25


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def stable_id(prefix: str, *parts: str) -> str:
    """Deterministic identifier from its inputs (the same inputs always give the same id)."""
    return f"{prefix}_{hashlib.sha256(chr(0).join(parts).encode()).hexdigest()[:20]}"


def investigation_id_for(request_id: str) -> str:
    """The investigation started by one conversation request (recomputable from the stored turn after a restart)."""
    return stable_id("inv", request_id)


class Record(BaseModel):
    """Base of every application record: frozen, closed, versioned, hashable."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["1"] = SCHEMA_VERSION

    def to_state(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)

    def record_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_state(), sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------------------------------------- enums
# The workers the controller can dispatch tasks to (values match the keys of authorization.OPERATIONS).
class WorkerName(str, Enum):
    STRUCTURED = "structured_data"
    DOCUMENT = "document_intelligence"
    PREDICTION = "ml_prediction"
    PLANNING = "procurement_planning"
    MONITORING = "project_monitoring"
    TENDER = "tender_design"
    LESSONS = "lesson_cards"


# The life of one task: pending -> running -> a terminal state (see ALLOWED_TRANSITIONS below).
class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"              # finished with incomplete evidence
    FAILED = "failed"
    BLOCKED = "blocked"              # a guardrail or privacy control stopped it
    CANCELLED = "cancelled"          # not run: a dependency failed or the investigation stopped
    SKIPPED = "skipped"              # not run: already executed (idempotent re-dispatch)


TERMINAL_TASK_STATES = frozenset({TaskState.SUCCEEDED, TaskState.PARTIAL, TaskState.FAILED, TaskState.BLOCKED,
                                  TaskState.CANCELLED, TaskState.SKIPPED})
USABLE_TASK_STATES = frozenset({TaskState.SUCCEEDED, TaskState.PARTIAL})
ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.SKIPPED}),
    TaskState.RUNNING: frozenset({TaskState.SUCCEEDED, TaskState.PARTIAL, TaskState.FAILED, TaskState.BLOCKED,
                                  TaskState.CANCELLED, TaskState.SKIPPED}),
    **{state: frozenset() for state in TERMINAL_TASK_STATES},
}


# The overall outcome of an investigation, as reported with its response.
class InvestigationStatus(str, Enum):
    ANSWERED = "answered"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    AWAITING_APPROVAL = "awaiting_approval"  # legacy: investigations saved before human review was removed
    REJECTED = "rejected"                    # legacy: the same (an approval was refused); never set now
    CLARIFICATION_REQUIRED = "clarification_required"
    BLOCKED = "blocked"                      # input guardrail or privacy control
    FAILED = "failed"


# Broad error classes, so the graph can decide what to do (retry, replan, stop) without reading messages.
class ErrorCategory(str, Enum):
    VALIDATION = "validation"                # plan or contract validation
    AUTHORIZATION = "authorization"
    GUARDRAIL = "guardrail"
    BUDGET = "budget"
    DEADLINE = "deadline"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    CIRCUIT_OPEN = "circuit_open"
    PROVIDER = "provider"
    PLANNER = "planner"
    EXECUTION = "execution"
    NO_EVIDENCE = "no_evidence"
    CLARIFICATION = "clarification"
    APPROVAL = "approval"                    # legacy: errors saved before human review was removed
    CANCELLED = "cancelled"                  # the user cancelled the investigation
    INTERNAL = "internal"


class ValidationStatus(str, Enum):
    VERIFIED = "verified"                    # checked by a deterministic control (validator + gateway, page check)
    NEEDS_REVIEW = "needs_review"            # present but flagged (e.g. extraction warnings)
    UNVERIFIED = "unverified"                # not checked against its source


# How a figure taken from a piece of evidence may be used in an answer.
class NumericUse(str, Enum):
    APPROVED = "approved"                    # validated dataset values (structured results through the gateway)
    DOCUMENT_STATEMENT = "document_statement"  # a figure stated in narrative document text: quotable with its citation
    NOT_APPROVED = "not_approved"            # e.g. PDF table values: never approved numerical evidence
    MODEL_ESTIMATE = "model_estimate"        # output of a frozen, hash-verified model: quotable as an estimate only


# Which system a piece of evidence came from.
class SourceSystem(str, Enum):
    DATABRICKS_PUBLIC = "databricks_public_procurement"   # Gold / Silver via the read-only gateway
    STRUCTURED_REGISTRY = "structured_registry"           # registry metadata (metric definitions, tables)
    RAG_PDF_CORPUS = "rag_pdf_corpus"                     # sanitised PDF corpus (narrative chunks, table windows)
    TYPED_PLAN_RECORDS = "typed_plan_records"             # procurement-plan activities extracted from PDFs
    FROZEN_ML_MODELS = "frozen_ml_models"                 # Module 2 models (ml/final, ml/final23), hash-verified


# What a piece of evidence is; each operation's allowed kinds are listed in OPERATION_EVIDENCE.
class EvidenceKind(str, Enum):
    STRUCTURED_RESULT = "structured_result"
    VALIDATED_QUERY = "validated_query"      # SQL approved by the validator but not executed
    METRIC_DEFINITION = "metric_definition"
    TABLE_CANDIDATE = "table_candidate"
    DOCUMENT_PASSAGE = "document_passage"
    DOCUMENT_TABLE = "document_table"
    TYPED_PLAN_RECORD = "typed_plan_record"
    MODEL_PREDICTION = "model_prediction"
    SIMILAR_PROJECT = "similar_project"
    LESSON_CARD = "lesson_card"              # a reviewed lesson from a completion report, IEG review or appraisal
    TENDER_CHECK = "tender_check"            # a tender-design term or check against history (tender rules)
    EARLY_WARNING = "early_warning"          # a project's milestone status or early warning (monitoring rules)


# ------------------------------------------------------------------------------------------------------- owners
class OwnerRef(Record):
    """Trusted owner of a conversation, investigation and its approvals (from authentication, never from text)."""

    tenant_id: str = Field(min_length=1, max_length=100)
    user_hash: str = Field(min_length=16, max_length=64)

    @classmethod
    def from_identity(cls, tenant_id: str, user_id: str) -> "OwnerRef":
        return cls(tenant_id=tenant_id, user_hash=hashlib.sha256(f"{tenant_id}\x00{user_id}".encode()).hexdigest()[:32])


# ------------------------------------------------------------------------------------------------------- budgets
# The limits of one investigation; budget.check_budget refuses a step before it would exceed any of them.
class InvestigationBudget(Record):
    max_tasks: int = Field(default=6, ge=1, le=20)
    max_depth: int = Field(default=3, ge=1, le=6)
    max_parallel: int = Field(default=1, ge=1, le=4)      # 1 today (synchronous); contracts allow more later
    max_llm_calls: int = Field(default=8, ge=0)
    max_input_tokens: int = Field(default=80_000, ge=0)
    max_output_tokens: int = Field(default=12_000, ge=0)
    max_cost_usd: Decimal | None = None                    # enforced only when a price table is configured
    max_executions: int = Field(default=3, ge=0)            # Databricks executions
    max_attempts_per_task: int = Field(default=2, ge=1, le=3)
    task_deadline_seconds: float = Field(default=60.0, gt=0)
    deadline_seconds: float = Field(default=120.0, gt=0)    # execution time of the whole investigation
    max_replans: int = Field(default=1, ge=0, le=2)


# What a call or task actually used, as reported by the provider.
class Usage(Record):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    executions: int = 0
    cost_usd: Decimal | None = None                         # None = no price table: never estimated


# ------------------------------------------------------------------------------------------------------ requests
class PackageSpec(Record):
    """A procurement package to assess (mode B: project ID + package basics; the rest is looked up in Gold)."""

    project_id: str = Field(pattern=r"^P\d{6}$")
    procurement_method: str = Field(min_length=1, max_length=100)
    procurement_category: str = Field(min_length=1, max_length=60)
    review_type: str = Field(min_length=1, max_length=20)
    bid_window_days: int = Field(ge=0, le=365)
    tender_date: dt.date


class PackageAssessment(PackageSpec):
    """A package to assess with the three models. The project comes from the user, so one without a World Bank ID yet
    can be assessed; for a country, its region and income group are read from Gold (a regional project gives its region).
    A project ID, when given, only scopes the search for documented lessons. The profile fields default to None only so
    that assessments saved before they existed still load (they are then refused)."""

    project_id: str | None = Field(default=None, pattern=r"^P\d{6}$")
    region_name: str | None = Field(default=None, min_length=1, max_length=80)          # needed for a regional project
    country_code: str | None = Field(default=None, pattern=r"^([A-Z0-9]{2}|REGIONAL)$")   # WDI code; REGIONAL = multi-country
    sector: str | None = Field(default=None, min_length=1, max_length=80)
    board_approval_date: dt.date | None = None                                           # planned or actual Board approval
    income_group: Literal["L", "LM", "UM", "H"] | None = None                             # None = from Gold, by tender year
    prior_tender_notices: int = Field(default=0, ge=0, le=5000)                           # tenders the project published before

    def label(self) -> str:
        """How the package's project is named in plans, messages and citations."""
        return self.project_id or f"a new {self.sector or ''} project in {self.country_code or 'an unnamed country'}".replace("  ", " ")


class TenderSpec(PackageSpec):
    """A tender design to check: the package, its estimate and lots, and the draft text (ToR, specification or
    qualification section; untrusted, already filtered for personal data)."""

    estimated_amount_usd: float | None = Field(default=None, gt=0)
    lots: int = Field(default=1, ge=1, le=50)
    draft_text: str = Field(default="", max_length=20_000)


class ProjectProfile(Record):
    """A new project being planned (no project ID yet)."""

    country_code: str = Field(pattern=r"^[A-Z]{2}$")                     # WDI two-letter code, e.g. ET
    sector: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=20, max_length=1500)            # what the project will do (untrusted text)
    approval_date: dt.date                                              # planned Board approval
    total_cost_usd: float | None = Field(default=None, gt=0)


# One package of a Planning Brief (a project that does not exist yet).
class PlannedPackage(Record):
    name: str = Field(min_length=1, max_length=80)
    procurement_category: str = Field(min_length=1, max_length=60)
    procurement_method: str | None = Field(default=None, max_length=100)  # None: suggested from historical practice
    estimated_amount_usd: float | None = Field(default=None, gt=0)
    review_type: str = Field(default="Prior", min_length=1, max_length=20)
    bid_window_days: int = Field(ge=0, le=365)
    tender_date: dt.date


class PlanningSpec(Record):
    """A Planning Brief request: the new project and its planned packages."""

    profile: ProjectProfile
    packages: tuple[PlannedPackage, ...] = Field(min_length=1, max_length=5)


# Everything an investigation starts from. A tender, package, planning or monitor_project selects a fixed plan
# (supervisor.py); otherwise the question is routed.
class InvestigationRequest(Record):
    investigation_id: str
    request_id: str
    conversation_thread_id: str
    owner: OwnerRef
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)   # standalone, already redacted
    route_hint: Literal["structured_data", "document_rag"] | None = None  # from the conversation front door
    mode: Literal["auto", "fast", "investigation"] = "auto"
    rag_release: Literal["pilot_v2", "pilot_v2_1", "pilot_v3", "pilot_v3_tablelens"] = "pilot_v3"   # default: 237 docs, Lakebase pgvector
    execute_requested: bool = False                                      # the user asked to run validated SQL
    package: PackageAssessment | None = None                             # a package assessment (ML predictions)
    planning: PlanningSpec | None = None                                 # a Planning Brief for a new project
    tender: TenderSpec | None = None                                     # a tender design to check against history
    monitor_project: str | None = Field(default=None, pattern=r"^P\d{6}$")  # a project's milestones and early warnings
    planning_context: str | None = Field(default=None, max_length=3000)  # the brief's profile + similar projects
    context_ref: str | None = None                                       # memory block id (text is not in state)
    authorization: "AppAuthorizationContext"
    budget: InvestigationBudget = InvestigationBudget()
    created_at: dt.datetime = Field(default_factory=utc_now)


# --------------------------------------------------------------------------------------------------------- plans
# What a task must produce: at least `min_count` records of the accepted kinds (verified ones, when required).
class EvidenceRequirement(Record):
    kind: EvidenceKind
    alternatives: tuple[EvidenceKind, ...] = ()         # other kinds that satisfy the same requirement
    min_count: int = Field(default=1, ge=1, le=20)
    must_be_verified: bool = True

    @property
    def accepted_kinds(self) -> tuple[EvidenceKind, ...]:
        return (self.kind, *self.alternatives)


class EvidenceSpec(BaseModel):
    """What an operation can return: its evidence kinds, and whether its records can be VERIFIED."""

    model_config = ConfigDict(frozen=True)
    kinds: tuple[EvidenceKind, ...]
    verifiable: bool


DOCUMENT_KINDS = (EvidenceKind.DOCUMENT_PASSAGE, EvidenceKind.DOCUMENT_TABLE, EvidenceKind.TYPED_PLAN_RECORD)
OPERATION_EVIDENCE = {
    "describe_metric": EvidenceSpec(kinds=(EvidenceKind.METRIC_DEFINITION,), verifiable=True),
    "find_tables": EvidenceSpec(kinds=(EvidenceKind.TABLE_CANDIDATE,), verifiable=True),
    "answer_document_question": EvidenceSpec(kinds=DOCUMENT_KINDS, verifiable=True),   # page-checked citations
    "retrieve_passages": EvidenceSpec(kinds=DOCUMENT_KINDS, verifiable=False),         # raw search hits: never page-checked
    "predict_package": EvidenceSpec(kinds=(EvidenceKind.MODEL_PREDICTION,), verifiable=True),  # hash-checked frozen models
    "predict_planned_packages": EvidenceSpec(kinds=(EvidenceKind.MODEL_PREDICTION,), verifiable=True),
    "match_similar_projects": EvidenceSpec(kinds=(EvidenceKind.SIMILAR_PROJECT,), verifiable=True),
    "market_benchmarks": EvidenceSpec(kinds=(EvidenceKind.STRUCTURED_RESULT,), verifiable=True),   # fixed read-only SQL
    "governance_context": EvidenceSpec(kinds=(EvidenceKind.STRUCTURED_RESULT,), verifiable=True),
    "watch_project": EvidenceSpec(kinds=(EvidenceKind.EARLY_WARNING,), verifiable=True),   # deterministic rules, cited rows
    "find_lessons": EvidenceSpec(kinds=(EvidenceKind.LESSON_CARD,), verifiable=True),    # reviewer-approved cards only
    "check_tender_design": EvidenceSpec(kinds=(EvidenceKind.TENDER_CHECK,), verifiable=True),  # rules + fixed Gold SQL
}


def evidence_spec(operation: str, requires_execution: bool) -> EvidenceSpec | None:
    """The single source of what each operation yields (used by planning, plan validation and verification).

    `answer_data_question` returns a STRUCTURED_RESULT when its SQL runs and a VALIDATED_QUERY when it only validates.
    """
    if operation == "answer_data_question":
        kind = EvidenceKind.STRUCTURED_RESULT if requires_execution else EvidenceKind.VALIDATED_QUERY
        return EvidenceSpec(kinds=(kind,), verifiable=True)
    return OPERATION_EVIDENCE.get(operation)


def default_requirement(operation: str, requires_execution: bool) -> EvidenceRequirement | None:
    """The evidence a task of this operation must produce; verification is demanded only when it is possible."""
    spec = evidence_spec(operation, requires_execution)
    if spec is None:
        return None
    return EvidenceRequirement(kind=spec.kinds[0], alternatives=spec.kinds[1:], must_be_verified=spec.verifiable)


# One unit of work in a plan, run by one worker operation.
class AgentTask(Record):
    task_id: str                                 # stable: <plan id>.<local id>
    local_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,15}$")
    investigation_id: str
    plan_id: str
    worker: WorkerName
    operation: str
    question: str = Field(min_length=1, max_length=1000)
    depends_on: tuple[str, ...] = ()             # task ids
    evidence_requirements: tuple[EvidenceRequirement, ...] = ()
    requires_execution: bool = False             # structured: run the validated read-only SQL
    optional: bool = False                       # its failure gives a partial result instead of a failed one
    deadline_seconds: float | None = Field(default=None, gt=0)
    max_attempts: int = Field(default=1, ge=1, le=3)
    idempotency_key: str


# When a plan's answer counts as complete.
class CompletionCondition(Record):
    required_task_ids: tuple[str, ...]           # must succeed for a complete answer
    min_verified_evidence: int = Field(default=1, ge=0)
    partial_allowed: bool = True                 # otherwise a missing required result fails the investigation


# One version of a plan; a replan creates a new version that supersedes the old one.
class InvestigationPlan(Record):
    plan_id: str
    investigation_id: str
    version: int = Field(ge=1)
    supersedes: str | None = None
    produced_by: Literal["fast_path", "fake_planner", "claude_planner"]
    tasks: tuple[AgentTask, ...] = Field(min_length=1)
    completion: CompletionCondition
    rationale: str = Field(default="", max_length=1000)   # planner text: untrusted, never shown as fact
    created_at: dt.datetime = Field(default_factory=utc_now)

    def task(self, task_id: str) -> AgentTask:
        return next(t for t in self.tasks if t.task_id == task_id)


# ------------------------------------------------------------------------------------------------------ evidence
class Fact(Record):
    """One bounded value from verified evidence, e.g. a result cell or a metric property."""

    name: str = Field(max_length=120)
    value: str = Field(max_length=200)
    unit: str | None = Field(default=None, max_length=60)


# One entry of the append-only evidence ledger, with its source, citation and how its numbers may be used.
class EvidenceRecord(Record):
    evidence_id: str
    investigation_id: str
    task_id: str
    source_system: SourceSystem
    kind: EvidenceKind
    project_ids: tuple[str, ...] = ()
    document_id: str | None = None
    document_version_id: str | None = None
    table_id: str | None = None
    unit_id: str | None = None
    pages: tuple[int, int] | None = None
    source_objects: tuple[str, ...] = ()         # Gold / Silver objects read
    source_version: str | None = None            # registry build, corpus release, validation id
    citation: str = Field(max_length=300)
    provenance: tuple[tuple[str, str], ...] = ()  # (key, value) pairs: validation_id, sql_sha256, result_ref, page_check
    validation_status: ValidationStatus
    numeric_use: NumericUse
    quality_warnings: tuple[str, ...] = ()
    facts: tuple[Fact, ...] = Field(default=(), max_length=MAX_FACTS_PER_EVIDENCE)
    summary: str = Field(default="", max_length=MAX_SUMMARY_CHARS)
    created_at: dt.datetime = Field(default_factory=utc_now)

    def provenance_value(self, key: str) -> str | None:
        return next((v for k, v in self.provenance if k == key), None)


# ------------------------------------------------------------------------------------------------------- results
# An error safe to store and show: a category and code, never exception text.
class ExecutionError(Record):
    error_id: str
    investigation_id: str
    task_id: str | None = None
    node: str
    category: ErrorCategory
    code: str = Field(max_length=80)
    retryable: bool = False
    retry_after_seconds: float | None = None
    safe_message: str = Field(max_length=300)    # never exception text or user content
    created_at: dt.datetime = Field(default_factory=utc_now)


# The outcome of one attempt of one task (one record per task and attempt).
class AgentResult(Record):
    task_id: str
    attempt: int = Field(ge=1)
    worker: WorkerName
    idempotency_key: str
    status: TaskState
    evidence_ids: tuple[str, ...] = ()
    error_id: str | None = None
    usage: Usage = Usage()
    duration_ms: int = 0
    notes: tuple[str, ...] = ()

    @field_validator("status")
    @classmethod
    def _terminal(cls, value: TaskState) -> TaskState:
        if value not in TERMINAL_TASK_STATES:
            raise ValueError("an agent result records a terminal task state")
        return value


# ------------------------------------------------------------------------------------------------------ response
BriefSection = Literal["similar_projects", "schedule", "market_approach", "risks", "lessons", "tender_design", "monitoring"]


# One sentence of the final answer and the evidence that supports it.
class ResponseClaim(Record):
    text: str = Field(min_length=1, max_length=600)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    section: BriefSection | None = None                 # Planning Brief section (None for ordinary answers)


# The checked answer of an investigation.
class InvestigationResponse(Record):
    investigation_id: str
    status: InvestigationStatus
    message: str = Field(max_length=4000)
    claims: tuple[ResponseClaim, ...] = ()
    removed_claims: tuple[str, ...] = ()          # reason codes only
    limitations: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    pending_approval_id: str | None = None        # legacy: always None (kept so saved responses still load)
    clarification_options: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()


from .authorization import AppAuthorizationContext  # noqa: E402  (forward reference of InvestigationRequest)

InvestigationRequest.model_rebuild()
