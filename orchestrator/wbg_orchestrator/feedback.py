"""Feedback that improves three things, each kept separately:

1. This conversation: a thumbs-down with a correction is recorded as a constraint of the conversation (the existing
   `decisions`) and the question is re-run with it, so a revised answer follows at once (`revise` on the front door).
2. Later investigations: a thumbs-up on the revised answer validates the correction; a thumbs-up on an investigation
   saves how it was answered (steps, tables, filters; the SQL only as its hash, as in the checkpoints). These personal
   items, the user's saved preferences and reviewer-shared items are matched to each new question and passed to the
   planner and agents as labelled guidance: they may steer scope and method, never supply a number.
3. The platform: validated corrections and approaches are queued for a reviewer before anyone else sees them; every
   thumbs-down with a correction is queued as a candidate evaluation case (exported by `learning_cli`).

Feedback is data, never instructions: corrections pass the PII/secret filter and are refused when instruction-like.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, Sequence

from pydantic import BaseModel, Field, model_validator

from conversation.learning import (REASONS, Feedback, LearnedItem, LearningItemNotFound, LearningStore, Rating, Review,
                                   ReviewStatus)
from conversation.models import Owner, Turn, utc_now
from conversation.service import ConversationService
from conversation.store import ConversationStore
from structured_agent.guardrails.policies import contains_instruction_like_text

Embedder = Callable[[str], Sequence[float]]
MAX_GUIDANCE_ITEMS = 3
SEMANTIC_MIN = 0.60                     # cosine similarity (bge-m3) for an item to count as "a similar question"
LEXICAL_MIN = 0.50                      # word overlap when no embedding is available
REVISION_ROUTE = "revision"
GUIDANCE_HEADER = ("LEARNED GUIDANCE (from this user's confirmed feedback, saved preferences and reviewer-approved notes; "
                   "it guides scope and method only and is not evidence: every number still needs cited evidence)")


class FeedbackRefused(ValueError):
    """The feedback cannot be accepted as given (e.g. an instruction-like correction, or nothing to act on)."""


class ReviewerOnly(PermissionError):
    """Only a reviewer may do this."""


class Preferences(BaseModel):
    """The fixed set of preferences a user can save (structured values, never free text)."""

    default_fiscal_years: tuple[int, int] | None = None
    amount_unit: Literal["usd", "usd_millions"] | None = None
    answer_length: Literal["brief", "detailed"] | None = None
    table_lens: bool | None = None

    # The default fiscal years must be two years between 1990 and 2035, oldest first.
    @model_validator(mode="after")
    def _years_in_order(self) -> "Preferences":
        years = self.default_fiscal_years
        if years and not (1990 <= years[0] <= years[1] <= 2035):
            raise ValueError("default_fiscal_years must be two fiscal years between 1990 and 2035, oldest first")
        return self


@dataclass(frozen=True)
class AnswerRecord:
    """How an answered investigation was produced (read from its checkpoint; no result rows)."""

    question: str
    steps: tuple[str, ...] = ()                      # worker.operation of each task
    tables: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    sql_sha256: str | None = None                    # the executed SQL of a data answer, as its hash (never the text)
    answered: bool = True


class AnswerRecords(Protocol):
    def answer_record(self, owner: Owner, thread_id: str, request_id: str) -> AnswerRecord | None: ...


@dataclass(frozen=True)
class FeedbackOutcome:
    """The stored feedback, whether a revision job should run, and the learned items it created."""
    feedback: Feedback
    revise: bool                                     # the question will be re-run with the correction
    saved: tuple[str, ...] = ()                      # learned item ids created by this feedback


@dataclass(frozen=True)
class Guidance:
    text: str | None
    item_ids: tuple[str, ...] = ()


# One rating from the API: the answer's turn, up or down, known reasons and an optional free-text correction.
class FeedbackRequest(BaseModel):
    turn_seq: int = Field(gt=0)
    rating: Rating
    reasons: tuple[str, ...] = ()
    correction: str | None = Field(default=None, max_length=500)

    # Every reason given must be one of the known REASONS.
    @model_validator(mode="after")
    def _known_reasons(self) -> "FeedbackRequest":
        unknown = set(self.reasons) - set(REASONS)
        if unknown:
            raise ValueError(f"unknown reasons: {sorted(unknown)}")
        return self


class FeedbackService:
    """Records feedback and turns it into conversation constraints, personal guidance and reviewer queue items."""

    def __init__(self, store: LearningStore, conversations: ConversationService, conversation_store: ConversationStore,
                 answers: AnswerRecords, embed: Embedder | None = None, clock: Callable[[], dt.datetime] = utc_now):
        self._store, self._conversations, self._turns = store, conversations, conversation_store
        self._answers, self._embed, self._clock = answers, embed, clock

    # ------------------------------------------------------------------------------- 1. this conversation
    def give(self, owner: Owner, thread_id: str, request: FeedbackRequest) -> FeedbackOutcome:
        """Record feedback on one answer of the owner's conversation and act on it (see the module docstring)."""
        answer, question = self._answer_turn(owner, thread_id, request.turn_seq)
        record = self._answers.answer_record(owner, thread_id, answer.metadata.request_id) if answer.metadata.request_id else None
        correction = self._clean_correction(request.correction)
        if request.rating == "down" and not (request.reasons or correction):
            raise FeedbackRefused("say what was wrong: pick a reason or write a correction")
        feedback = Feedback(thread_id=thread_id, turn_seq=request.turn_seq, request_id=answer.metadata.request_id,
                            question=(record.question if record else question)[:2000], rating=request.rating,
                            reasons=request.reasons, correction=correction)
        if request.rating == "up":
            self._store.add_feedback(owner, feedback)
            return FeedbackOutcome(feedback, revise=False, saved=self._learn_from_approval(owner, answer, feedback, record))
        if correction:
            self._conversations.record_decision(owner, thread_id, "constraint",
                                                f"Correction to answer {request.turn_seq}: {correction}", request.turn_seq)
            self._queue(owner, "eval_case", feedback, {"question": feedback.question, "correction": correction,
                                                       "reasons": list(request.reasons), "answer_excerpt": answer.text[:600]})
        revise = bool(correction and record)
        feedback = feedback.model_copy(update={"status": "revising" if revise else "recorded"})
        self._store.add_feedback(owner, feedback)
        return FeedbackOutcome(feedback, revise=revise)

    def revised(self, owner: Owner, feedback_id: str, revision_request_id: str) -> Feedback:
        """The re-run finished: link it, so a thumbs-up on it validates the correction."""
        return self._store.set_feedback_status(owner, feedback_id, "revised", revision_request_id)

    def thread_feedback(self, owner: Owner, thread_id: str) -> dict[int, Feedback]:
        """The latest feedback per answer of a conversation."""
        return {f.turn_seq: f for f in self._store.thread_feedback(owner, thread_id)}

    # ------------------------------------------------------------------------------ 2. later investigations
    def guidance(self, owner: Owner, question: str) -> Guidance:
        """Preferences plus the saved items that match this question, as one labelled block (None when empty)."""
        preference_lines = _preference_lines(self.preferences(owner))
        now = self._clock()
        items = [i for i in self._store.items(owner) if not i.expired(now)]
        matched = self._rank(question, items)[:MAX_GUIDANCE_ITEMS]
        # Stored text is checked again here: a line that reads like an instruction never reaches the planner.
        lines = [line for line in preference_lines + [_item_line(i) for i in matched] if not contains_instruction_like_text(line)]
        if not lines:
            return Guidance(None)
        used = tuple(i.item_id for i in matched if _item_line(i) in lines)
        return Guidance("\n".join([GUIDANCE_HEADER, *lines]), used)

    def record_use(self, owner: Owner, request_id: str, item_ids: Sequence[str]) -> None:
        if item_ids:
            self._store.record_uses(owner, request_id, list(item_ids))

    def applied(self, owner: Owner, request_id: str) -> list[LearnedItem]:
        return self._store.used_items(owner, request_id)

    def preferences(self, owner: Owner) -> Preferences:
        return Preferences.model_validate(self._store.preferences(owner))

    def set_preferences(self, owner: Owner, update: Preferences) -> Preferences:
        """Only the fields present in the update change; a field set to null is reset."""
        for key in update.model_fields_set:
            value = getattr(update, key)
            if value is None:
                self._store.clear_preference(owner, key)
            else:
                self._store.set_preference(owner, key, list(value) if isinstance(value, tuple) else value)
        return self.preferences(owner)

    def items(self, owner: Owner) -> list[tuple[LearnedItem, bool]]:
        """The owner's items and the tenant's shared items, each with whether it has expired."""
        now = self._clock()
        return [(i, i.expired(now)) for i in self._store.items(owner)]

    def revoke(self, owner: Owner, item_id: str, reviewer: bool) -> LearnedItem:
        """Stop using a learned item; a shared (tenant-wide) item can only be revoked by a reviewer."""
        item = next((i for i in self._store.items(owner) if i.item_id == item_id), None)
        if item is None:
            raise LearningItemNotFound("item not found")
        if item.scope == "shared" and not reviewer:
            raise ReviewerOnly("only a reviewer can remove a shared item")
        return self._store.revoke_item(owner, item_id)

    # --------------------------------------------------------------------------------------- 3. the platform
    def reviews(self, owner: Owner, status: ReviewStatus | None = "pending") -> list[Review]:
        return self._store.reviews(owner, status)

    def decide(self, owner: Owner, review_id: str, approved: bool, note: str | None = None,
               expected_terms: Sequence[str] = ()) -> Review:
        """A reviewer's decision. Approving a shared correction or approach makes a tenant-wide copy of it."""
        review = next((r for r in self._store.reviews(owner, "pending") if r.review_id == review_id), None)
        if review is None:
            raise LearningItemNotFound("review not found")
        terms = tuple(t.strip()[:80] for t in expected_terms if t.strip())[:10]
        decided = self._store.decide_review(owner, review_id, "approved" if approved else "rejected",
                                            (note or "").strip()[:300] or None, terms)
        if approved and review.kind in ("share_correction", "share_approach"):
            payload = review.payload
            self._store.add_item(owner, LearnedItem(
                user_id=review.submitted_by, scope="shared", kind=payload["kind"], statement=payload["statement"],
                question=payload["question"], details=payload.get("details", {}), embedding=self._vector(payload["question"]),
                source_feedback_id=review.source_feedback_id, approved_by=owner.user_id))
        return decided

    # ------------------------------------------------------------------------------------------- internals
    def _answer_turn(self, owner: Owner, thread_id: str, turn_seq: int) -> tuple[Turn, str]:
        """The rated assistant turn and the user question just before it; any other turn is refused as not found."""
        turns = self._turns.turns(owner, thread_id)                           # ownership of the conversation
        answer = next((t for t in turns if t.seq == turn_seq and t.role == "assistant"), None)
        if answer is None:
            raise LearningItemNotFound("only answers can be rated")
        question = next((t.text for t in reversed(turns) if t.seq < turn_seq and t.role == "user"), "")
        return answer, question

    def _clean_correction(self, correction: str | None) -> str | None:
        """The correction with whitespace collapsed and personal data filtered; refused when instruction-like."""
        text = " ".join((correction or "").split())
        if not text:
            return None
        if contains_instruction_like_text(text):
            raise FeedbackRefused("a correction can change what is answered, not rules, permissions or tools")
        return self._conversations.text_filter(text)[:500]

    def _learn_from_approval(self, owner: Owner, answer: Turn, feedback: Feedback, record: AnswerRecord | None) -> tuple[str, ...]:
        """On a thumbs-up: validate the correction this answer revised, and save how the answer was produced (once)."""
        saved = []
        corrected = self._store.feedback_for_revision(owner, answer.metadata.request_id) if answer.metadata.request_id else None
        if corrected and corrected.status == "revised" and corrected.correction:
            self._store.set_feedback_status(owner, corrected.feedback_id, "validated")
            saved.append(self._save(owner, corrected, "correction", corrected.correction, {"reasons": list(corrected.reasons)}))
        request_id = answer.metadata.request_id
        already = any(i.kind == "approach" and i.details.get("request_id") == request_id for i in self._store.items(owner))
        if record and record.answered and (record.tables or record.steps) and not already:
            details = {"steps": list(record.steps), "tables": list(record.tables), "filters": list(record.filters),
                       "sql_sha256": record.sql_sha256, "request_id": request_id}
            saved.append(self._save(owner, feedback, "approach", _approach_statement(record), details))
        return tuple(saved)

    def _save(self, owner: Owner, feedback: Feedback, kind: str, statement: str, details: dict[str, Any]) -> str:
        """A personal item, plus a review that would share it with the tenant."""
        item = self._store.add_item(owner, LearnedItem(
            user_id=owner.user_id, kind=kind, statement=statement[:500], question=feedback.question, details=details,
            embedding=self._vector(feedback.question), source_feedback_id=feedback.feedback_id))
        self._queue(owner, f"share_{kind}", feedback, {"kind": kind, "statement": item.statement, "question": item.question,
                                                       "details": details})
        return item.item_id

    def _queue(self, owner: Owner, kind: str, feedback: Feedback, payload: dict[str, Any]) -> None:
        self._store.add_review(owner, Review(submitted_by=owner.user_id, kind=kind, payload=payload,
                                             source_feedback_id=feedback.feedback_id))

    def _vector(self, text: str) -> tuple[float, ...] | None:
        return tuple(float(x) for x in self._embed(text)) if self._embed and text else None

    def _rank(self, question: str, items: list[LearnedItem]) -> list[LearnedItem]:
        """Items similar enough to the question, best first: cosine similarity with vectors, else word overlap."""
        query = self._vector(question)
        scored = []
        for item in items:
            if query and item.embedding:
                score, floor = _cosine(query, item.embedding), SEMANTIC_MIN
            else:
                score, floor = _overlap(question, f"{item.question} {item.statement}"), LEXICAL_MIN
            if score >= floor:
                scored.append((score, item))
        return [i for _, i in sorted(scored, key=lambda pair: -pair[0])]


# ------------------------------------------------------------------------------------------------- helpers
def _preference_lines(preferences: Preferences) -> list[str]:
    """The saved preferences that shape answers, as guidance lines (the TableLens switch is not one of them)."""
    lines = []
    if preferences.default_fiscal_years:
        first, last = preferences.default_fiscal_years
        lines.append(f"- [preference] When the question names no period, the user's default is fiscal years {first}-{last}; "
                     "state that assumption.")
    if preferences.amount_unit:
        lines.append("- [preference] Amounts are reported in " +
                     ("USD millions." if preferences.amount_unit == "usd_millions" else "US dollars."))
    if preferences.answer_length:
        lines.append("- [preference] " + ("A brief answer: the key figures and findings first."
                                          if preferences.answer_length == "brief"
                                          else "A detailed answer, with the breakdowns behind the figures."))
    return lines


def _item_line(item: LearnedItem) -> str:
    """One learned item as a labelled guidance line, naming whether it is the user's own or a team note."""
    source = "team note" if item.scope == "shared" else "saved"
    asked = f'"{item.question[:160]}"'
    if item.kind == "correction":
        return f"- [{source} correction {item.item_id[:8]}] For a similar question ({asked}) the user confirmed: {item.statement}"
    return f"- [{source} approach {item.item_id[:8]}] A similar question ({asked}) was answered well with: {item.statement}"


def _approach_statement(record: AnswerRecord) -> str:
    """A short description of how an answer was produced: its steps, up to 4 tables and up to 3 filters."""
    parts = [f"steps {', '.join(record.steps)}"] if record.steps else []
    if record.tables:
        parts.append(f"tables {', '.join(t.rsplit('.', 1)[-1] for t in record.tables[:4])}")
    if record.filters:
        parts.append(f"filters {'; '.join(record.filters[:3])}")
    return ("; ".join(parts) + ".")[:500]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _overlap(question: str, text: str) -> float:
    asked = _words(question)
    return len(asked & _words(text)) / max(1, len(asked))
