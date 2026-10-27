"""Thin adapter over the frozen Module 2 models (ml/serving): one package -> three model-prediction evidence records.

Operation (read-only): predict_package -> ml.serving.predict_package(request, run_sql, models)
The package comes from the structured request (`WorkerContext.package`), never from message text. Project context is
read from the Gold tables with fixed SQL through the read-only gateway (`run_sql`). Every record is a MODEL_ESTIMATE:
quotable as an estimate, never as a recorded fact. Models 2 and 3 carry the `synthetic_training_data` warning.
"""
from __future__ import annotations

from typing import Any

from ml.serving.features import InvalidRequest, PackageRequest
from ml.serving.gold import GOLD, RunSql
from ml.serving.models import Driver, FrozenModelError, FrozenModels
from ml.serving.service import PackagePrediction, predict_package

from ..contracts import (AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, Fact, NumericUse,
                         PackageSpec, SourceSystem, TaskState, ValidationStatus, WorkerName, new_id)
from .base import TicketAuthority, WorkerContext, WorkerOutput

SOURCE_OBJECTS = (f"{GOLD}.gold_project_portfolio", f"{GOLD}.gold_country_context", f"{GOLD}.gold_procurement_notices")


class LookupFailed(RuntimeError):
    """The Gold lookup was rejected or failed (code only; never data)."""


class PredictionWorker:
    name = WorkerName.PREDICTION
    operations = frozenset({"predict_package"})

    def __init__(self, run_sql: RunSql, authority: TicketAuthority, models: FrozenModels | None = None):
        self._run_sql, self._authority = run_sql, authority
        self._models = models or FrozenModels()

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        self._authority.verify(context.ticket, task)
        if task.operation not in self.operations:
            return _failed(task, ErrorCategory.AUTHORIZATION, "unknown_operation", "The operation is not offered by this worker.")
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
        return WorkerOutput(TaskState.SUCCEEDED, evidence=_evidence(task, context.package, prediction), notes=prediction.notes)


def _request(package: PackageSpec) -> PackageRequest:
    return PackageRequest(package.project_id, package.procurement_method, package.procurement_category, package.review_type,
                          float(package.bid_window_days), package.tender_date)


def _drivers(drivers: tuple[Driver, ...]) -> str:
    return "; ".join(f"{d.label}: {d.effect}" for d in drivers) or "no dominant driver"


def _evidence(task: AgentTask, package: PackageSpec, p: PackagePrediction) -> tuple[EvidenceRecord, ...]:
    inputs = (f"{package.project_id}, {package.procurement_method}, {package.procurement_category}, {package.review_type} review, "
              f"{package.bid_window_days}-day bid window, tender {package.tender_date.isoformat()}")
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
    return tuple(records)


def _record(task: AgentTask, package: PackageSpec, model: str, inputs: str, facts: tuple[Fact, ...], summary: str,
            warnings: tuple[str, ...]) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
        source_system=SourceSystem.FROZEN_ML_MODELS, kind=EvidenceKind.MODEL_PREDICTION, project_ids=(package.project_id,),
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
                                              max_rows=100)
        if result.status != "ok":
            raise LookupFailed(f"gold_lookup_{result.status}")
        return list(result.rows)
    return run
