"""Conversation memory, compression and checkpoint references for investigations (interfaces over existing stores).

* `InvestigationDigest` compresses an investigation's state into what later turns must not lose: user constraints
  (active decisions), pending approvals, project identifiers, evidence references and unresolved tasks.
  `digest_losses` proves nothing required was dropped; rendering never truncates mandatory items silently.
* `CheckpointRef` links a conversation turn to the investigation's checkpoint thread (no state is copied).
* `ConversationMemoryView` reads recent messages, the summary, decisions and relevant memory through the existing
  owner-scoped ConversationStore; `OwnerScopedMemoryRetriever` implements the existing LongTermMemoryRetriever
  contract and never crosses owners (every store call is made with the caller's owner).
No PostgreSQL or Lakebase table is created; the existing store implementations are used unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

from conversation.contracts import MemoryHit
from conversation.models import Decision, Owner, SummaryRecord, Turn
from conversation.store import ConversationStore
from conversation.tokens import ApproxTokenCounter, ContextBudget

from .contracts import TERMINAL_TASK_STATES, Record, TaskState

PROJECT_ID = re.compile(r"\bP\d{6}\b")


# Links a conversation turn to an investigation's checkpoint thread (references only, no state copied).
class CheckpointRef(Record):
    tenant_id: str
    user_hash: str
    conversation_thread_id: str
    investigation_id: str
    checkpoint_thread_id: str


# The compressed memory of one investigation that later turns must keep (ids and verbatim user constraints).
class InvestigationDigest(Record):
    investigation_id: str
    checkpoint_thread_id: str
    status: str
    constraint_ids: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()              # active user decisions / constraints, verbatim
    pending_approval_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    unresolved_task_ids: tuple[str, ...] = ()
    open_error_codes: tuple[str, ...] = ()


def build_digest(values: Mapping[str, Any], checkpoint_thread_id: str, decisions: list[Decision],
                 pending_approval_ids: tuple[str, ...] = ()) -> InvestigationDigest:
    """Compress an investigation's state into its digest: active constraints, projects, evidence and open tasks."""
    active = [d for d in decisions if d.active]
    states = values.get("task_states", {})
    evidence = values.get("evidence", [])
    projects = {p for e in evidence for p in e.get("project_ids", [])} | set(PROJECT_ID.findall(values["request"]["question"]))
    # Unresolved = still running, or ended without a usable result (failed, cancelled, blocked).
    unresolved = tuple(t for t, s in states.items() if TaskState(s) not in TERMINAL_TASK_STATES
                       or TaskState(s) in (TaskState.FAILED, TaskState.CANCELLED, TaskState.BLOCKED))
    response = values.get("response") or {}
    return InvestigationDigest(
        investigation_id=values["request"]["investigation_id"], checkpoint_thread_id=checkpoint_thread_id,
        status=response.get("status", "awaiting_approval" if pending_approval_ids else "in_progress"),
        constraint_ids=tuple(d.decision_id for d in active), constraints=tuple(d.statement for d in active),
        pending_approval_ids=pending_approval_ids, project_ids=tuple(sorted(projects)),
        evidence_ids=tuple(e["evidence_id"] for e in evidence), unresolved_task_ids=unresolved,
        open_error_codes=tuple(sorted({e["code"] for e in values.get("errors", []) if e.get("task_id") in unresolved})))


def digest_losses(digest: InvestigationDigest, values: Mapping[str, Any], decisions: list[Decision],
                  pending_approval_ids: tuple[str, ...] = ()) -> list[str]:
    """Required items missing from a digest (empty = lossless for what later turns depend on)."""
    expected = build_digest(values, digest.checkpoint_thread_id, decisions, pending_approval_ids)
    missing = []
    for field in ("constraint_ids", "pending_approval_ids", "project_ids", "evidence_ids", "unresolved_task_ids"):
        lost = set(getattr(expected, field)) - set(getattr(digest, field))
        missing += [f"{field}:{item}" for item in sorted(lost)]
    return missing


# A digest rendered as prompt text, with its token count and whether it exceeds the budget.
class RenderedDigest(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    tokens: int
    over_budget: bool                     # mandatory items do not fit: nothing was dropped, the caller must decide


def render_digest(digest: InvestigationDigest, budget_tokens: int, counter: ApproxTokenCounter | None = None) -> RenderedDigest:
    """Render the digest as a memory block for a prompt; it is never cut, only flagged when over budget."""
    counter = counter or ApproxTokenCounter()
    lines = ["BEGIN INVESTIGATION MEMORY (references, not evidence text)",
             f"investigation {digest.investigation_id} status {digest.status} checkpoint {digest.checkpoint_thread_id}"]
    sections = (("USER CONSTRAINTS (verbatim)", [f"[{i}] {s}" for i, s in zip(digest.constraint_ids, digest.constraints)]),
                ("PENDING APPROVALS", list(digest.pending_approval_ids)), ("UNRESOLVED TASKS", list(digest.unresolved_task_ids)),
                ("PROJECTS", list(digest.project_ids)), ("EVIDENCE REFERENCES", list(digest.evidence_ids)))
    for title, items in sections:
        if items:
            lines.append(f"{title}: " + "; ".join(items))
    lines.append("END INVESTIGATION MEMORY")
    text = "\n".join(lines)
    tokens = counter.count(text)
    return RenderedDigest(text=text, tokens=tokens, over_budget=tokens > budget_tokens)


# --------------------------------------------------------------------------------------------- conversation view
class ConversationMemoryView:
    """Owner-scoped reads for the planner's context (through the existing store; ownership is checked there)."""

    def __init__(self, store: ConversationStore, budget: ContextBudget | None = None):
        self._store = store
        self.budget = budget or ContextBudget()

    def recent_messages(self, owner: Owner, thread_id: str, limit: int = 6) -> list[Turn]:
        """The last `limit` turns of the current topic, leaving out turns the guardrails blocked."""
        thread = self._store.get_thread(owner, thread_id)
        turns = self._store.turns(owner, thread_id, topic_epoch=thread.topic_epoch)
        return [t for t in turns if not t.metadata.blocked][-limit:]

    def summary(self, owner: Owner, thread_id: str) -> SummaryRecord | None:
        summary = self._store.latest_summary(owner, thread_id)
        return summary if summary and summary.invalidated_at is None else None

    def decisions(self, owner: Owner, thread_id: str) -> list[Decision]:
        return self._store.decisions(owner, thread_id)

    def relevant_memory(self, owner: Owner, query: str, limit: int = 5) -> list[MemoryHit]:
        return OwnerScopedMemoryRetriever(self._store).retrieve(owner, query, limit)


class OwnerScopedMemoryRetriever:
    """LongTermMemoryRetriever over the owner's own threads: filter by owner first, then rank by word overlap."""

    def __init__(self, store: ConversationStore):
        self._store = store

    def retrieve(self, owner: Owner, query: str, limit: int = 5) -> list[MemoryHit]:
        """The best-matching summaries and user turns from the owner's 50 most recent threads (score above zero)."""
        words = _words(query)
        hits = []
        for thread in self._store.list_threads(owner, limit=50):
            summary = self._store.latest_summary(owner, thread.thread_id)
            if summary and summary.invalidated_at is None:
                text = summary.content.narrative
                hits.append(MemoryHit(thread_id=thread.thread_id, text=text, score=_overlap(words, text), source="summary"))
            for turn in self._store.turns(owner, thread.thread_id, topic_epoch=thread.topic_epoch):
                if turn.role == "user" and not turn.metadata.blocked:
                    hits.append(MemoryHit(thread_id=thread.thread_id, turn_seq=turn.seq, text=turn.text,
                                          score=_overlap(words, turn.text), source="turn"))
        return sorted((h for h in hits if h.score > 0), key=lambda h: -h.score)[:limit]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _overlap(words: set[str], text: str) -> float:
    other = _words(text)
    return round(len(words & other) / max(1, len(words)), 3)
