"""Tender Design & Packaging: a draft tender checked against history (tender.check; no model call).

Operation:
  check_tender_design -> TENDER_CHECK records: the terms read from the draft (each with its sentence), then one record
                         per check (bid window, method, review type, packaging, turnover, similar experience) with its
                         status, benchmark and source. The bid-window benchmark is a fixed, reviewed Gold query over
                         published response windows; the others come from the procurement-plan records.
The package and the draft come from the structured request (`WorkerContext.tender`), never from message text.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

from ml.serving.gold import GOLD, RunSql
from monitoring import data
from tender import check, history
from tender.extract import Term, extract

from ..contracts import (AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, Fact, NumericUse, SourceSystem,
                         TaskState, TenderSpec, ValidationStatus, WorkerName, new_id)
from .base import TicketAuthority, WorkerContext, WorkerOutput
from .prediction import LookupFailed

NOTICES = f"{GOLD}.gold_procurement_notices"
YEARS_OF_HISTORY = 10
_WINDOW = (f"SELECT COUNT(*) AS notices, PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY published_response_window_days) AS p25_days, "
           f"MEDIAN(published_response_window_days) AS median_days, PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY "
           f"published_response_window_days) AS p75_days FROM {NOTICES} WHERE published_response_window_days IS NOT NULL "
           f"AND notice_type = :notice_type AND procurement_category = :category AND publication_fiscal_year >= :since_fy")
WINDOW_SQL = {"category and method": _WINDOW + " AND procurement_method = :method", "category": _WINDOW}
LABELS = {"bid_window_days": "bid window", "bid_validity_days": "bid validity", "bid_security_percent": "bid security",
          "lots": "lots", "experience_years": "experience", "completion_months": "completion period",
          "turnover_usd": "average annual turnover", "similar_contracts": "similar contracts",
          "similar_contract_value_usd": "value of each similar contract"}
UNITS = {"days": "days", "percent": "%", "lots": "", "years": "years", "months": "months", "contracts": "", "usd": ""}


def notice_type(category: str) -> str:
    return "Request for Expression of Interest" if category == "Consultant Services" else "Invitation for Bids"


class TenderDesignWorker:
    name = WorkerName.TENDER
    operations = frozenset({"check_tender_design"})

    def __init__(self, run_sql: RunSql, authority: TicketAuthority, today: Callable[[], dt.date] = dt.date.today,
                 history_dir: Path = data.SHARED):
        self._run_sql, self._authority, self._today, self._history = run_sql, authority, today, history_dir

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        self._authority.verify(context.ticket, task)
        if task.operation not in self.operations:
            return _failed(task, ErrorCategory.AUTHORIZATION, "unknown_operation", "The operation is not offered by this worker.")
        if not isinstance(context.tender, TenderSpec):
            return _failed(task, ErrorCategory.VALIDATION, "tender_missing", "A tender check needs the package and its draft.")
        spec = context.tender
        terms = extract(spec.draft_text)
        package = check.Package(spec.project_id, spec.procurement_category, spec.procurement_method, spec.review_type,
                                spec.estimated_amount_usd, spec.bid_window_days, spec.lots)
        checks = check.run(package, terms, self._window(spec), self._history)
        evidence = ([_terms_record(task, spec, terms)] if terms else []) + [_check_record(task, spec, c) for c in checks]
        notes = () if terms or not spec.draft_text.strip() else ("no_terms_found_in_draft",)
        return WorkerOutput(TaskState.SUCCEEDED, evidence=tuple(evidence), notes=notes)

    def _window(self, spec: TenderSpec) -> check.WindowBenchmark | None:
        """Published response windows: same notice type, category and method; the category alone when too few."""
        since = self._today().year - YEARS_OF_HISTORY
        params = {"notice_type": notice_type(spec.procurement_category), "category": spec.procurement_category, "since_fy": since}
        for scope, sql in WINDOW_SQL.items():
            try:
                rows = self._run_sql(sql, params | ({"method": spec.procurement_method} if scope != "category" else {}))
            except LookupFailed:
                return None                                       # the other checks stand on their own
            row = rows[0] if rows else {}
            if int(row.get("notices") or 0) >= history.MIN_PACKAGES:
                return check.WindowBenchmark(params["notice_type"], scope, int(row["notices"]), float(row["p25_days"]),
                                             float(row["median_days"]), float(row["p75_days"]), since)
        return None


# ---------------------------------------------------------------------------------------------- records
def _show(term: Term) -> str:
    if term.unit == "usd":
        return f"US$ {term.value:,.0f}"
    if term.unit == "percent":
        return f"{term.value:g}%"
    return f"{term.value:g} {UNITS[term.unit]}".strip()


def _terms_record(task: AgentTask, spec: TenderSpec, terms: list[Term]) -> EvidenceRecord:
    summary = "Read from the draft: " + "; ".join(f"{LABELS[t.name]} {_show(t)}" for t in terms) + "."
    facts = tuple(f for t in terms for f in (Fact(name=t.name, value=_show(t)), Fact(name=f"{t.name}_sentence", value=t.sentence[:200])))
    return _record(task, spec, "Draft tender text (terms read by rules)", facts, summary, SourceSystem.TYPED_PLAN_RECORDS,
                   NumericUse.DOCUMENT_STATEMENT, (("rules", "tender.extract"),))


def _check_record(task: AgentTask, spec: TenderSpec, c: check.Check) -> EvidenceRecord:
    gold = c.code == "bid_window" and bool(c.benchmark)
    facts = (Fact(name="check", value=c.code), Fact(name="status", value=c.status),
             *(Fact(name=k, value=str(v)[:200]) for k, v in c.benchmark.items()))
    return _record(task, spec, "; ".join(c.sources) or "Tender design check", facts, f"{c.title}: {c.message}",
                   SourceSystem.DATABRICKS_PUBLIC if gold else SourceSystem.TYPED_PLAN_RECORDS,
                   NumericUse.APPROVED if gold else NumericUse.DOCUMENT_STATEMENT,
                   (("rules", "tender.check"), ("check", c.code), ("status", c.status)), (NOTICES,) if gold else ())


def _record(task: AgentTask, spec: TenderSpec, citation: str, facts: tuple[Fact, ...], summary: str, source: SourceSystem,
            numeric_use: NumericUse, provenance: tuple[tuple[str, str], ...], objects: tuple[str, ...] = ()) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id, source_system=source,
        kind=EvidenceKind.TENDER_CHECK, project_ids=(spec.project_id,), source_objects=objects or ("pilot_v3.plan_activities",),
        source_version="tender_rules", citation=citation[:300], provenance=provenance, validation_status=ValidationStatus.VERIFIED,
        numeric_use=numeric_use, facts=facts[:25], summary=summary[:1500])


def _failed(task: AgentTask, category: ErrorCategory, code: str, message: str) -> WorkerOutput:
    return WorkerOutput(TaskState.FAILED, error=ExecutionError(
        error_id=new_id("err"), investigation_id=task.investigation_id, task_id=task.task_id, node="execute",
        category=category, code=code, safe_message=message))
