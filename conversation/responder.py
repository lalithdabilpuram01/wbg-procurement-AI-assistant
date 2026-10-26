"""Conversation response path: replies that need no external evidence, and recall of the user's own conversation.

`ConversationResponder` is the interface. A production implementation may generate natural replies with an approved
model (no retrieval, no tools); it must declare `uses_live_model = True`, and the service then refuses to start
without a `LiveAccess` grant from the existing dual opt-in (live_guard). No such model responder exists yet.

`OfflineResponder` is the deterministic placeholder used offline. Its short acknowledgements are NOT the intended
conversational capability; for general explanations it says plainly that the conversational model is not enabled.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from conversation.compression import usable
from conversation.models import Decision, EvidenceReference, Owner, StructuredSummary, ThreadInfo, TurnKind
from conversation.routing.routes import ReasonCategory, Route
from conversation.store import ConversationStore


class RecalledQuestion(BaseModel):
    turn_seq: int
    question: str
    route: str | None = None


class RecalledReference(BaseModel):
    turn_seq: int
    reference: EvidenceReference


class MemoryRecall(BaseModel):
    """The owner's own conversation records for the current topic. Recall is memory, not re-verified evidence."""

    questions: list[RecalledQuestion] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    references: list[RecalledReference] = Field(default_factory=list)
    summary: StructuredSummary | None = None          # generated; valid summaries only
    summary_version: int | None = None


class ConversationRequest(BaseModel):
    message: str                                      # the redacted current message
    route: Route
    reason: ReasonCategory
    memory_context: str = ""                          # labelled, sanitised memory block (untrusted, not evidence)
    recall: MemoryRecall | None = None                # only for conversation_memory


class ConversationResponse(BaseModel):
    text: str
    generated_by: str
    memory_sources: list[str] = Field(default_factory=list)      # e.g. "turn:3", "decision:<id>"
    evidence_status: Literal["none", "conversation_memory_not_reverified"] = "none"


class ConversationResponder(Protocol):
    name: str
    uses_live_model: bool

    def respond(self, request: ConversationRequest) -> ConversationResponse: ...


def recall_memory(store: ConversationStore, owner: Owner, thread: ThreadInfo) -> MemoryRecall:
    """Read only the owner's stored conversation (ownership is checked by the store); no retrieval or tools."""
    turns = usable(store.turns(owner, thread.thread_id, topic_epoch=thread.topic_epoch))
    questions = [RecalledQuestion(turn_seq=t.seq, question=t.metadata.standalone_question or t.text, route=t.metadata.route)
                 for t in turns if t.role == "user" and t.kind not in (TurnKind.CLARIFICATION_REQUIRED, TurnKind.CONVERSATION)]
    references = [RecalledReference(turn_seq=t.seq, reference=e) for t in turns for e in t.metadata.evidence]
    summary = store.latest_summary(owner, thread.thread_id)
    usable_summary = summary if summary and summary.valid and summary.topic_epoch == thread.topic_epoch else None
    return MemoryRecall(questions=questions, decisions=store.decisions(owner, thread.thread_id), references=references,
                        summary=usable_summary.content if usable_summary else None,
                        summary_version=usable_summary.version if usable_summary else None)


class OfflineResponder:
    """Deterministic offline placeholder (no model). See the module docstring."""

    name = "offline-deterministic"
    uses_live_model = False

    REPLIES = {
        ReasonCategory.GREETING: "Hello! I can help with procurement data, procurement documents, predictions and "
                                 "procurement planning. What would you like to look at?",
        ReasonCategory.GRATITUDE: "You're welcome. Let me know if you need anything else.",
        ReasonCategory.FAREWELL: "Goodbye.",
        ReasonCategory.SMALL_TALK: "I'm the procurement intelligence assistant. I can answer questions about procurement "
                                   "data, documents, predictions and planning.",
        ReasonCategory.TOPIC_CHANGE: "Okay, new topic. Earlier filters will not be reused.",
    }
    MODEL_NOT_ENABLED = ("General explanations need the conversational model, which is not enabled in this offline "
                         "build. You can ask a specific procurement question instead.")

    def respond(self, request: ConversationRequest) -> ConversationResponse:
        if request.route == Route.CONVERSATION_MEMORY:
            return self._recap(request.recall or MemoryRecall())
        return ConversationResponse(text=self.REPLIES.get(request.reason, self.MODEL_NOT_ENABLED), generated_by=self.name)

    def _recap(self, recall: MemoryRecall) -> ConversationResponse:
        lines = ["From this conversation (conversation memory, not re-verified evidence):"]
        sources: list[str] = []
        if recall.questions:
            lines.append("Questions asked: " + "; ".join(f"{q.question} (turn {q.turn_seq})" for q in recall.questions))
            sources += [f"turn:{q.turn_seq}" for q in recall.questions]
        if recall.decisions:
            lines.append("Approved decisions and constraints: " + "; ".join(d.statement for d in recall.decisions))
            sources += [f"decision:{d.decision_id}" for d in recall.decisions]
        if recall.references:
            lines.append("Verified result references: " + "; ".join(
                f"{r.reference.source_id} (turn {r.turn_seq})" for r in recall.references))
        if recall.summary is not None:
            lines.append(f"Earlier turns (generated summary v{recall.summary_version}): {recall.summary.narrative}")
        if len(lines) == 1:
            lines.append("Nothing has been discussed in this topic yet.")
        return ConversationResponse(text="\n".join(lines), generated_by=self.name, memory_sources=sources,
                                    evidence_status="conversation_memory_not_reverified")
