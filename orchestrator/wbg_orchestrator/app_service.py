"""Application layer behind the web API: ownership-checked operations, background jobs and duplicate protection.

Every operation takes the TRUSTED owner (from authentication, never from the request body) and checks it twice:
the conversation store only returns the owner's threads, and the engine only returns investigations whose
checkpoint belongs to the owner. Another user's conversation or investigation looks exactly like a missing one.

Long work (a message that starts an investigation, an approval that resumes one, a recovery) runs as a background
job; the caller polls the job and then reads the investigation. A conversation runs one job at a time, and a
repeated `client_message_id` returns the existing job instead of running the work again. Jobs are kept in memory:
after a restart, the durable state is the conversation store plus the checkpoints, and an investigation that was
stopped mid-run is reported as `interrupted` and can be continued with `recover`.

Investigations are found from the conversation itself: each user turn stores its request id, and the investigation
id is derived from it (`contracts.investigation_id_for`), so no extra index is needed.
"""
from __future__ import annotations

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from conversation.models import Owner, ThreadInfo, Turn
from conversation.service import ConversationService
from conversation.store import ConversationStore

from .contracts import investigation_id_for, new_id
from .conversation_bridge import ConversationFrontDoor, owner_ref
from .engine import EngineOutcome, InvestigationEngine, InvestigationNotFound, checkpoint_thread_id

JobState = Literal["running", "succeeded", "failed"]


class ConversationBusy(RuntimeError):
    """Another job is still running in this conversation."""


class JobNotFound(LookupError):
    """No job with this id for this owner."""


@dataclass
class Job:
    job_id: str
    owner: Owner
    conversation_id: str
    kind: Literal["message", "approval", "recover"]
    state: JobState = "running"
    request_id: str | None = None             # the investigation's request, once known
    status: str | None = None                 # conversation or investigation status at the end
    error: str | None = None                  # error class only (never a message that could contain data)
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


@dataclass(frozen=True)
class InvestigationRef:
    request_id: str
    turn_seq: int
    question: str


class AppService:
    def __init__(self, conversations: ConversationService, store: ConversationStore, engine: InvestigationEngine,
                 front_door: ConversationFrontDoor, *, max_workers: int = 2):
        self._conversations, self._store, self._engine, self._front_door = conversations, store, engine, front_door
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wbg-job")
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._by_client_id: dict[tuple[str, str, str, str], str] = {}

    # ------------------------------------------------------------------------------------------ conversations
    def start_conversation(self, owner: Owner, title: str | None = None) -> ThreadInfo:
        return self._conversations.start_thread(owner, title)

    def list_conversations(self, owner: Owner) -> list[ThreadInfo]:
        return self._store.list_threads(owner)

    def history(self, owner: Owner, conversation_id: str) -> list[Turn]:
        return self._conversations.history(owner, conversation_id)

    def submit_message(self, owner: Owner, conversation_id: str, text: str, execute: bool, client_message_id: str) -> Job:
        self._store.get_thread(owner, conversation_id)                         # ownership before anything runs
        return self._submit(owner, conversation_id, "message", client_message_id,
                            lambda job: self._run_message(job, text, execute))

    # ------------------------------------------------------------------------------------------ investigations
    def investigations(self, owner: Owner, conversation_id: str) -> list[InvestigationRef]:
        turns = self._store.turns(owner, conversation_id)
        return [InvestigationRef(t.metadata.request_id, t.seq, t.text) for t in turns
                if t.role == "user" and t.metadata.request_id and self._has_checkpoint(owner, conversation_id, t.metadata.request_id)]

    def investigation(self, owner: Owner, conversation_id: str, request_id: str) -> EngineOutcome:
        return self._engine.outcome(owner_ref(owner), self._checkpoint_thread(owner, conversation_id, request_id))

    def decide(self, owner: Owner, conversation_id: str, request_id: str, approval_id: str, approved: bool,
               reason: str | None, client_message_id: str) -> Job:
        thread = self._checkpoint_thread(owner, conversation_id, request_id)
        return self._submit(owner, conversation_id, "approval", client_message_id, lambda job: self._finish(
            job, request_id, self._front_door.approve(owner, conversation_id, thread, approval_id, approved, reason)))

    def recover(self, owner: Owner, conversation_id: str, request_id: str, client_message_id: str) -> Job:
        thread = self._checkpoint_thread(owner, conversation_id, request_id)
        return self._submit(owner, conversation_id, "recover", client_message_id,
                            lambda job: self._finish(job, request_id, self._engine.recover(owner_ref(owner), thread)))

    def cancel(self, owner: Owner, conversation_id: str, request_id: str) -> EngineOutcome:
        return self._engine.cancel(owner_ref(owner), self._checkpoint_thread(owner, conversation_id, request_id))

    def job(self, owner: Owner, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None or job.owner != owner:
            raise JobNotFound("job not found")
        return job

    def is_running(self, owner: Owner, conversation_id: str, request_id: str) -> bool:
        return any(j.state == "running" and j.owner == owner and j.conversation_id == conversation_id
                   and j.request_id in (None, request_id) for j in self._jobs.values())

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)

    # ------------------------------------------------------------------------------------------------ internals
    def _submit(self, owner: Owner, conversation_id: str, kind: str, client_message_id: str,
                work: Callable[[Job], None]) -> Job:
        key = (owner.tenant_id, owner.user_id, conversation_id, client_message_id)
        with self._lock:
            existing = self._by_client_id.get(key)
            if existing:
                return self._jobs[existing]                                     # a repeat: the same job, not a new run
            if any(j.state == "running" and j.owner == owner and j.conversation_id == conversation_id for j in self._jobs.values()):
                raise ConversationBusy("another request is still running in this conversation")
            job = Job(job_id=new_id("job"), owner=owner, conversation_id=conversation_id, kind=kind)
            self._jobs[job.job_id], self._by_client_id[key] = job, job.job_id
        self._pool.submit(self._guarded, job, work)
        return job

    @staticmethod
    def _guarded(job: Job, work: Callable[[Job], None]) -> None:
        try:
            work(job)
            job.state = "succeeded"
        except Exception as error:                     # the class only: messages may contain data
            job.state, job.error = "failed", type(error).__name__

    def _run_message(self, job: Job, text: str, execute: bool) -> None:
        reply = self._front_door.handle_message(job.owner, job.conversation_id, text, execute=execute)
        user_turn = next((t for t in self._store.turns(job.owner, job.conversation_id) if t.seq == reply.conversation.user_turn_seq), None)
        job.request_id = user_turn.metadata.request_id if user_turn else None
        if reply.investigation is not None:
            job.request_id = reply.investigation.values["request"]["request_id"]
        job.status = reply.status

    @staticmethod
    def _finish(job: Job, request_id: str, outcome: EngineOutcome) -> None:
        job.request_id, job.status = request_id, outcome.status.value

    def _checkpoint_thread(self, owner: Owner, conversation_id: str, request_id: str) -> str:
        """The checkpoint thread of an investigation that belongs to this owner's conversation (else not found)."""
        if not any(t.metadata.request_id == request_id for t in self._store.turns(owner, conversation_id)):
            raise InvestigationNotFound("investigation not found")
        return checkpoint_thread_id(owner_ref(owner), conversation_id, investigation_id_for(request_id))

    def _has_checkpoint(self, owner: Owner, conversation_id: str, request_id: str) -> bool:
        try:
            self._engine.outcome(owner_ref(owner), self._checkpoint_thread(owner, conversation_id, request_id))
            return True
        except InvestigationNotFound:
            return False


def summarize(outcome: EngineOutcome) -> dict[str, Any]:
    """A JSON-safe view of one investigation for its owner (no SQL, no rows beyond the bounded evidence facts)."""
    values, response = outcome.values, outcome.response
    plan = values.get("plan") or {}
    approval = outcome.pending_approval
    return {
        "status": outcome.status.value,
        "message": response.message if response else "",
        "claims": [{"text": c.text, "evidence_ids": list(c.evidence_ids)} for c in response.claims] if response else [],
        "limitations": list(response.limitations) if response else [],
        "error_codes": sorted({e["code"] for e in values.get("errors", [])} | set(response.error_codes if response else ())),
        "pending_approval": None if approval is None else {
            "approval_id": approval.approval_id, "kind": approval.kind, "summary": approval.summary,
            "expires_at": approval.expires_at.isoformat()},
        "tasks": [{"task": t["local_id"], "worker": t["worker"], "operation": t["operation"], "question": t["question"],
                   "state": outcome.task_states.get(t["task_id"], "pending")} for t in plan.get("tasks", [])],
        "evidence": [{"evidence_id": e.evidence_id, "kind": e.kind.value, "citation": e.citation, "numeric_use": e.numeric_use.value,
                      "validation_status": e.validation_status.value, "warnings": list(e.quality_warnings),
                      "facts": [{"name": f.name, "value": f.value} for f in e.facts]} for e in outcome.evidence],
        "usage": {k: values.get("usage", {}).get(k) for k in ("llm_calls", "input_tokens", "output_tokens", "cost_usd", "executions")},
    }
