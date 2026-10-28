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
from conversation.workspace import Bookmark, Folder, InMemoryWorkspaceStore, WorkspaceItemNotFound, WorkspaceStore

from .contracts import PackageSpec, PlanningSpec, investigation_id_for, new_id
from .conversation_bridge import ConversationFrontDoor, owner_ref
from .engine import EngineOutcome, InvestigationEngine, InvestigationNotFound, checkpoint_thread_id

JobState = Literal["running", "succeeded", "failed"]
TABLELENS_RELEASE = "pilot_v3_tablelens"           # the document pipeline behind the TableLens switch


class ConversationBusy(RuntimeError):
    """Another job is still running in this conversation."""


class JobNotFound(LookupError):
    """No job with this id for this owner."""


@dataclass
class Job:
    job_id: str
    owner: Owner
    conversation_id: str
    kind: Literal["message", "approval", "recover", "package", "plan"]
    state: JobState = "running"
    request_id: str | None = None             # the investigation's request, once known
    status: str | None = None                 # conversation or investigation status at the end
    error: str | None = None                  # error class only (never a message that could contain data)
    cancel_requested: bool = False            # the owner pressed stop (applied as soon as the investigation id is known)
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


@dataclass(frozen=True)
class InvestigationRef:
    request_id: str
    turn_seq: int
    question: str


class AppService:
    def __init__(self, conversations: ConversationService, store: ConversationStore, engine: InvestigationEngine,
                 front_door: ConversationFrontDoor, *, workspace: WorkspaceStore | None = None, max_workers: int = 2):
        self._conversations, self._store, self._engine, self._front_door = conversations, store, engine, front_door
        self._workspace = workspace or InMemoryWorkspaceStore()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wbg-job")
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._by_client_id: dict[tuple[str, str, str, str], str] = {}

    # ------------------------------------------------------------------------------------------ conversations
    def start_conversation(self, owner: Owner, title: str | None = None) -> ThreadInfo:
        return self._conversations.start_thread(owner, title)

    def list_conversations(self, owner: Owner, limit: int = 100) -> list[tuple[ThreadInfo, str | None]]:
        """Conversations, newest first, each with its folder id (None = not filed)."""
        filed = self._workspace.thread_folders(owner)
        return [(t, filed.get(t.thread_id)) for t in self._store.list_threads(owner, limit)]

    def rename_conversation(self, owner: Owner, conversation_id: str, title: str) -> None:
        self._store.get_thread(owner, conversation_id)
        self._store.set_title(owner, conversation_id, title)

    # ------------------------------------------------------------------------------------------ workspace
    def folders(self, owner: Owner) -> list[Folder]:
        return self._workspace.list_folders(owner)

    def create_folder(self, owner: Owner, name: str) -> Folder:
        return self._workspace.create_folder(owner, name)

    def rename_folder(self, owner: Owner, folder_id: str, name: str) -> Folder:
        return self._workspace.rename_folder(owner, folder_id, name)

    def delete_folder(self, owner: Owner, folder_id: str) -> None:
        self._workspace.delete_folder(owner, folder_id)

    def file_conversation(self, owner: Owner, conversation_id: str, folder_id: str | None) -> None:
        self._store.get_thread(owner, conversation_id)                         # ownership of the conversation
        self._workspace.file_thread(owner, conversation_id, folder_id)

    def bookmarks(self, owner: Owner) -> list[tuple[Bookmark, str, str]]:
        """Bookmarks with their conversation title and a short excerpt of the bookmarked answer."""
        titles = {t.thread_id: t.title for t in self._store.list_threads(owner, 500)}
        out = []
        for bookmark in self._workspace.list_bookmarks(owner):
            turn = next((t for t in self._store.turns(owner, bookmark.thread_id) if t.seq == bookmark.turn_seq), None)
            out.append((bookmark, titles.get(bookmark.thread_id, ""), (turn.text if turn else "")[:240]))
        return out

    def add_bookmark(self, owner: Owner, conversation_id: str, turn_seq: int, note: str) -> Bookmark:
        turns = self._store.turns(owner, conversation_id)                       # ownership of the conversation
        if not any(t.seq == turn_seq and t.role == "assistant" for t in turns):
            raise WorkspaceItemNotFound("only answers can be bookmarked")
        return self._workspace.add_bookmark(owner, conversation_id, turn_seq, note)

    def delete_bookmark(self, owner: Owner, bookmark_id: str) -> None:
        self._workspace.delete_bookmark(owner, bookmark_id)

    def history(self, owner: Owner, conversation_id: str) -> list[Turn]:
        return self._conversations.history(owner, conversation_id)

    def submit_message(self, owner: Owner, conversation_id: str, text: str, execute: bool, client_message_id: str,
                       table_lens: bool = False) -> Job:
        """table_lens: answer PDF tables of the 10 pilot documents with TableLens (SQL) instead of table slices."""
        self._store.get_thread(owner, conversation_id)                         # ownership before anything runs
        release = TABLELENS_RELEASE if table_lens else None
        return self._submit(owner, conversation_id, "message", client_message_id,
                            lambda job: self._run_message(job, text, execute, release))

    def submit_package(self, owner: Owner, conversation_id: str, package: PackageSpec, client_message_id: str) -> Job:
        self._store.get_thread(owner, conversation_id)                         # ownership before anything runs
        return self._submit(owner, conversation_id, "package", client_message_id,
                            lambda job: self._finish_outcome(job, self._front_door.assess_package(
                                owner, conversation_id, package, on_start=lambda rid: self._started(job, rid))))

    def submit_plan(self, owner: Owner, conversation_id: str, spec: PlanningSpec, client_message_id: str) -> Job:
        self._store.get_thread(owner, conversation_id)                         # ownership before anything runs
        return self._submit(owner, conversation_id, "plan", client_message_id,
                            lambda job: self._finish_outcome(job, self._front_door.plan_project(
                                owner, conversation_id, spec, on_start=lambda rid: self._started(job, rid))))

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

    def cancel_job(self, owner: Owner, job_id: str) -> Job:
        """Stop a running job: its investigation stops before its next step (a step already running finishes)."""
        job = self.job(owner, job_id)                                           # ownership of the job
        if job.state == "running":
            job.cancel_requested = True
            if job.request_id:
                self._engine.request_cancel(investigation_id_for(job.request_id))
        return job

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

    def _run_message(self, job: Job, text: str, execute: bool, rag_release: str | None = None) -> None:
        reply = self._front_door.handle_message(job.owner, job.conversation_id, text, execute=execute,
                                                on_start=lambda rid: self._started(job, rid), rag_release=rag_release)
        job.request_id, job.status = reply.request_id, reply.status

    def _started(self, job: Job, request_id: str) -> None:
        job.request_id = request_id
        if job.cancel_requested:                                                # stop was pressed before the id existed
            self._engine.request_cancel(investigation_id_for(request_id))

    @staticmethod
    def _finish_outcome(job: Job, outcome: EngineOutcome) -> None:
        job.request_id, job.status = outcome.values["request"]["request_id"], outcome.status.value

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
        "claims": [{"text": c.text, "evidence_ids": list(c.evidence_ids), "section": c.section} for c in response.claims]
        if response else [],
        "limitations": list(response.limitations) if response else [],
        "error_codes": sorted({e["code"] for e in values.get("errors", [])} | set(response.error_codes if response else ())),
        "pending_approval": None if approval is None else {
            "approval_id": approval.approval_id, "kind": approval.kind, "summary": approval.summary,
            "expires_at": approval.expires_at.isoformat()},
        "tasks": [{"task": t["local_id"], "worker": t["worker"], "operation": t["operation"], "question": t["question"],
                   "state": outcome.task_states.get(t["task_id"], "pending")} for t in plan.get("tasks", [])],
        "evidence": [{"evidence_id": e.evidence_id, "kind": e.kind.value, "citation": e.citation, "numeric_use": e.numeric_use.value,
                      "validation_status": e.validation_status.value, "warnings": list(e.quality_warnings),
                      "summary": e.summary, "document_id": e.document_id, "pages": list(e.pages) if e.pages else None,
                      "project_ids": list(e.project_ids), "source_url": e.provenance_value("source_url"),
                      "facts": [{"name": f.name, "value": f.value, "unit": f.unit} for f in e.facts]} for e in outcome.evidence],
        "usage": {k: values.get("usage", {}).get(k) for k in ("llm_calls", "input_tokens", "output_tokens", "cost_usd", "executions")},
    }
