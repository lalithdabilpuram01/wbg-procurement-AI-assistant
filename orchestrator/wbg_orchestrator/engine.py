"""Investigation engine: compiles the graph with a checkpointer and exposes start / cancel / recover / snapshot.

* Each investigation runs in its own checkpoint thread (an opaque id derived from owner, conversation thread and
  investigation). The default checkpointer is LangGraph's InMemorySaver; hosts pass the isolated PostgresSaver from
  `checkpointing.py` for Lakebase. Every save and resume emits a metadata-only checkpoint event.
* Every run happens inside `no_external_tracing()` (LangSmith forced off) with durability "sync".
* Conversation memory is request-scoped (kept in this process only, never in a checkpoint).
* `recover` continues an investigation stopped by a crash or restart from its last checkpoint (owner checked first).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from .contracts import EvidenceRecord, InvestigationRequest, InvestigationResponse, InvestigationStatus, OwnerRef, stable_id
from .graph import EngineDeps, build_graph
from .safety import no_external_tracing


class InvestigationNotFound(LookupError):
    """No investigation with this id for this owner (the same error for a missing and a foreign one)."""


@dataclass(frozen=True)
class EngineOutcome:
    """What the engine reports after a run: status, checked answer, evidence, task states and the raw state values."""
    thread_id: str
    status: InvestigationStatus
    response: InvestigationResponse | None
    evidence: tuple[EvidenceRecord, ...]
    task_states: dict[str, str]
    values: dict[str, Any]
    unfinished: bool = False          # the workflow has steps left (stopped by a crash or restart)


def checkpoint_thread_id(owner: OwnerRef, conversation_thread_id: str, investigation_id: str) -> str:
    """The stable checkpoint thread id of one investigation (the same inputs always give the same id)."""
    return stable_id("lgt", owner.tenant_id, owner.user_hash, conversation_thread_id, investigation_id)


class InvestigationEngine:
    """Runs investigation graphs: one checkpoint thread per investigation, owner checked on every read."""

    def __init__(self, deps: EngineDeps, checkpointer: Any | None = None):
        """Hook memory and cancellation into `deps`, then compile the graph with the checkpointer (in memory by
        default)."""
        # The graph nodes read memory and cancellation through these hooks, so neither is stored in a checkpoint.
        self._memory: dict[str, str] = {}
        self._cancelled: set[str] = set()
        deps.memory = self._memory.get
        deps.cancel_requested = self._cancelled.__contains__
        self.deps = deps
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = build_graph(deps).compile(checkpointer=self.checkpointer)

    # ------------------------------------------------------------------------------------------------ api
    def start(self, request: InvestigationRequest, memory_block: str | None = None) -> EngineOutcome:
        """Run a new investigation to its end (or until it stops) and return the outcome."""
        thread_id = checkpoint_thread_id(request.owner, request.conversation_thread_id, request.investigation_id)
        if memory_block:
            self._memory[request.investigation_id] = memory_block
        self._invoke({"request": request.to_state()}, thread_id)
        return self.outcome(request.owner, thread_id)

    def cancel(self, owner: OwnerRef, thread_id: str) -> EngineOutcome:
        """Cancel an investigation: a running one stops before its next task.

        The task already running finishes (cooperative cancellation). A finished investigation is left unchanged.
        """
        snapshot = self._owned_snapshot(owner, thread_id)
        if snapshot.next:
            self._cancelled.add(snapshot.values["request"]["investigation_id"])
        return self.outcome(owner, thread_id)

    def request_cancel(self, investigation_id: str) -> None:
        """Flag an investigation to stop before its next task, also before its first checkpoint exists. The caller has
        already checked ownership (the app service owns the job that started it)."""
        self._cancelled.add(investigation_id)

    def recover(self, owner: OwnerRef, thread_id: str) -> EngineOutcome:
        """Continue an investigation stopped by a crash or restart from its last checkpoint.

        Does nothing when the investigation finished.
        """
        snapshot = self._owned_snapshot(owner, thread_id)
        if snapshot.next:
            self._checkpoint_event("orchestrator.checkpoint_resumed", snapshot, thread_id)
            self._invoke(None, thread_id)
        return self.outcome(owner, thread_id)

    def outcome(self, owner: OwnerRef, thread_id: str) -> EngineOutcome:
        """The current outcome from the latest checkpoint; FAILED when no response was written."""
        snapshot = self._owned_snapshot(owner, thread_id)
        values = snapshot.values
        response = InvestigationResponse.from_state(values["response"]) if values.get("response") else None
        status = response.status if response else InvestigationStatus.FAILED
        evidence = tuple(EvidenceRecord.from_state(e) for e in values.get("evidence", []))
        return EngineOutcome(thread_id, status, response, evidence, dict(values.get("task_states", {})), values,
                             unfinished=bool(snapshot.next))

    # ------------------------------------------------------------------------------------------- internals
    def _invoke(self, payload: Any, thread_id: str) -> None:
        """Run the graph on a thread (None payload = resume) with tracing off and synchronous checkpoint writes."""
        config = {"configurable": {"thread_id": thread_id}}
        with no_external_tracing():
            self.graph.invoke(payload, config, durability="sync")
        self._checkpoint_event("orchestrator.checkpoint_saved", self.graph.get_state(config), thread_id)

    def _checkpoint_event(self, name: str, snapshot, thread_id: str) -> None:
        """Emit a metadata-only tracer event about a checkpoint (ids and step, no content)."""
        request = (snapshot.values or {}).get("request") or {}
        self.deps.tracer.event(name, checkpoint_thread=thread_id, checkpointer=type(self.checkpointer).__name__,
                               checkpoint_id=(snapshot.config or {}).get("configurable", {}).get("checkpoint_id"),
                               step=(snapshot.metadata or {}).get("step"), investigation_id=request.get("investigation_id"),
                               conversation_id=request.get("conversation_thread_id"))

    def _owned_snapshot(self, owner: OwnerRef, thread_id: str):
        """The thread's state if it belongs to `owner`; a missing and a foreign thread raise the same error."""
        snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        request = (snapshot.values or {}).get("request")
        if not request or OwnerRef.from_state(request["owner"]) != owner:
            raise InvestigationNotFound("investigation not found")
        return snapshot

