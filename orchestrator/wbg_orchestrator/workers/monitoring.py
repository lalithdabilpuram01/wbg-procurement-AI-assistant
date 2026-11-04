"""Execution & Monitoring in chat: "is P148850 slipping?" answered from the monitoring rules (monitoring.watch).

Operation:
  watch_project -> EARLY_WARNING records: one status summary of the project (packages, warnings, slippage, latest ISR
                   ratings) and its high and medium warnings (at most MAX_WARNINGS, at most PER_RULE of a kind; live
                   problems before past ones), each citing the plan row, contract record or ISR it rests on. Deterministic: no model call, and the answer restates these records.
The project comes from the fixed monitoring plan (the front door detected it in the message with `monitoring_project`).
"""
from __future__ import annotations

import re
from collections.abc import Callable

from monitoring.watch import ProjectWatch, Warning
from monitoring.watch import watch as watch_project

from ..contracts import (AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, Fact, NumericUse, SourceSystem,
                         TaskState, ValidationStatus, WorkerName, new_id)
from .base import TicketAuthority, WorkerContext, WorkerOutput

MAX_WARNINGS, PER_RULE = 8, 3
LIVE_FIRST = ("overdue_signing", "stalled_stage", "rating_downgraded", "rating_unsatisfactory", "high_risk", "many_cancellations",
              "cost_overrun", "slipped_signing", "plan_out_of_date")      # what needs action now, then what already happened
SOURCES = ("pilot_v3.plan_activities", "pilot_v3.evidence_links", "pilot_v3.isr_ratings")
PROJECT = re.compile(r"(?<![A-Z0-9])P\d{6}(?!\d)")
MONITORING = re.compile(
    r"\b(slipp(?:ing|ed|age)|behind schedule|on (?:track|schedule)|overdue|running late|early[- ]warnings?|warning signs?|"
    r"red flags?|stall(?:ed|ing)|stuck|milestones?|monitor(?:ing)?|watch|at risk|procurement (?:status|progress)|"
    r"(?:delayed|late) (?:packages?|contracts?)|(?:packages?|contracts?) (?:are |is |were )?(?:delayed|late))\b", re.I)
NOT_MONITORING = re.compile(r"\b(why|lessons?|caus(?:e|es|ed)|reasons?|predict\w*|forecast\w*)\b", re.I)


def monitoring_project(text: str) -> str | None:
    """The project a monitoring question is about ("Is P148850 slipping?"), or None. Questions about causes and
    lessons stay with the documents, predictions with the models; exactly one project must be named."""
    projects = set(PROJECT.findall(text))
    if len(projects) != 1 or not MONITORING.search(text) or NOT_MONITORING.search(text):
        return None
    return projects.pop()


class MonitoringWorker:
    """Worker for `watch_project`: runs the deterministic monitoring rules for one project and cites each finding."""
    name = WorkerName.MONITORING
    operations = frozenset({"watch_project"})

    def __init__(self, authority: TicketAuthority, watch: Callable[[str], ProjectWatch] = watch_project):
        self._authority, self._watch = authority, watch

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        """Check the ticket, find the project ID in the task question and return its status and selected warnings."""
        self._authority.verify(context.ticket, task)
        if task.operation not in self.operations:
            return _failed(task, ErrorCategory.AUTHORIZATION, "unknown_operation", "The operation is not offered by this worker.")
        project = PROJECT.search(task.question)
        if project is None:
            return _failed(task, ErrorCategory.VALIDATION, "project_missing", "Monitoring needs a project ID (P + 6 digits).")
        view = self._watch(project.group(0))
        if not view.packages and not view.ratings:
            return _failed(task, ErrorCategory.NO_EVIDENCE, "no_monitoring_data",
                           "There is no procurement plan or ISR for this project in the document corpus.")
        return WorkerOutput(TaskState.SUCCEEDED, evidence=(_status(task, view), *(_warning(task, view, w) for w in _selected(view.warnings))))


def _selected(warnings: list[Warning]) -> list[Warning]:
    """High before medium; within a severity, live problems first; a few of each rule so one kind cannot crowd out the rest."""
    rank = {code: i for i, code in enumerate(LIVE_FIRST)}
    chosen, per_rule = [], {}
    for w in sorted((w for w in warnings if w.severity != "low"),
                    key=lambda w: (w.severity != "high", rank.get(w.code, len(rank)), -(w.value or 0))):
        if per_rule.get(w.code, 0) < PER_RULE:
            per_rule[w.code] = per_rule.get(w.code, 0) + 1
            chosen.append(w)
    return chosen[:MAX_WARNINGS]


# ---------------------------------------------------------------------------------------------- records
def _status(task: AgentTask, view: ProjectWatch) -> EvidenceRecord:
    """The project's status summary as one record: package counts, warnings, slippage and the latest ISR ratings."""
    s = view.summary
    text = (f"{view.title or view.project_id} ({view.project_id}): {s['packages']} packages in the latest procurement plan"
            f"{f' (statuses as of {view.as_of})' if view.as_of else ''}: {s['signed']} signed, {s['open']} open, "
            f"{s['cancelled']} cancelled. {s['warnings']['high']} high and {s['warnings']['medium']} medium early warnings")
    if s["median_slippage_days"] is not None:
        text += f"; signed packages were signed a median {round(s['median_slippage_days'])} days after their planned date"
    if s["stale_plan_entries"]:
        text += f"; {s['stale_plan_entries']} plan entries are still pending years after their planned date (probably abandoned)"
    text += "."
    latest = {r["indicator"]: r for r in view.ratings}                       # ratings are oldest first
    if latest:
        date = max((r["date"] for r in latest.values() if r["date"]), default=None)
        text += f" Latest ISR{f' ({date})' if date else ''}: " + "; ".join(f"{r['indicator']} {r['current']}" for r in latest.values()) + "."
    plans, isrs = s["plans"], s["isr_reports"]
    citation = (f"Project watch {view.project_id}: procurement plan {', '.join(plans[:3]) or 'none'}"
                f"{f'; ISR {isrs[-1]}' if isrs else ''}")
    facts = tuple(Fact(name=k, value=str(round(s[k]) if isinstance(s[k], float) else s[k]))
                  for k in ("packages", "signed", "open", "cancelled", "overdue", "stale_plan_entries", "median_slippage_days")
                  if s[k] is not None) + tuple(Fact(name=f"{k}_warnings", value=str(v)) for k, v in s["warnings"].items())
    return _record(task, view, citation, facts, text)


def _warning(task: AgentTask, view: ProjectWatch, w: Warning) -> EvidenceRecord:
    """One early warning as a record, with its rule, severity, value and benchmark group when it has one."""
    text = f"{w.severity.capitalize()} warning: {f'{w.reference}: ' if w.reference else ''}{w.message}"
    facts = [Fact(name="rule", value=w.code), Fact(name="severity", value=w.severity)]
    if w.value is not None:
        facts.append(Fact(name="value", value=str(w.value)))
    if w.benchmark and "n" in w.benchmark:
        facts += [Fact(name="benchmark_group", value=str(w.benchmark["group"])[:200]),
                  Fact(name="benchmark_packages", value=str(w.benchmark["n"]))]
    return _record(task, view, "; ".join(w.citations) or f"Project watch {view.project_id}", tuple(facts), text)


def _record(task: AgentTask, view: ProjectWatch, citation: str, facts: tuple[Fact, ...], summary: str) -> EvidenceRecord:
    """A verified EARLY_WARNING record from the typed plan records; its figures are quotable document statements."""
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
        source_system=SourceSystem.TYPED_PLAN_RECORDS, kind=EvidenceKind.EARLY_WARNING, project_ids=(view.project_id,),
        source_objects=SOURCES, source_version="pilot_v3:monitoring_rules", citation=citation[:300],
        provenance=(("rules", "monitoring.watch"), ("as_of", view.as_of or "unknown")),
        validation_status=ValidationStatus.VERIFIED, numeric_use=NumericUse.DOCUMENT_STATEMENT, facts=facts, summary=summary[:1500])


def _failed(task: AgentTask, category: ErrorCategory, code: str, message: str) -> WorkerOutput:
    return WorkerOutput(TaskState.FAILED, error=ExecutionError(
        error_id=new_id("err"), investigation_id=task.investigation_id, task_id=task.task_id, node="execute",
        category=category, code=code, safe_message=message))
