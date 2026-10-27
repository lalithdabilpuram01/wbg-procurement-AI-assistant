"""Thin adapter over the frozen Module 2 models (ml/serving): each package -> three model-prediction evidence records.

Operations (read-only):
  predict_package          an existing project's package (`WorkerContext.package`); context looked up by project ID
  predict_planned_packages the packages of a new project (`WorkerContext.planning`); context from its profile, the
                           method suggested from historical practice where the planner left it open
Inputs come from the structured request, never from message text. Project context is read from the Gold tables with
fixed SQL through the read-only gateway (`run_sql`). Every record is a MODEL_ESTIMATE: quotable as an estimate, never as
a recorded fact. Models 2 and 3 carry the `synthetic_training_data` warning.
"""
from __future__ import annotations

import re
from typing import Any

from ml.serving.features import InvalidRequest, PackageRequest
from ml.serving.gold import GOLD, RunSql, new_project_context
from ml.serving.models import Driver, FrozenModelError, FrozenModels
from ml.serving.service import PackagePrediction, predict_for_context, predict_package

from ..contracts import (AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, Fact, NumericUse,
                         PackageSpec, PlannedPackage, PlanningSpec, SourceSystem, TaskState, ValidationStatus, WorkerName,
                         new_id)
from .base import TicketAuthority, WorkerContext, WorkerOutput

PROJECT_ID = re.compile(r"P\d{6}")
GATEWAY_MAX_ROWS = 500                    # the fixed lookups' largest LIMIT (the gateway allows up to 1,000)
SOURCE_OBJECTS = (f"{GOLD}.gold_project_portfolio", f"{GOLD}.gold_country_context", f"{GOLD}.gold_procurement_notices")


class LookupFailed(RuntimeError):
    """The Gold lookup was rejected or failed (code only; never data)."""


class PredictionWorker:
    name = WorkerName.PREDICTION
    operations = frozenset({"predict_package", "predict_planned_packages"})

    def __init__(self, run_sql: RunSql, authority: TicketAuthority, models: FrozenModels | None = None):
        self._run_sql, self._authority = run_sql, authority
        self._models = models or FrozenModels()

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        self._authority.verify(context.ticket, task)
        if task.operation not in self.operations:
            return _failed(task, ErrorCategory.AUTHORIZATION, "unknown_operation", "The operation is not offered by this worker.")
        if task.operation == "predict_planned_packages":
            return self._planned(task, context)
        if not isinstance(context.package, PackageSpec):
            return _failed(task, ErrorCategory.VALIDATION, "package_missing", "A package assessment needs the package details.")
        try:
            prediction = predict_package(_request(context.package), self._run_sql, self._models)
        except InvalidRequest as invalid:
            return _failed(task, ErrorCategory.VALIDATION, "invalid_package", str(invalid)[:300])
        except FrozenModelError:
            return _failed(task, ErrorCategory.INTERNAL, "model_integrity", "A frozen model failed its integrity check.")
        except LookupFailed as failed:
            return _failed(task, ErrorCategory.EXECUTION, str(failed)[:80], "The project context could not be read.")
        return WorkerOutput(TaskState.SUCCEEDED, evidence=tuple(_evidence(task, context.package, prediction)), notes=prediction.notes)


    def _planned(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        spec = context.planning
        if not isinstance(spec, PlanningSpec):
            return _failed(task, ErrorCategory.VALIDATION, "planning_missing", "A Planning Brief needs the project profile.")
        suggested = _suggested_methods(context.upstream_evidence)
        evidence: list[EvidenceRecord] = []
        notes: list[str] = []
        try:
            for package in spec.packages:
                method = package.procurement_method or suggested.get(package.procurement_category)
                if method is None:
                    notes.append(f"No method could be suggested for '{package.name}'; it was not scored.")
                    continue
                earlier = sum(1 for p in spec.packages if p.tender_date < package.tender_date)
                project = new_project_context(spec.profile.country_code, spec.profile.sector, spec.profile.approval_date,
                                              earlier, self._run_sql)
                request = PackageRequest(None, method, package.procurement_category, package.review_type,
                                         float(package.bid_window_days), package.tender_date)
                prediction = predict_for_context(request, project, self._models)
                label = f"package '{package.name}'" + ("" if package.procurement_method else f" (method suggested: {method})")
                evidence += _evidence(task, _spec_of(spec, package, method), prediction, label)
                notes += prediction.notes
        except InvalidRequest as invalid:
            return _failed(task, ErrorCategory.VALIDATION, "invalid_plan", str(invalid)[:300])
        except FrozenModelError:
            return _failed(task, ErrorCategory.INTERNAL, "model_integrity", "A frozen model failed its integrity check.")
        except LookupFailed as failed:
            return _failed(task, ErrorCategory.EXECUTION, str(failed)[:80], "The country context could not be read.")
        if not evidence:
            return _failed(task, ErrorCategory.NO_EVIDENCE, "no_packages_scored", "No package could be scored.")
        return WorkerOutput(TaskState.SUCCEEDED, evidence=tuple(evidence), notes=tuple(dict.fromkeys(notes)))


def _suggested_methods(upstream: tuple[EvidenceRecord, ...]) -> dict[str, str]:
    """Category -> the method used most often historically (first row of the market step's methods benchmark)."""
    return {e.provenance_value("category"): e.facts[0].name for e in upstream
            if e.provenance_value("template") == "methods" and e.facts and e.provenance_value("category")}


def _spec_of(spec: PlanningSpec, package: PlannedPackage, method: str) -> PackageSpec:
    """The package in the shape the evidence records describe (a new project has no ID: 'new project' in citations)."""
    return PackageSpec.model_construct(project_id=f"new {spec.profile.country_code} {spec.profile.sector} project",
                                       procurement_method=method, procurement_category=package.procurement_category,
                                       review_type=package.review_type, bid_window_days=package.bid_window_days,
                                       tender_date=package.tender_date)


def _request(package: PackageSpec) -> PackageRequest:
    return PackageRequest(package.project_id, package.procurement_method, package.procurement_category, package.review_type,
                          float(package.bid_window_days), package.tender_date)


def _drivers(drivers: tuple[Driver, ...]) -> str:
    return "; ".join(f"{d.label}: {d.effect}" for d in drivers) or "no dominant driver"


def _evidence(task: AgentTask, package: PackageSpec, p: PackagePrediction, label: str = "") -> list[EvidenceRecord]:
    inputs = (f"{label + ': ' if label else ''}{package.project_id}, {package.procurement_method}, {package.procurement_category}, "
              f"{package.review_type} review, {package.bid_window_days}-day bid window, tender {package.tender_date.isoformat()}")
    context = [Fact(name=f"input_{k}", value=str(v)) for k, v in p.context.items()]
    notes = ("model_input_note",) if p.notes else ()
    d = p.duration
    duration = _record(task, package, "Model 1 tender duration (ridge_final, real data)", inputs, (
        Fact(name="estimated_duration", value=str(d.days), unit="days"),
        Fact(name="likely_range_p10", value=str(d.p10_days), unit="days"),
        Fact(name="likely_range_p90", value=str(d.p90_days), unit="days"),
        Fact(name="probability_over_365_days", value=f"{100 * d.probability_over_365:.0f}", unit="%"),
        Fact(name="long_tender_alert", value="yes" if d.long_tender_alert else "no"),
        Fact(name="estimated_signing_date", value=p.estimated_signing_date.isoformat()), *context),
        f"{d.model}. Estimated {d.days} days from first tender to contract signing (likely {d.p10_days}-{d.p90_days} days); "
        f"probability over 365 days {100 * d.probability_over_365:.0f}%. Drivers: {_drivers(d.drivers)}.", notes)
    records = [duration]
    for name, risk in (("Model 2 amendment risk (m2_S2_l2_final, SYNTHETIC)", p.amendment),
                       ("Model 3 re-bidding risk (m3_S2_l2_final, SYNTHETIC)", p.rebid)):
        records.append(_record(task, package, name, inputs, (
            Fact(name="simulated_probability", value=f"{risk.simulated_probability_pct}", unit="%"),
            Fact(name="assessment", value=risk.flag[:200])),
            f"{risk.model}: simulated probability {risk.simulated_probability_pct}% ({risk.flag}). "
            f"Simulated drivers: {_drivers(risk.drivers)}. {risk.disclaimer}.", notes + ("synthetic_training_data",)))
    return records


def _record(task: AgentTask, package: PackageSpec, model: str, inputs: str, facts: tuple[Fact, ...], summary: str,
            warnings: tuple[str, ...]) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
        source_system=SourceSystem.FROZEN_ML_MODELS, kind=EvidenceKind.MODEL_PREDICTION,
        project_ids=(package.project_id,) if PROJECT_ID.fullmatch(package.project_id) else (),
        source_objects=SOURCE_OBJECTS, source_version="ml:frozen", citation=f"{model}; inputs: {inputs}"[:300],
        provenance=(("model", model), ("integrity", "sha256 checked against FROZEN.json")),
        validation_status=ValidationStatus.VERIFIED, numeric_use=NumericUse.MODEL_ESTIMATE, quality_warnings=warnings,
        facts=facts, summary=summary[:1500])


def _failed(task: AgentTask, category: ErrorCategory, code: str, message: str) -> WorkerOutput:
    return WorkerOutput(TaskState.FAILED, error=ExecutionError(
        error_id=new_id("err"), investigation_id=task.investigation_id, task_id=task.task_id, node="execute",
        category=category, code=code[:80], safe_message=message))


def gateway_sql(service: Any, principal_id: str, workspace: str) -> RunSql:
    """`run_sql` over the Structured Data Agent's read-only gateway (validation, column policy, result checks, audit)."""
    from structured_agent.service import RequestContext

    def run(sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        result = service.execute_readonly_sql(RequestContext(principal_id, workspace), sql=sql, parameters=parameters,
                                              max_rows=GATEWAY_MAX_ROWS)
        if result.status != "ok":
            raise LookupFailed(f"gold_lookup_{result.status}")
        return list(result.rows)
    return run
