"""Continuous Learning: reviewed lesson cards for the situation at hand (lessons.match; no model call).

Operation:
  find_lessons -> LESSON_CARD records: the approved lessons that best fit the request (at most lessons.match.MAX_LESSONS),
                  each citing its report and page and naming its reviewer. The situation comes from the request:
                    a tender check  -> tender design: procurement, qualification, specifications, packaging; the category
                    a Planning Brief -> planning: design, capacity, financing, packaging; the sector
                    a monitored project (its ID in the fixed question) -> execution: delays, contractors, costs, capacity
                  When no approved card fits, one record says how many proposed cards for this stage await a reviewer.
Only approved cards are ever used: proposed and rejected ones never reach an answer.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from lessons.cards import LessonCard
from lessons.match import Situation, relevant, score
from monitoring import data

from ..contracts import (AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, Fact, NumericUse, PlanningSpec,
                         SourceSystem, TaskState, TenderSpec, ValidationStatus, WorkerName, new_id)
from .base import TicketAuthority, WorkerContext, WorkerOutput

PROJECT = re.compile(r"(?<![A-Z0-9])P\d{6}(?!\d)")
STAGE_TOPICS = {"tender_design": frozenset({"procurement", "qualification", "specifications", "packaging"}),
                "planning": frozenset({"design", "capacity", "financing", "packaging"}),
                "execution": frozenset({"timeline", "contractor_performance", "cost", "capacity"})}
STAGE_LABELS = {"tender_design": "tender design", "planning": "project planning", "execution": "execution and monitoring"}
CardSource = Callable[[], list[LessonCard]]          # every card with its status (the store)


class LessonsWorker:
    """Worker for `find_lessons`: turns the request into a situation and returns the approved cards that fit."""
    name = WorkerName.LESSONS
    operations = frozenset({"find_lessons"})

    def __init__(self, authority: TicketAuthority, cards: CardSource,
                 projects: Callable[[], dict[str, data.Project]] = data.projects):
        self._authority, self._cards, self._projects = authority, cards, projects

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        """Check the ticket, find the situation and return lesson records, or one "none yet" record when none fit."""
        self._authority.verify(context.ticket, task)
        if task.operation not in self.operations:
            return _failed(task, ErrorCategory.AUTHORIZATION, "unknown_operation", "The operation is not offered by this worker.")
        situation = self._situation(task, context)
        if situation is None:
            return _failed(task, ErrorCategory.VALIDATION, "situation_missing", "Lessons need a tender, a brief or a project.")
        try:
            cards = self._cards()
        except Exception:                                   # the store is down: the answer stands without lessons
            return _failed(task, ErrorCategory.EXECUTION, "lesson_store_unavailable", "The lesson cards could not be read.")
        chosen = relevant(cards, situation)
        if chosen:
            return WorkerOutput(TaskState.SUCCEEDED, evidence=tuple(_lesson(task, c) for c in chosen))
        # Only counted, never shown: proposed cards tell the user a reviewer has work waiting for this stage.
        waiting = sum(c.status == "proposed" and score(c, situation) > 0 for c in cards)
        return WorkerOutput(TaskState.SUCCEEDED, evidence=(_none_yet(task, situation, waiting),))

    def _situation(self, task: AgentTask, context: WorkerContext) -> Situation | None:
        """Stage and topics of the request: from a tender check first, then a Planning Brief, then a project ID."""
        if isinstance(context.tender, TenderSpec):
            spec = context.tender
            return self._about(Situation("tender_design", STAGE_TOPICS["tender_design"], spec.procurement_category, spec.project_id))
        if isinstance(context.planning, PlanningSpec):
            return Situation("planning", STAGE_TOPICS["planning"], sector=context.planning.profile.sector)
        project = PROJECT.search(task.question)
        return self._about(Situation("execution", STAGE_TOPICS["execution"], project_id=project.group(0))) if project else None

    def _about(self, s: Situation) -> Situation:
        """The project's sector and country, so lessons from similar projects rank higher."""
        info = self._projects().get(s.project_id or "")
        return Situation(s.stage, s.topics, s.category, s.project_id, info.sector if info else None, info.country if info else None)


# ---------------------------------------------------------------------------------------------- records
def _lesson(task: AgentTask, card: LessonCard) -> EvidenceRecord:
    """One approved lesson card as verified evidence, citing its report, pages and reviewer."""
    facts = (Fact(name="card_id", value=card.card_id), Fact(name="source", value=card.source_label[:200]),
             Fact(name="project_id", value=card.project_id or "none"), Fact(name="topics", value=", ".join(card.topics)[:200]),
             Fact(name="stages", value=", ".join(card.stages)[:200]), Fact(name="reviewed_by", value=card.decided_by or "unknown"))
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id, source_system=SourceSystem.RAG_PDF_CORPUS,
        kind=EvidenceKind.LESSON_CARD, project_ids=(card.project_id,) if card.project_id else (), document_id=card.document_id,
        pages=(card.page_start, card.page_end or card.page_start) if card.page_start else None, source_objects=("wbg_kg.lesson_card",),
        source_version=f"lesson_card:{card.origin}", citation=card.citation[:300],
        provenance=(("card_id", card.card_id), ("reviewed_by", card.decided_by or "unknown")),
        validation_status=ValidationStatus.VERIFIED, numeric_use=NumericUse.DOCUMENT_STATEMENT, facts=facts,
        summary=f"Lesson from {card.project_id or 'a past project'} ({card.source_label}): {card.headline}"[:1500])


def _none_yet(task: AgentTask, s: Situation, waiting: int) -> EvidenceRecord:
    """The record used when no approved card fits: says so, and how many proposed cards await review."""
    text = (f"No reviewed lessons for {STAGE_LABELS[s.stage]} match yet"
            + (f"; {waiting} proposed lesson cards for this stage await a reviewer." if waiting else "."))
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id, source_system=SourceSystem.RAG_PDF_CORPUS,
        kind=EvidenceKind.LESSON_CARD, source_objects=("wbg_kg.lesson_card",), source_version="lesson_card:none_approved",
        citation="Lesson cards (review queue)", validation_status=ValidationStatus.VERIFIED, numeric_use=NumericUse.DOCUMENT_STATEMENT,
        facts=(Fact(name="awaiting_review", value=str(waiting)),), summary=text)


def _failed(task: AgentTask, category: ErrorCategory, code: str, message: str) -> WorkerOutput:
    return WorkerOutput(TaskState.FAILED, error=ExecutionError(
        error_id=new_id("err"), investigation_id=task.investigation_id, task_id=task.task_id, node="execute",
        category=category, code=code, safe_message=message))
