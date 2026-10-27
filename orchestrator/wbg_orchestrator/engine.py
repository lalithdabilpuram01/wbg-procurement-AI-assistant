"""Investigation engine: compiles the graph with a checkpointer and exposes start / resume / snapshot.

* Each investigation runs in its own checkpoint thread (an opaque id derived from owner, conversation thread and
  investigation). The default checkpointer is LangGraph's InMemorySaver; hosts pass the isolated PostgresSaver from
  `checkpointing.py` for Lakebase. Every save and resume emits a metadata-only checkpoint event.
* Every run happens inside `no_external_tracing()` (LangSmith forced off) with durability "sync".
* Conversation memory is request-scoped (kept in this process only, never in a checkpoint).
* `resume` checks, before touching the graph, that the caller owns the investigation and answers the pending
  approval; the approval node checks the decision again (payload hash, expiry, single use).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .contracts import (ApprovalDecision, ApprovalRequest, EvidenceRecord, InvestigationRequest, InvestigationResponse,
                        InvestigationStatus, OwnerRef, stable_id)
from .graph import EngineDeps, build_graph
from .safety import no_external_tracing


class InvestigationNotFound(LookupError):
    """No investigation with this id for this owner (the same error for a missing and a foreign one)."""


class ApprovalNotPending(LookupError):
    """There is no pending approval with this id."""


@dataclass(frozen=True)
class EngineOutcome:
    thread_id: str
    status: InvestigationStatus
    response: InvestigationResponse | None
    pending_approval: ApprovalRequest | None
    evidence: tuple[EvidenceRecord, ...]
    task_states: dict[str, str]
    values: dict[str, Any]
    unfinished: bool = False          # the workflow has steps left and is not waiting for an approval


def checkpoint_thread_id(owner: OwnerRef, conversation_thread_id: str, investigation_id: str) -> str:
    return stable_id("lgt", owner.tenant_id, owner.user_hash, conversation_thread_id, investigation_id)


class InvestigationEngine:
    def __init__(self, deps: EngineDeps, checkpointer: Any | None = None):
        self._memory: dict[str, str] = {}
        self._cancelled: set[str] = set()
        deps.memory = self._memory.get
        deps.cancel_requested = self._cancelled.__contains__
        self.deps = deps
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = build_graph(deps).compile(checkpointer=self.checkpointer)

    # ------------------------------------------------------------------------------------------------ api
    def start(self, request: InvestigationRequest, memory_block: str | None = None) -> EngineOutcome:
        thread_id = checkpoint_thread_id(request.owner, request.conversation_thread_id, request.investigation_id)
        if memory_block:
            self._memory[request.investigation_id] = memory_block
        self._invoke({"request": request.to_state()}, thread_id)
        return self.outcome(request.owner, thread_id)

    def resume(self, owner: OwnerRef, thread_id: str, approval_id: str, approved: bool, reason: str | None = None) -> EngineOutcome:
        snapshot = self._owned_snapshot(owner, thread_id)
        pending = _pending_approval(snapshot)
        if pending is None or pending.approval_id != approval_id:
            raise ApprovalNotPending("no pending approval with this id")
        decision = ApprovalDecision(approval_id=approval_id, payload_sha256=pending.payload_sha256, approved=approved,
                                    decided_by=owner, reason=reason)
        self._checkpoint_event("orchestrator.checkpoint_resumed", snapshot, thread_id)
        self._invoke(Command(resume=decision.to_state()), thread_id)
        return self.outcome(owner, thread_id)

    def cancel(self, owner: OwnerRef, thread_id: str) -> EngineOutcome:
        """Cancel an investigation: a pending approval is rejected; a running one stops before its next task.

        The task already running finishes (cooperative cancellation). A finished investigation is left unchanged.
        """
        snapshot = self._owned_snapshot(owner, thread_id)
        pending = _pending_approval(snapshot)
        if pending is not None:
            return self.resume(owner, thread_id, pending.approval_id, approved=False, reason="cancelled by the user")
        if snapshot.next:
            self._cancelled.add(snapshot.values["request"]["investigation_id"])
        return self.outcome(owner, thread_id)

    def request_cancel(self, investigation_id: str) -> None:
        """Flag an investigation to stop before its next task, also before its first checkpoint exists. The caller has
        already checked ownership (the app service owns the job that started it)."""
        self._cancelled.add(investigation_id)

    def recover(self, owner: OwnerRef, thread_id: str) -> EngineOutcome:
        """Continue an investigation stopped by a crash or restart from its last checkpoint.

        Does nothing when the investigation finished or waits for an approval (that needs `resume`).
        """
        snapshot = self._owned_snapshot(owner, thread_id)
        if snapshot.next and _pending_approval(snapshot) is None:
            self._checkpoint_event("orchestrator.checkpoint_resumed", snapshot, thread_id)
            self._invoke(None, thread_id)
        return self.outcome(owner, thread_id)

    def outcome(self, owner: OwnerRef, thread_id: str) -> EngineOutcome:
        snapshot = self._owned_snapshot(owner, thread_id)
        values = snapshot.values
        pending = _pending_approval(snapshot)
        response = InvestigationResponse.from_state(values["response"]) if values.get("response") else None
        if pending is not None:
            response = InvestigationResponse(
                investigation_id=pending.investigation_id, status=InvestigationStatus.AWAITING_APPROVAL,
                message=f"Approval needed before running: {pending.summary}", pending_approval_id=pending.approval_id)
        status = response.status if response else InvestigationStatus.FAILED
        evidence = tuple(EvidenceRecord.from_state(e) for e in values.get("evidence", []))
        return EngineOutcome(thread_id, status, response, pending, evidence, dict(values.get("task_states", {})), values,
                             unfinished=bool(snapshot.next) and pending is None)

    # ------------------------------------------------------------------------------------------- internals
    def _invoke(self, payload: Any, thread_id: str) -> None:
        config = {"configurable": {"thread_id": thread_id}}
        with no_external_tracing():
            self.graph.invoke(payload, config, durability="sync")
        self._checkpoint_event("orchestrator.checkpoint_saved", self.graph.get_state(config), thread_id)

    def _checkpoint_event(self, name: str, snapshot, thread_id: str) -> None:
        request = (snapshot.values or {}).get("request") or {}
        self.deps.tracer.event(name, checkpoint_thread=thread_id, checkpointer=type(self.checkpointer).__name__,
                               checkpoint_id=(snapshot.config or {}).get("configurable", {}).get("checkpoint_id"),
                               step=(snapshot.metadata or {}).get("step"), investigation_id=request.get("investigation_id"),
                               conversation_id=request.get("conversation_thread_id"))

    def _owned_snapshot(self, owner: OwnerRef, thread_id: str):
        snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        request = (snapshot.values or {}).get("request")
        if not request or OwnerRef.from_state(request["owner"]) != owner:
            raise InvestigationNotFound("investigation not found")
        return snapshot


def _pending_approval(snapshot) -> ApprovalRequest | None:
    for item in snapshot.interrupts or ():
        value = getattr(item, "value", None)
        if isinstance(value, dict) and "approval_id" in value:
            return ApprovalRequest.from_state(value)
    return None
