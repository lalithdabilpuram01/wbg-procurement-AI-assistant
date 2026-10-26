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
from conversation.routing.routes import ReasonCategory, Route
from conversation.service import ConversationReply, ConversationService
from conversation.store import ConversationStore

from .authorization import AppAuthorizationContext
from .contracts import EvidenceKind, InvestigationRequest, InvestigationStatus, OwnerRef, ValidationStatus, new_id
from .engine import EngineOutcome, InvestigationEngine

COMBINABLE = {Route.STRUCTURED_DATA.value, Route.DOCUMENT_RAG.value}


@dataclass(frozen=True)
class OwnerBinding:
    owner: Owner
    thread_id: str


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
                 rag_release: str = "pilot_v2"):
        if route.value not in COMBINABLE:
            raise ValueError("the orchestrator runs only the structured_data and document_rag routes")
        self._engine, self._route, self._authorization_for, self._release = engine, route, authorization_for, rag_release

    def __call__(self, question: str, conversation_context: str, execute: bool, request_id: str) -> ConversationOutcome:
        binding = CURRENT_OWNER.get()
        if binding is None:
            raise MissingTrustedOwner("no trusted owner bound to this request")
        request = InvestigationRequest(
            investigation_id=new_id("inv"), request_id=request_id, conversation_thread_id=binding.thread_id,
            owner=owner_ref(binding.owner), question=question, route_hint=self._route.value, execute_requested=execute,
            rag_release=self._release, authorization=self._authorization_for((self._route,)))
        return to_outcome(self._engine.start(request, memory_block=conversation_context or None))


@dataclass
class FrontDoorReply:
    conversation: ConversationReply
    investigation: EngineOutcome | None = None

    @property
    def status(self) -> str:
        return self.investigation.status.value if self.investigation else self.conversation.status


class ConversationFrontDoor:
    def __init__(self, service: ConversationService, store: ConversationStore, engine: InvestigationEngine,
                 authorization_for: AuthorizationFactory, *, allow_combined: bool = True, rag_release: str = "pilot_v2"):
        self._service, self._store, self._engine = service, store, engine
        self._authorization_for, self._allow_combined, self._release = authorization_for, allow_combined, rag_release

    def handle_message(self, owner: Owner, thread_id: str, text: str, execute: bool = False) -> FrontDoorReply:
        token = CURRENT_OWNER.set(OwnerBinding(owner, thread_id))
        try:
            reply = self._service.handle_message(owner, thread_id, text, execute=execute)
        finally:
            CURRENT_OWNER.reset(token)
        if not self._is_combined(reply):
            outcome = reply.agent_result.investigation if isinstance(reply.agent_result, ConversationOutcome) else None
            return FrontDoorReply(reply, outcome)
        return FrontDoorReply(reply, self._investigate(owner, thread_id, reply, execute))

    def approve(self, owner: Owner, thread_id: str, checkpoint_thread_id: str, approval_id: str, approved: bool,
                reason: str | None = None) -> EngineOutcome:
        self._store.get_thread(owner, thread_id)                                    # ownership of the conversation
        outcome = self._engine.resume(owner_ref(owner), checkpoint_thread_id, approval_id, approved, reason)
        self._service.record_decision(owner, thread_id, "decision",
                                      f"Investigation approval {approval_id}: {'approved' if approved else 'rejected'}")
        return outcome

    # ------------------------------------------------------------------------------------------- internals
    def _is_combined(self, reply: ConversationReply) -> bool:
        routing = reply.routing
        return bool(self._allow_combined and routing is not None and routing.reason == ReasonCategory.MIXED_INTENT
                    and set(routing.matched_signals) == COMBINABLE)

    def _investigate(self, owner: Owner, thread_id: str, reply: ConversationReply, execute: bool) -> EngineOutcome:
        question = next(t.text for t in self._store.turns(owner, thread_id) if t.seq == reply.user_turn_seq)   # redacted text
        routes = (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)
        request = InvestigationRequest(
            investigation_id=new_id("inv"), request_id=new_id("req"), conversation_thread_id=thread_id, owner=owner_ref(owner),
            question=question, mode="investigation", execute_requested=execute, rag_release=self._release,
            authorization=self._authorization_for(routes))
        outcome = self._engine.start(request)
        thread = self._store.get_thread(owner, thread_id)
        evidence = [EvidenceReference(
            source_type="structured_result" if e.kind == EvidenceKind.STRUCTURED_RESULT else "rag_document",
            source_id=e.provenance_value("validation_id") or e.document_id or e.evidence_id, objects=list(e.source_objects),
            citation=e.citation, verified_by="orchestrator_evidence_ledger")
            for e in outcome.evidence if e.validation_status == ValidationStatus.VERIFIED]
        self._store.append_turn(owner, Turn(
            thread_id=thread_id, seq=self._store.next_seq(owner, thread_id), role="assistant", kind=TurnKind.ANSWER,
            text=redact_secrets(outcome.response.message if outcome.response else ""), topic_epoch=thread.topic_epoch,
            metadata=TurnMetadata(request_id=request.request_id, status=outcome.status.value, route="investigation",
                                  source_objects=sorted({o for e in outcome.evidence for o in e.source_objects}),
                                  evidence=evidence[:20])))
        return outcome
