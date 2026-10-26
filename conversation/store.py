"""Conversation persistence contract and an in-memory implementation (tests, local runs).

Every read and write takes the trusted `Owner`; a thread is visible only to the tenant and user that created it.
`ThreadNotFound` is raised both for a missing thread and for another owner's thread, so callers cannot probe ids.
Turns are append-only. Summaries are versioned and only ever invalidated, never edited or deleted.
"""
from __future__ import annotations

import threading
from typing import Protocol

from conversation.models import Decision, Owner, SummaryRecord, ThreadInfo, Turn, new_id, utc_now


class ThreadNotFound(LookupError):
    """No such thread for this owner (deliberately does not say whether it exists for someone else)."""


class ConversationStore(Protocol):
    def create_thread(self, owner: Owner, title: str) -> ThreadInfo: ...
    def get_thread(self, owner: Owner, thread_id: str) -> ThreadInfo: ...
    def list_threads(self, owner: Owner, limit: int = 20) -> list[ThreadInfo]: ...
    def start_new_topic(self, owner: Owner, thread_id: str) -> ThreadInfo: ...
    def set_title(self, owner: Owner, thread_id: str, title: str) -> None: ...
    def append_turn(self, owner: Owner, turn: Turn) -> Turn: ...
    def turns(self, owner: Owner, thread_id: str, topic_epoch: int | None = None, after_seq: int = 0,
              limit: int | None = None) -> list[Turn]: ...
    def next_seq(self, owner: Owner, thread_id: str) -> int: ...
    def add_decision(self, owner: Owner, decision: Decision) -> Decision: ...
    def revoke_decision(self, owner: Owner, thread_id: str, decision_id: str, reason: str) -> Decision: ...
    def decisions(self, owner: Owner, thread_id: str, include_revoked: bool = False) -> list[Decision]: ...
    def add_summary(self, owner: Owner, summary: SummaryRecord) -> SummaryRecord: ...
    def latest_summary(self, owner: Owner, thread_id: str) -> SummaryRecord | None: ...
    def invalidate_summary(self, owner: Owner, thread_id: str, summary_id: str, reason: str) -> None: ...


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: dict[str, ThreadInfo] = {}
        self._turns: dict[str, list[Turn]] = {}
        self._decisions: dict[str, list[Decision]] = {}
        self._summaries: dict[str, list[SummaryRecord]] = {}

    # ------------------------------------------------------------------------------------------------ threads
    def create_thread(self, owner: Owner, title: str) -> ThreadInfo:
        now = utc_now()
        thread = ThreadInfo(thread_id=new_id(), owner=owner, title=title, created_at=now, updated_at=now)
        with self._lock:
            self._threads[thread.thread_id] = thread
            self._turns[thread.thread_id], self._decisions[thread.thread_id], self._summaries[thread.thread_id] = [], [], []
        return thread.model_copy()

    def get_thread(self, owner: Owner, thread_id: str) -> ThreadInfo:
        return self._owned(owner, thread_id).model_copy()

    def list_threads(self, owner: Owner, limit: int = 20) -> list[ThreadInfo]:
        owned = [t for t in self._threads.values() if t.owner == owner]
        return [t.model_copy() for t in sorted(owned, key=lambda t: t.updated_at, reverse=True)[:limit]]

    def set_title(self, owner: Owner, thread_id: str, title: str) -> None:
        with self._lock:
            self._owned(owner, thread_id).title = title[:200]

    def start_new_topic(self, owner: Owner, thread_id: str) -> ThreadInfo:
        with self._lock:
            thread = self._owned(owner, thread_id)
            thread.topic_epoch += 1
            thread.updated_at = utc_now()
        return thread.model_copy()

    # -------------------------------------------------------------------------------------------------- turns
    def append_turn(self, owner: Owner, turn: Turn) -> Turn:
        with self._lock:
            thread = self._owned(owner, turn.thread_id)
            turns = self._turns[turn.thread_id]
            if turns and turn.seq <= turns[-1].seq:
                raise ValueError("turn sequence numbers must increase")
            turns.append(turn.model_copy(deep=True))
            thread.updated_at = turn.created_at
        return turn

    def turns(self, owner: Owner, thread_id: str, topic_epoch: int | None = None, after_seq: int = 0,
              limit: int | None = None) -> list[Turn]:
        self._owned(owner, thread_id)
        selected = [t for t in self._turns[thread_id]
                    if t.seq > after_seq and (topic_epoch is None or t.topic_epoch == topic_epoch)]
        selected = selected[-limit:] if limit else selected
        return [t.model_copy(deep=True) for t in selected]

    def next_seq(self, owner: Owner, thread_id: str) -> int:
        self._owned(owner, thread_id)
        turns = self._turns[thread_id]
        return turns[-1].seq + 1 if turns else 1

    # ---------------------------------------------------------------------------------------------- decisions
    def add_decision(self, owner: Owner, decision: Decision) -> Decision:
        with self._lock:
            self._owned(owner, decision.thread_id)
            self._decisions[decision.thread_id].append(decision.model_copy())
        return decision

    def revoke_decision(self, owner: Owner, thread_id: str, decision_id: str, reason: str) -> Decision:
        with self._lock:
            self._owned(owner, thread_id)
            for decision in self._decisions[thread_id]:
                if decision.decision_id == decision_id:
                    if decision.active:
                        decision.revoked_at, decision.revoked_reason = utc_now(), reason
                    return decision.model_copy()
        raise KeyError("no such decision in this thread")

    def decisions(self, owner: Owner, thread_id: str, include_revoked: bool = False) -> list[Decision]:
        self._owned(owner, thread_id)
        return [d.model_copy() for d in self._decisions[thread_id] if include_revoked or d.active]

    # ---------------------------------------------------------------------------------------------- summaries
    def add_summary(self, owner: Owner, summary: SummaryRecord) -> SummaryRecord:
        with self._lock:
            self._owned(owner, summary.thread_id)
            existing = self._summaries[summary.thread_id]
            if existing and summary.version <= existing[-1].version:
                raise ValueError("summary versions must increase")
            existing.append(summary.model_copy(deep=True))
        return summary

    def latest_summary(self, owner: Owner, thread_id: str) -> SummaryRecord | None:
        self._owned(owner, thread_id)
        summaries = self._summaries[thread_id]
        return summaries[-1].model_copy(deep=True) if summaries else None

    def invalidate_summary(self, owner: Owner, thread_id: str, summary_id: str, reason: str) -> None:
        with self._lock:
            self._owned(owner, thread_id)
            for summary in self._summaries[thread_id]:
                if summary.summary_id == summary_id and summary.valid:
                    summary.invalidated_at, summary.invalidation_reason = utc_now(), reason

    # ---------------------------------------------------------------------------------------------- ownership
    def _owned(self, owner: Owner, thread_id: str) -> ThreadInfo:
        thread = self._threads.get(thread_id)
        if thread is None or thread.owner != owner:
            raise ThreadNotFound("thread not found")
        return thread
