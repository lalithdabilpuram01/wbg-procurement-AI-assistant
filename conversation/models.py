"""Typed records of the conversation service.

Ownership (`Owner`) always comes from the trusted, authenticated caller, never from message text. Original turns are
immutable; summaries, decisions and evidence references are stored separately and clearly labelled.
"""
from __future__ import annotations

import datetime as dt
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Owner(BaseModel, frozen=True):
    """Trusted identity of the conversation owner (from authentication, never from user text)."""

    tenant_id: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=100)


class TurnKind(str, Enum):
    QUESTION = "question"                  # a new question (no inherited filters)
    REFINEMENT = "refinement"              # narrows or adds filters to the active query
    COMPARISON = "comparison"              # adds entities to compare with the active query
    REFERENCE = "reference"                # refers to the active entities ("those projects")
    FILTER_REMOVAL = "filter_removal"      # removes a filter from the active query
    CORRECTION = "correction"              # replaces an entity ("I meant Tanzania, not Uganda")
    TOPIC_RESET = "topic_reset"            # starts a new topic; earlier context is not reused
    CONVERSATION = "conversation"          # small talk, a general explanation or a memory recall (no specialist agent)
    CLARIFICATION_REQUIRED = "clarification_required"
    ANSWER = "answer"                      # assistant turn


class CountryRef(BaseModel, frozen=True):
    name: str
    code: str                              # WDI 2-letter code used by the Gold tables (wdi_country_code)


class ActiveContext(BaseModel):
    """The entities and filters the current query is about (deterministically tracked)."""

    topic: str | None = None               # the latest full question the filters apply to
    countries: list[CountryRef] = Field(default_factory=list)
    fiscal_years: list[int] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    compare_by_country: bool = False
    assumptions: list[str] = Field(default_factory=list)   # stated interpretations, e.g. a bare year read as a fiscal year

    def is_empty(self) -> bool:
        return not (self.countries or self.fiscal_years or self.project_ids)


class ThreadInfo(BaseModel):
    thread_id: str
    owner: Owner
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime
    topic_epoch: int = 0                   # incremented by a topic reset; earlier epochs are not reused


class EvidenceReference(BaseModel):
    """A reference to independently verified evidence (not a conversation summary)."""

    source_type: Literal["structured_result", "rag_document", "ml_prediction"]
    source_id: str                         # validation id, document id or prediction id
    objects: list[str] = Field(default_factory=list)   # e.g. Gold tables read
    citation: str | None = None            # document/page citation for RAG evidence
    row_count: int | None = None
    verified_by: str                       # which control verified it (e.g. "sqlglot_validator+execution_gateway")


class TurnMetadata(BaseModel):
    """Safe metadata only: no result rows, credentials or prompts."""

    request_id: str | None = None
    standalone_question: str | None = None   # the resolved, explicit question sent to an agent
    status: str | None = None
    failure_class: str | None = None
    route: str | None = None
    route_reason: str | None = None
    guardrail_reason: str | None = None
    blocked: bool = False
    validated_sql: str | None = None
    executed: bool = False
    row_count: int | None = None
    source_objects: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    clarification_options: list[str] = Field(default_factory=list)
    resolution_notes: list[str] = Field(default_factory=list)
    registry_build_id: str | None = None


class Turn(BaseModel):
    turn_id: str = Field(default_factory=new_id)
    thread_id: str
    seq: int
    role: Literal["user", "assistant"]
    kind: TurnKind
    text: str                              # original text after secret redaction
    topic_epoch: int
    context: ActiveContext = Field(default_factory=ActiveContext)   # the context after this turn
    metadata: TurnMetadata = Field(default_factory=TurnMetadata)
    created_at: dt.datetime = Field(default_factory=utc_now)


class Decision(BaseModel):
    """A user-approved decision or constraint (explicitly recorded; never inferred from text)."""

    decision_id: str = Field(default_factory=new_id)
    thread_id: str
    kind: Literal["decision", "constraint"]
    statement: str = Field(min_length=3, max_length=500)
    approved_by: str                       # trusted user id of the approver
    source_turn_seq: int | None = None
    created_at: dt.datetime = Field(default_factory=utc_now)
    revoked_at: dt.datetime | None = None
    revoked_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class StructuredSummary(BaseModel):
    """Compressed memory of older turns. Generated, NOT verified evidence."""

    questions: list[str] = Field(default_factory=list)          # short forms of earlier user questions
    countries: list[str] = Field(default_factory=list)          # WDI codes
    fiscal_years: list[int] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    evidence_source_ids: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)           # e.g. "answered", "rejected: sql_validation_denied"
    corrections: list[str] = Field(default_factory=list)        # e.g. "turn 9: Uganda replaced by Tanzania"
    narrative: str = ""


class SummaryRecord(BaseModel):
    summary_id: str = Field(default_factory=new_id)
    thread_id: str
    version: int
    topic_epoch: int
    covers_from_seq: int
    covers_through_seq: int
    content: StructuredSummary
    generator: str
    inputs_sha256: str
    registry_build_id: str | None = None
    created_at: dt.datetime = Field(default_factory=utc_now)
    invalidated_at: dt.datetime | None = None
    invalidation_reason: str | None = None

    @property
    def valid(self) -> bool:
        return self.invalidated_at is None


def json_safe(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
