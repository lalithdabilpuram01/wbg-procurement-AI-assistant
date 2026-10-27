"""The existing ConversationService stays the front door; the investigation engine runs behind it.

* `ConversationRunner` has the existing AgentRunner signature and is registered for the structured_data and
  document_rag routes. Its authorization is the routed capabilities only (no widening). The trusted owner reaches it
  through a context variable set by `ConversationFrontDoor` (the runner signature has no owner; unchanged).
* `ConversationOutcome` provides the attributes the service's `result_metadata` reads, so the service stores safe
  metadata for orchestrated answers exactly as for the NL2SQL workflow.
* Combined data + document questions: the existing router answers them with a mixed-intent clarification. When the
  application allows combined investigations, the front door runs one investigation whose authorization is the
  union of exactly those two route policies, and records the answer turn through the existing store API.
* Approvals: `approve` checks thread ownership, records the decision with the existing `record_decision`, and
  resumes the engine as the same user.
Nothing in conversation/ is modified.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Callable

from conversation.models import EvidenceReference, Owner, Turn, TurnKind, TurnMetadata
from conversation.redaction import redact_secrets
from conversation.replies import SCOPE
from conversation.routing.rules import mentions_entities, normalize
from structured_agent.guardrails.policies import mentions_domain
from conversation.routing.routes import ReasonCategory, Route, RoutingDecision
from conversation.service import DEFAULT_TITLE, TITLE_CHARS, ConversationReply, ConversationService
from conversation.store import ConversationStore

from .authorization import AppAuthorizationContext
from .contracts import (EvidenceKind, InvestigationRequest, InvestigationStatus, OwnerRef, PackageSpec, PlanningSpec,
                        ValidationStatus, investigation_id_for, new_id)
from .engine import EngineOutcome, InvestigationEngine, InvestigationNotFound, checkpoint_thread_id
from .planner import BRIEF_BUDGET

COMBINABLE = {Route.STRUCTURED_DATA.value, Route.DOCUMENT_RAG.value}
UNCLEAR = {ReasonCategory.NO_CLEAR_INTENT, ReasonCategory.CLASSIFIER_LOW_SCORE, ReasonCategory.CLASSIFIER_ERROR}
PACKAGE_FIELDS_MESSAGE = (
    "I can estimate the tender duration and the amendment and re-bidding risks for a procurement package. "
    "Please use “Assess package” and give: 1) the project ID (P + 6 digits, e.g. P148850); 2) the procurement method "
    "(e.g. Request for Bids); 3) the category (Works, Goods, Consultant Services or Non-consulting Services); 4) the review "
    "type (Prior or Post); 5) the bid window in days; 6) the planned tender date. Region, country, sector, income group and "
    "earlier tenders are looked up from the project records.")
SOURCE_TYPES = {EvidenceKind.STRUCTURED_RESULT: "structured_result", EvidenceKind.MODEL_PREDICTION: "ml_prediction",
                EvidenceKind.SIMILAR_PROJECT: "structured_result"}
BRIEF_ROUTE = "planning_brief"
PLANNING_FIELDS_MESSAGE = (
    "I can prepare a Planning Brief for a new project from past similar projects. Please use “Plan a new project” and "
    "give: the country, the sector, a short description of what the project will do, the planned Board approval date "
    "(and the estimated cost if known), and for each planned package its name, category, method (or leave it for me to "
    "suggest), review type, bid window and planned tender date.")


def describe_plan(spec: PlanningSpec) -> str:
    profile = spec.profile
    packages = "; ".join(f"{p.name} ({p.procurement_category}, {p.procurement_method or 'method to suggest'}, "
                         f"{p.review_type} review, tender {p.tender_date.isoformat()})" for p in spec.packages)
    return (f"Plan a new {profile.sector} project in {profile.country_code}, Board approval planned for "
            f"{profile.approval_date.isoformat()}: {profile.description} Packages: {packages}.")[:2000]


def describe_package(package: PackageSpec) -> str:
    return (f"Assess a {package.procurement_category} package for project {package.project_id}: {package.procurement_method}, "
            f"{package.review_type} review, {package.bid_window_days}-day bid window, tender on {package.tender_date.isoformat()}.")


@dataclass(frozen=True)
class OwnerBinding:
    owner: Owner
    thread_id: str
    on_start: Callable[[str], None] | None = None          # told the request id before the engine starts (to cancel)


CURRENT_OWNER: contextvars.ContextVar[OwnerBinding | None] = contextvars.ContextVar("wbg_current_owner", default=None)


class MissingTrustedOwner(PermissionError):
    """An orchestrated runner was called without the trusted owner of the request."""


@dataclass
class ConversationOutcome:
    """What ConversationService and result_metadata read from a runner result (no rows, no SQL text)."""

    status: InvestigationStatus
    message: str
    clarification_options: list[str] = field(default_factory=list)
    source_objects: list[str] = field(default_factory=list)
    context_tables: list[str] = field(default_factory=list)
    failure_class: Any = None
    guardrail_reason: str | None = None
    validated_sql: str | None = None
    executed_sql: str | None = None
    validation: Any = None
    row_count: int = 0
    investigation: EngineOutcome | None = None


AuthorizationFactory = Callable[[tuple[Route, ...]], AppAuthorizationContext]


def owner_ref(owner: Owner) -> OwnerRef:
    return OwnerRef.from_identity(owner.tenant_id, owner.user_id)


def to_outcome(result: EngineOutcome) -> ConversationOutcome:
    response = result.response
    objects = sorted({o for e in result.evidence for o in e.source_objects})
    return ConversationOutcome(status=result.status, message=response.message if response else "",
                               clarification_options=list(response.clarification_options if response else ()),
                               source_objects=objects, investigation=result,
                               guardrail_reason=(response.error_codes[0] if response and result.status == InvestigationStatus.BLOCKED
                                                 and response.error_codes else None))


class ConversationRunner:
    def __init__(self, engine: InvestigationEngine, route: Route, authorization_for: AuthorizationFactory,
                 rag_release: str = "pilot_v3"):
        if route.value not in COMBINABLE:
            raise ValueError("the orchestrator runs only the structured_data and document_rag routes")
        self._engine, self._route, self._authorization_for, self._release = engine, route, authorization_for, rag_release

    def __call__(self, question: str, conversation_context: str, execute: bool, request_id: str) -> ConversationOutcome:
        binding = CURRENT_OWNER.get()
        if binding is None:
            raise MissingTrustedOwner("no trusted owner bound to this request")
        request = InvestigationRequest(
            investigation_id=investigation_id_for(request_id), request_id=request_id, conversation_thread_id=binding.thread_id,
            owner=owner_ref(binding.owner), question=question, route_hint=self._route.value, execute_requested=execute,
            rag_release=self._release, authorization=self._authorization_for((self._route,)))
        if binding.on_start:
            binding.on_start(request_id)
        return to_outcome(self._engine.start(request, memory_block=conversation_context or None))


@dataclass
class FrontDoorReply:
    status: str
    request_id: str | None
    conversation: ConversationReply | None = None           # set when the conversation service handled the message
    investigation: EngineOutcome | None = None


class ConversationFrontDoor:
    def __init__(self, service: ConversationService, store: ConversationStore, engine: InvestigationEngine,
                 authorization_for: AuthorizationFactory, *, allow_combined: bool = True, rag_release: str = "pilot_v3"):
        self._service, self._store, self._engine = service, store, engine
        self._authorization_for, self._allow_combined, self._release = authorization_for, allow_combined, rag_release

    def handle_message(self, owner: Owner, thread_id: str, text: str, execute: bool = False,
                       on_start: Callable[[str], None] | None = None) -> FrontDoorReply:
        """Questions for both agents, and questions whose intent the rules cannot place, go straight to an
        investigation (its planner chooses the agents) instead of a clarifying question. Prediction requests ask for
        the package fields. Everything else is handled by the conversation service as before."""
        routing = self._service.route_message(owner, thread_id, text)
        if routing is not None and routing.route == Route.ML_PREDICTION:
            return self._ask_for_fields(owner, thread_id, text, Route.ML_PREDICTION.value, PACKAGE_FIELDS_MESSAGE)
        if routing is not None and routing.route == Route.PROCUREMENT_PLANNING:
            return self._ask_for_fields(owner, thread_id, text, BRIEF_ROUTE, PLANNING_FIELDS_MESSAGE)
        if routing is not None and routing.reason in UNCLEAR and not (mentions_domain(text) or mentions_entities(normalize(text))):
            return self._scope_reply(owner, thread_id, text)                  # nothing procurement-related to investigate
        planning = self._planning_context(owner, thread_id) if routing is not None else None
        if routing is not None and (self._opens_investigation(routing) or (planning and self._follows_brief(routing))):
            outcome = self._open_investigation(owner, thread_id, text, execute, on_start, planning)
            return FrontDoorReply(outcome.status.value, outcome.values["request"]["request_id"], investigation=outcome)
        token = CURRENT_OWNER.set(OwnerBinding(owner, thread_id, on_start))
        try:
            reply = self._service.handle_message(owner, thread_id, text, execute=execute)
        finally:
            CURRENT_OWNER.reset(token)
        outcome = reply.agent_result.investigation if isinstance(reply.agent_result, ConversationOutcome) else None
        user_turn = next((t for t in self._store.turns(owner, thread_id) if t.seq == reply.user_turn_seq), None)
        request_id = outcome.values["request"]["request_id"] if outcome else (user_turn.metadata.request_id if user_turn else None)
        return FrontDoorReply(outcome.status.value if outcome else reply.status, request_id, reply, outcome)

    def approve(self, owner: Owner, thread_id: str, checkpoint_thread_id: str, approval_id: str, approved: bool,
                reason: str | None = None) -> EngineOutcome:
        self._store.get_thread(owner, thread_id)                                    # ownership of the conversation
        outcome = self._engine.resume(owner_ref(owner), checkpoint_thread_id, approval_id, approved, reason)
        self._service.record_decision(owner, thread_id, "decision",
                                      f"Investigation approval {approval_id}: {'approved' if approved else 'rejected'}")
        self._record_answer(owner, thread_id, outcome.values["request"]["request_id"], outcome)    # history shows the result
        return outcome

    def plan_project(self, owner: Owner, thread_id: str, spec: PlanningSpec,
                     on_start: Callable[[str], None] | None = None) -> EngineOutcome:
        """A Planning Brief for a new project: recorded as a user turn, run with the fixed brief plan."""
        profile = spec.profile.model_copy(update={"description": self._service.text_filter(spec.profile.description)})
        spec = spec.model_copy(update={"profile": profile})                      # the free text is filtered once, here
        text = describe_plan(spec)
        memory = self._service.memory_for(owner, thread_id, text)                 # before this request's own turn
        request_id = self._user_turn(owner, thread_id, text, BRIEF_ROUTE)
        request = InvestigationRequest(
            investigation_id=investigation_id_for(request_id), request_id=request_id, conversation_thread_id=thread_id,
            owner=owner_ref(owner), question=self._service.text_filter(text), mode="investigation", planning=spec, budget=BRIEF_BUDGET,
            rag_release=self._release,
            authorization=self._authorization_for((Route.PROCUREMENT_PLANNING, Route.ML_PREDICTION, Route.DOCUMENT_RAG)))
        if on_start:
            on_start(request_id)
        outcome = self._engine.start(request, memory_block=memory)
        self._record_answer(owner, thread_id, request_id, outcome)
        self._service.compact(owner, thread_id)                                    # summarize older turns when needed
        return outcome

    def assess_package(self, owner: Owner, thread_id: str, package: PackageSpec,
                       on_start: Callable[[str], None] | None = None) -> EngineOutcome:
        """A structured package assessment: recorded as a user turn, run with the fixed prediction + lessons plan."""
        text = describe_package(package)
        memory = self._service.memory_for(owner, thread_id, text)
        request_id = self._user_turn(owner, thread_id, text, Route.ML_PREDICTION.value)
        request = InvestigationRequest(
            investigation_id=investigation_id_for(request_id), request_id=request_id, conversation_thread_id=thread_id,
            owner=owner_ref(owner), question=text, mode="investigation", package=package, rag_release=self._release,
            authorization=self._authorization_for((Route.ML_PREDICTION, Route.DOCUMENT_RAG)))
        if on_start:
            on_start(request_id)
        outcome = self._engine.start(request, memory_block=memory)
        self._record_answer(owner, thread_id, request_id, outcome)
        self._service.compact(owner, thread_id)                                    # summarize older turns when needed
        return outcome

    # ------------------------------------------------------------------------------------------- internals
    @staticmethod
    def _follows_brief(routing: RoutingDecision) -> bool:
        """After a brief, task questions and references ("these projects") go to the planner with the brief's context."""
        return routing.is_task or routing.reason in UNCLEAR | {ReasonCategory.AMBIGUOUS_REFERENT}

    def _opens_investigation(self, routing: RoutingDecision) -> bool:
        combined = routing.reason == ReasonCategory.MIXED_INTENT and set(routing.matched_signals) == COMBINABLE
        return self._allow_combined and (combined or routing.reason in UNCLEAR)

    def _user_turn(self, owner: Owner, thread_id: str, text: str, route: str) -> str:
        """Records the (redacted) user message with a new request id; names a new conversation after it."""
        thread = self._store.get_thread(owner, thread_id)                           # ownership of the conversation
        request_id = new_id("req")
        self._store.append_turn(owner, Turn(
            thread_id=thread_id, seq=self._store.next_seq(owner, thread_id), role="user", kind=TurnKind.QUESTION,
            text=self._service.text_filter(text), topic_epoch=thread.topic_epoch, metadata=TurnMetadata(request_id=request_id, route=route)))
        if thread.title == DEFAULT_TITLE:
            self._store.set_title(owner, thread_id, self._service.text_filter(text)[:TITLE_CHARS])
        return request_id

    def _open_investigation(self, owner: Owner, thread_id: str, text: str, execute: bool,
                            on_start: Callable[[str], None] | None, planning_context: str | None = None) -> EngineOutcome:
        memory = self._service.memory_for(owner, thread_id, text)
        request_id = self._user_turn(owner, thread_id, text, "investigation")
        request = InvestigationRequest(
            investigation_id=investigation_id_for(request_id), request_id=request_id, conversation_thread_id=thread_id,
            owner=owner_ref(owner), question=self._service.text_filter(text), mode="investigation", execute_requested=execute,
            rag_release=self._release, planning_context=planning_context,
            authorization=self._authorization_for((Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)))
        if on_start:
            on_start(request_id)
        outcome = self._engine.start(request, memory_block=memory)
        self._record_answer(owner, thread_id, request_id, outcome)
        self._service.compact(owner, thread_id)                                    # summarize older turns when needed
        return outcome

    def _ask_for_fields(self, owner: Owner, thread_id: str, text: str, route: str, message: str) -> FrontDoorReply:
        """Predictions and briefs need structured fields: say exactly which ones (no agent runs)."""
        status = "needs_package_details" if route == Route.ML_PREDICTION.value else "needs_project_details"
        request_id = self._user_turn(owner, thread_id, text, route)
        thread = self._store.get_thread(owner, thread_id)
        self._store.append_turn(owner, Turn(
            thread_id=thread_id, seq=self._store.next_seq(owner, thread_id), role="assistant", kind=TurnKind.ANSWER,
            text=message, topic_epoch=thread.topic_epoch, metadata=TurnMetadata(request_id=request_id, status=status, route=route)))
        return FrontDoorReply(status, request_id)

    def _scope_reply(self, owner: Owner, thread_id: str, text: str) -> FrontDoorReply:
        request_id = self._user_turn(owner, thread_id, text, Route.CONVERSATION.value)
        thread = self._store.get_thread(owner, thread_id)
        self._store.append_turn(owner, Turn(
            thread_id=thread_id, seq=self._store.next_seq(owner, thread_id), role="assistant", kind=TurnKind.ANSWER,
            text=SCOPE, topic_epoch=thread.topic_epoch,
            metadata=TurnMetadata(request_id=request_id, status="out_of_scope", route=Route.CONVERSATION.value)))
        return FrontDoorReply("out_of_scope", request_id)

    def _planning_context(self, owner: Owner, thread_id: str) -> str | None:
        """The latest brief of this conversation as planner context: the project profile and its similar projects."""
        brief = next((t for t in reversed(self._store.turns(owner, thread_id))
                      if t.role == "user" and t.metadata.route == BRIEF_ROUTE and t.metadata.request_id), None)
        if brief is None:
            return None
        owner_key = owner_ref(owner)
        try:
            outcome = self._engine.outcome(owner_key, checkpoint_thread_id(owner_key, thread_id,
                                                                           investigation_id_for(brief.metadata.request_id)))
        except InvestigationNotFound:
            return None
        spec = PlanningSpec.model_validate(outcome.values["request"]["planning"])
        similar = [e for e in outcome.evidence if e.kind == EvidenceKind.SIMILAR_PROJECT]
        listed = "; ".join(f"{e.project_ids[0]} {e.citation.split(': ', 1)[-1]}"
                           + (" [documents]" if any(f.name == "document_scope" and f.value == "yes" for f in e.facts) else "")
                           for e in similar if e.project_ids)
        return self._service.text_filter(f"{describe_plan(spec)} Similar past projects: {listed or 'none found'}.")[:3000]

    def _record_answer(self, owner: Owner, thread_id: str, request_id: str, outcome: EngineOutcome) -> None:
        """Append the investigation's answer as an assistant turn (safe metadata only: no SQL, no rows)."""
        thread = self._store.get_thread(owner, thread_id)
        evidence = [EvidenceReference(
            source_type=SOURCE_TYPES.get(e.kind, "rag_document"),
            source_id=e.provenance_value("validation_id") or e.document_id or e.evidence_id, objects=list(e.source_objects),
            citation=e.citation, verified_by="orchestrator_evidence_ledger")
            for e in outcome.evidence if e.validation_status == ValidationStatus.VERIFIED]
        self._store.append_turn(owner, Turn(
            thread_id=thread_id, seq=self._store.next_seq(owner, thread_id), role="assistant", kind=TurnKind.ANSWER,
            text=redact_secrets(outcome.response.message if outcome.response else ""), topic_epoch=thread.topic_epoch,
            metadata=TurnMetadata(request_id=request_id, status=outcome.status.value, route="investigation",
                                  source_objects=sorted({o for e in outcome.evidence for o in e.source_objects}),
                                  evidence=evidence[:20])))
