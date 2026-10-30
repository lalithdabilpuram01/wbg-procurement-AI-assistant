"""Application layer behind the web API: ownership-checked operations, background jobs and duplicate protection.

Every operation takes the TRUSTED owner (from authentication, never from the request body) and checks it twice:
the conversation store only returns the owner's threads, and the engine only returns investigations whose
checkpoint belongs to the owner. Another user's conversation or investigation looks exactly like a missing one.

Long work (a message that starts an investigation, a recovery) runs as a background
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

from conversation.learning import Feedback, LearnedItem, Review
from conversation.models import Owner, ThreadInfo, Turn
from conversation.service import ConversationService
from conversation.store import ConversationStore
from conversation.workspace import Bookmark, Folder, InMemoryWorkspaceStore, WorkspaceItemNotFound, WorkspaceStore
from lessons.cards import LessonCard
from lessons.store import LessonNotFound, LessonStore

from .contracts import InvestigationResponse, PackageAssessment, PlanningSpec, TenderSpec, investigation_id_for, new_id
from .conversation_bridge import ConversationFrontDoor, owner_ref
from .engine import EngineOutcome, InvestigationEngine, InvestigationNotFound, checkpoint_thread_id
from .feedback import FeedbackRequest, FeedbackService, Preferences
from .logs import bind, get_logger, log_context
from .observability import current_trace_id
from .progress import JobStreams

log = get_logger("jobs")

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
    kind: Literal["message", "recover", "package", "plan", "revision", "tender"]
    state: JobState = "running"
    request_id: str | None = None             # the investigation's request, once known
    status: str | None = None                 # conversation or investigation status at the end
    error: str | None = None                  # error class only (never a message that could contain data)
    trace_id: str | None = None               # the job's Logfire trace (also on every log line of the job)
    cancel_requested: bool = False            # the owner pressed stop (applied as soon as the investigation id is known)
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


@dataclass(frozen=True)
class InvestigationRef:
    request_id: str
    turn_seq: int
    question: str


def answer_text(response: InvestigationResponse) -> str:
    """The checked answer as paragraphs: the no-evidence opening (when part of the question found nothing), then one
    paragraph per kept claim; the message itself when there are no claims."""
    if not response.claims:
        return response.message
    opening, marker = "", "What the evidence shows:"
    if response.message.startswith("No evidence found") and marker in response.message:
        opening = response.message.split(marker, 1)[0] + marker + "\n\n"
    return opening + "\n\n".join(c.text for c in response.claims)


DELETED_FOLDER = "__deleted__"          # system folder holding deleted conversations (never listed; name reserved)


class AppService:
    def __init__(self, conversations: ConversationService, store: ConversationStore, engine: InvestigationEngine,
                 front_door: ConversationFrontDoor, *, workspace: WorkspaceStore | None = None,
                 learning: FeedbackService | None = None, lessons: LessonStore | None = None, max_workers: int = 2):
        self._conversations, self._store, self._engine, self._front_door = conversations, store, engine, front_door
        self._workspace = workspace or InMemoryWorkspaceStore()
        self.learning = learning
        self.lessons = lessons
        if learning:
            front_door.use_learning(learning)
        self._feedback_by_client: dict[tuple[str, str, str, str], tuple[Feedback, str | None]] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wbg-job")
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._by_client_id: dict[tuple[str, str, str, str], str] = {}
        self._job_spans: dict[str, Callable[..., None]] = {}                   # a running job's root span setter
        self.streams = JobStreams()                                             # live steps and text, per job
        engine.deps.progress = self.streams.publish_investigation

    # ------------------------------------------------------------------------------------------ conversations
    def start_conversation(self, owner: Owner, title: str | None = None) -> ThreadInfo:
        return self._conversations.start_thread(owner, title)

    def list_conversations(self, owner: Owner, limit: int = 100) -> list[tuple[ThreadInfo, str | None]]:
        """Conversations, newest first, each with its folder id (None = not filed); deleted ones are left out."""
        filed, deleted = self._workspace.thread_folders(owner), self._deleted_folder(owner)
        return [(t, filed.get(t.thread_id)) for t in self._store.list_threads(owner, limit)
                if deleted is None or filed.get(t.thread_id) != deleted]

    def delete_conversation(self, owner: Owner, conversation_id: str) -> None:
        """Removes a conversation from the user's lists. Turns are append-only (an audit record), so the conversation
        is filed in a hidden system folder rather than erased."""
        self._store.get_thread(owner, conversation_id)                         # ownership of the conversation
        self._workspace.file_thread(owner, conversation_id, self._deleted_folder(owner, create=True))

    def _deleted_folder(self, owner: Owner, create: bool = False) -> str | None:
        found = next((f.folder_id for f in self._workspace.list_folders(owner) if f.name == DELETED_FOLDER), None)
        if found is None and create:
            found = self._workspace.create_folder(owner, DELETED_FOLDER).folder_id
        return found

    def rename_conversation(self, owner: Owner, conversation_id: str, title: str) -> None:
        self._store.get_thread(owner, conversation_id)
        self._store.set_title(owner, conversation_id, title)

    # ------------------------------------------------------------------------------------------ workspace
    def folders(self, owner: Owner) -> list[Folder]:
        return [f for f in self._workspace.list_folders(owner) if f.name != DELETED_FOLDER]

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
        filed, deleted = self._workspace.thread_folders(owner), self._deleted_folder(owner)
        out = []
        for bookmark in self._workspace.list_bookmarks(owner):
            if deleted is not None and filed.get(bookmark.thread_id) == deleted:
                continue
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

    def submit_package(self, owner: Owner, conversation_id: str, package: PackageAssessment, client_message_id: str) -> Job:
        self._store.get_thread(owner, conversation_id)                         # ownership before anything runs
        return self._submit(owner, conversation_id, "package", client_message_id,
                            lambda job: self._finish_outcome(job, self._front_door.assess_package(
                                owner, conversation_id, package, on_start=lambda rid: self._started(job, rid))))

    def submit_plan(self, owner: Owner, conversation_id: str, spec: PlanningSpec, client_message_id: str) -> Job:
        self._store.get_thread(owner, conversation_id)                         # ownership before anything runs
        return self._submit(owner, conversation_id, "plan", client_message_id,
                            lambda job: self._finish_outcome(job, self._front_door.plan_project(
                                owner, conversation_id, spec, on_start=lambda rid: self._started(job, rid))))

    def submit_tender(self, owner: Owner, conversation_id: str, spec: TenderSpec, client_message_id: str) -> Job:
        self._store.get_thread(owner, conversation_id)                         # ownership before anything runs
        return self._submit(owner, conversation_id, "tender", client_message_id,
                            lambda job: self._finish_outcome(job, self._front_door.check_tender(
                                owner, conversation_id, spec, on_start=lambda rid: self._started(job, rid))))

    # ------------------------------------------------------------------------------------------------ feedback
    def give_feedback(self, owner: Owner, conversation_id: str, request: FeedbackRequest,
                      client_message_id: str) -> tuple[Feedback, Job | None]:
        """Feedback on one answer; a thumbs-down with a correction also starts a revision job (the corrected answer)."""
        learning = self._learning()
        key = (owner.tenant_id, owner.user_id, conversation_id, client_message_id)
        with self._lock:
            repeated = self._feedback_by_client.get(key)
        if repeated:                                                            # a repeat: same feedback, no second run
            return repeated[0], self._jobs.get(repeated[1]) if repeated[1] else None
        if request.correction and self._busy(owner, conversation_id):
            raise ConversationBusy("another request is still running in this conversation")
        outcome = learning.give(owner, conversation_id, request)
        job = None
        if outcome.revise:
            feedback = outcome.feedback
            job = self._submit(owner, conversation_id, "revision", client_message_id,
                               lambda job: self._run_revision(job, feedback))
        with self._lock:
            self._feedback_by_client[key] = (outcome.feedback, job.job_id if job else None)
        return outcome.feedback, job

    def thread_feedback(self, owner: Owner, conversation_id: str) -> dict[int, Feedback]:
        return self.learning.thread_feedback(owner, conversation_id) if self.learning else {}

    def applied_learning(self, owner: Owner, request_id: str) -> list[LearnedItem]:
        return self.learning.applied(owner, request_id) if self.learning else []

    def memory(self, owner: Owner) -> tuple[Preferences, list[tuple[LearnedItem, bool]]]:
        learning = self._learning()
        return learning.preferences(owner), learning.items(owner)

    def set_preferences(self, owner: Owner, update: Preferences) -> Preferences:
        return self._learning().set_preferences(owner, update)

    def revoke_item(self, owner: Owner, item_id: str, reviewer: bool) -> None:
        self._learning().revoke(owner, item_id, reviewer)

    def reviews(self, owner: Owner, status: str | None) -> list[Review]:
        return self._learning().reviews(owner, status)

    def decide_review(self, owner: Owner, review_id: str, approved: bool, note: str | None, expected_terms: list[str]) -> Review:
        return self._learning().decide(owner, review_id, approved, note, expected_terms)

    # ----------------------------------------------------------------------------------------------- lessons
    def lesson_cards(self, status: str | None, limit: int = 500) -> list[LessonCard]:
        return self._lessons().cards(status, limit)

    def lesson_counts(self) -> dict[str, int]:
        return self._lessons().counts()

    def decide_lesson(self, owner: Owner, card_id: str, approved: bool, note: str | None) -> LessonCard:
        return self._lessons().decide(card_id, approved, owner.user_id, note)

    def _lessons(self) -> LessonStore:
        if self.lessons is None:
            raise LessonNotFound("lesson cards are not enabled in this app")
        return self.lessons

    # ------------------------------------------------------------------------------------------ investigations
    def investigations(self, owner: Owner, conversation_id: str) -> list[InvestigationRef]:
        turns = self._store.turns(owner, conversation_id)
        return [InvestigationRef(t.metadata.request_id, t.seq, t.text) for t in turns
                if t.role == "user" and t.metadata.request_id and self._has_checkpoint(owner, conversation_id, t.metadata.request_id)]

    def investigation(self, owner: Owner, conversation_id: str, request_id: str) -> EngineOutcome:
        return self._engine.outcome(owner_ref(owner), self._checkpoint_thread(owner, conversation_id, request_id))

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
            if self._busy(owner, conversation_id):
                raise ConversationBusy("another request is still running in this conversation")
            job = Job(job_id=new_id("job"), owner=owner, conversation_id=conversation_id, kind=kind)
            self._jobs[job.job_id], self._by_client_id[key] = job, job.job_id
            self.streams.open(job.job_id)
        self._pool.submit(self._guarded, job, work)
        return job

    def _busy(self, owner: Owner, conversation_id: str) -> bool:
        return any(j.state == "running" and j.owner == owner and j.conversation_id == conversation_id for j in self._jobs.values())

    def _learning(self) -> FeedbackService:
        if self.learning is None:
            raise RuntimeError("feedback is not enabled in this app")
        return self.learning

    def _run_revision(self, job: Job, feedback: Feedback) -> None:
        outcome = self._front_door.revise(job.owner, job.conversation_id, feedback.request_id, feedback.correction,
                                          on_start=lambda rid: self._started(job, rid))
        request_id = outcome.values["request"]["request_id"]
        self._learning().revised(job.owner, feedback.feedback_id, request_id)
        job.request_id, job.status = request_id, outcome.status.value

    def _guarded(self, job: Job, work: Callable[[Job], None]) -> None:
        """Run one job in a worker thread. The job is the root of its trace: every span below it (routing, graph
        steps, tool and model calls) nests under `job.<kind>`, and every log line carries the job's ids."""
        tracer = self._engine.deps.tracer
        with log_context(job_id=job.job_id, job_kind=job.kind, conversation_id=job.conversation_id), \
                tracer.span(f"job.{job.kind}", job_id=job.job_id, job_kind=job.kind, conversation_id=job.conversation_id,
                            owner_hash=owner_ref(job.owner).user_hash) as span:
            job.trace_id = current_trace_id()
            self._job_spans[job.job_id] = span
            log.info("job started")
            try:
                work(job)
                span(status=job.status, request_id=job.request_id)
                log.info("job finished", extra={"status": job.status})
                job.state = "succeeded"                 # last: pollers see the end only after it is logged
            except Exception as error:                 # the user sees the class only; the log has the full record
                span(error=error, log_message="job failed", status="failed")
                job.error = type(error).__name__
                job.state = "failed"
            finally:
                self._job_spans.pop(job.job_id, None)
                self.streams.close(job.job_id, {"state": job.state, "status": job.status})

    def _run_message(self, job: Job, text: str, execute: bool, rag_release: str | None = None) -> None:
        reply = self._front_door.handle_message(job.owner, job.conversation_id, text, execute=execute,
                                                on_start=lambda rid: self._started(job, rid), rag_release=rag_release,
                                                on_text=lambda piece: self.streams.publish(job.job_id, {"type": "text", "text": piece}))
        job.request_id, job.status = reply.request_id, reply.status
        if reply.investigation is not None:
            self._publish_answer(job, reply.investigation)

    def _publish_answer(self, job: Job, outcome: EngineOutcome) -> None:
        """The investigation's answer, once checked against its evidence, laid out as the answer card shows it."""
        if outcome.response is not None:
            self.streams.publish(job.job_id, {"type": "answer", "text": answer_text(outcome.response)})

    def _started(self, job: Job, request_id: str) -> None:
        job.request_id = request_id
        bind(request_id=request_id, investigation_id=investigation_id_for(request_id))
        if span := self._job_spans.get(job.job_id):
            span(request_id=request_id, investigation_id=investigation_id_for(request_id))
        self.streams.bind(investigation_id_for(request_id), job.job_id)
        if job.cancel_requested:                                                # stop was pressed before the id existed
            self._engine.request_cancel(investigation_id_for(request_id))

    def _finish_outcome(self, job: Job, outcome: EngineOutcome) -> None:
        job.request_id, job.status = outcome.values["request"]["request_id"], outcome.status.value
        self._publish_answer(job, outcome)

    def _finish(self, job: Job, request_id: str, outcome: EngineOutcome) -> None:
        job.request_id, job.status = request_id, outcome.status.value
        self._publish_answer(job, outcome)

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
    return {
        "status": outcome.status.value,
        "message": response.message if response else "",
        "claims": [{"text": c.text, "evidence_ids": list(c.evidence_ids), "section": c.section} for c in response.claims]
        if response else [],
        "limitations": list(response.limitations) if response else [],
        "error_codes": sorted({e["code"] for e in values.get("errors", [])} | set(response.error_codes if response else ())),
        "tasks": [{"task": t["local_id"], "worker": t["worker"], "operation": t["operation"], "question": t["question"],
                   "state": outcome.task_states.get(t["task_id"], "pending")} for t in plan.get("tasks", [])],
        "evidence": [{"evidence_id": e.evidence_id, "kind": e.kind.value, "citation": e.citation, "numeric_use": e.numeric_use.value,
                      "validation_status": e.validation_status.value, "warnings": list(e.quality_warnings),
                      "summary": e.summary, "document_id": e.document_id, "pages": list(e.pages) if e.pages else None,
                      "project_ids": list(e.project_ids), "source_url": e.provenance_value("source_url"),
                      "facts": [{"name": f.name, "value": f.value, "unit": f.unit} for f in e.facts]} for e in outcome.evidence],
        "usage": {k: values.get("usage", {}).get(k) for k in ("llm_calls", "input_tokens", "output_tokens", "cost_usd", "executions")},
    }
