"""Approved execution gateway: the only code path that runs SQL against Databricks.

For every request:
  1. credential gate: write-capable credentials are refused unless WBG_ALLOW_DEV_OWNER_CREDENTIALS=true, and then
     every result is stamped credential_mode=dev_owner_credentials, production_read_only=false;
  2. the SQL is validated again here (a validation id from an earlier call is never trusted);
  3. only the validator's normalised SQL runs, with bound parameters and a wall-clock timeout;
  4. the result is validated before it is returned. Failures return typed errors, never invented results.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp

from structured_agent.config import Settings
from structured_agent.contracts import (CheckResult, ErrorCode, ExecutionProvenance, ParameterValue, ResultColumn, ToolError,
                                        ValidationReport)
from structured_agent.execution.executor import CredentialCheck, QueryTimeout, SqlExecutor
from structured_agent.execution.result_validation import validate_result
from structured_agent.observability import log_failure
from structured_agent.policy import Principal
from structured_agent.registry.models import Metric, RegistryBundle
from structured_agent.sql_validation.structure import DIALECT
from structured_agent.sql_validation.validator import SqlValidator

SUM_OF_COLUMN = re.compile(r"sum\((\w+)\)")


@dataclass
class GatewayResult:
    status: str                                  # ok | rejected | error
    report: ValidationReport | None = None
    query_id: str | None = None
    columns: list[ResultColumn] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    truncated: bool = False
    metrics: list[Metric] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    result_checks: list[CheckResult] = field(default_factory=list)
    errors: list[ToolError] = field(default_factory=list)
    provenance: ExecutionProvenance | None = None
    execution_ms: int = 0


class ExecutionGateway:
    def __init__(self, settings: Settings, bundle: RegistryBundle, validator: SqlValidator, executor: SqlExecutor):
        self._settings = settings
        self._bundle = bundle
        self._validator = validator
        self._executor = executor
        self._credential: CredentialCheck | None = None

    # ------------------------------------------------------------------------------------------------- credential gate
    def credential_mode(self) -> tuple[str | None, ToolError | None]:
        if self._credential is None:
            self._credential = self._executor.check_credential()
        if not self._credential.write_capable:
            return "read_only_principal", None
        if self._settings.allow_dev_owner_credentials:
            return "dev_owner_credentials", None
        return None, ToolError.of(ErrorCode.CREDENTIAL_NOT_READ_ONLY,
                                  "The configured Databricks credential can write to the approved schemas "
                                  f"({self._credential.evidence}). Configure a read-only principal, or set "
                                  "WBG_ALLOW_DEV_OWNER_CREDENTIALS=true for controlled assessment testing only.")

    # ------------------------------------------------------------------------------------------------------- execute
    def execute(self, sql: str, parameters: dict[str, ParameterValue], principal: Principal,
                max_rows: int | None = None) -> GatewayResult:
        mode, error = self.credential_mode()
        if error:
            return GatewayResult(status="error", errors=[error])

        report = self._validator.validate(sql, parameters, principal, max_rows)
        if not report.approved:
            return GatewayResult(status="rejected", report=report, errors=report.errors)

        query_id = "qry_" + uuid.uuid4().hex[:16]
        started = time.perf_counter()
        try:
            raw = self._executor.execute(report.validated_sql, parameters, self._settings.limits.timeout_seconds)
        except QueryTimeout as timeout:
            return GatewayResult(status="error", report=report, query_id=query_id,
                                 errors=[ToolError.of(ErrorCode.EXECUTION_TIMEOUT, str(timeout))],
                                 execution_ms=round(1000 * (time.perf_counter() - started)))
        except Exception as failure:  # the engine's message may contain data: return only its type
            log_failure("databricks query failed", failure, query_id=query_id)
            return GatewayResult(status="error", report=report, query_id=query_id,
                                 errors=[ToolError.of(ErrorCode.EXECUTION_FAILED, f"Query failed in Databricks ({type(failure).__name__})")],
                                 execution_ms=round(1000 * (time.perf_counter() - started)))
        execution_ms = round(1000 * (time.perf_counter() - started))

        limit = self._limit_of(report.validated_sql)
        checks = validate_result(raw, report, limit)
        provenance = ExecutionProvenance(credential_mode=mode, production_read_only=mode == "read_only_principal",
                                         registry_build_id=self._bundle.build.build_id,
                                         executed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if not all(c.passed for c in checks):
            failed = [c.name for c in checks if not c.passed]
            return GatewayResult(status="error", report=report, query_id=query_id, result_checks=checks, provenance=provenance,
                                 errors=[ToolError.of(ErrorCode.RESULT_VALIDATION_FAILED, f"Result checks failed: {failed}; rows withheld")],
                                 execution_ms=execution_ms)

        units = {c.name.lower(): c for c in report.output_columns}
        columns = [ResultColumn(name=name, data_type=dtype, unit=units[name.lower()].unit,
                                source_columns=units[name.lower()].source_columns) for name, dtype in raw.columns]
        return GatewayResult(
            status="ok", report=report, query_id=query_id, columns=columns, rows=raw.rows,
            truncated=len(raw.rows) >= limit, metrics=self._metrics_used(report), limitations=self._limitations(report, mode),
            result_checks=checks, provenance=provenance, execution_ms=execution_ms)

    # ------------------------------------------------------------------------------------------------------- helpers
    @staticmethod
    def _limit_of(validated_sql: str) -> int:
        limit = sqlglot.parse_one(validated_sql, read=DIALECT).args["limit"].expression
        return int(limit.this) if isinstance(limit, exp.Literal) else 0

    def _metrics_used(self, report: ValidationReport) -> list[Metric]:
        """Registered metrics whose certified column, or summed measure column, feeds an output of this query."""
        sources = {s for c in report.output_columns for s in c.source_columns}
        used = []
        for metric in self._bundle.metrics:
            summed = SUM_OF_COLUMN.fullmatch(metric.expression)
            measure_column = f"{metric.object_fqn}.{summed.group(1)}" if summed else None
            if metric.certified_source in sources or measure_column in sources:
                used.append(metric)
        return used

    def _limitations(self, report: ValidationReport, mode: str) -> list[str]:
        notes = [n for fqn in report.objects for n in self._bundle.object(fqn).limitations]
        if mode == "dev_owner_credentials":
            notes.append("Executed with the owner (write-capable) Databricks credential in labelled development mode: "
                         "read-only access was enforced by the validator and gateway, NOT by database permissions.")
        return list(dict.fromkeys(notes))
